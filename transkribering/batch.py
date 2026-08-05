import subprocess
import tempfile
from pathlib import Path

import numpy as np

from kontrakter.transkripsjon import Segment, TranskripsjonSvar
from services.transkripsjon_backend import StatusCallback
from transkribering.hallusinasjon import trim_null_ord, trim_etter_stille, fjern_hallusinasjon
from transkribering.diarisering import diariser, tilordne_taler

# Estimert prosesseringstid som andel av lydens varighet (kalibrert for MPS).
_MODELL_FAKTOR = {"tiny": 0.08, "base": 0.12, "small": 0.20, "medium": 0.33, "large": 0.60}
# Multiplikator per hardware relativt til MPS-baseline.
_ENHET_MULTIPLIKATOR = {"cuda": 0.25, "mps": 1.0, "cpu": 3.5}
_DIARISER_OVERHEAD_S = 8


def estimert_total_s(modell_id: str, lyd_s: float, enhet: str = "mps") -> float:
    faktor  = next((v for k, v in _MODELL_FAKTOR.items() if k in modell_id.lower()), 0.33)
    hw_mult = _ENHET_MULTIPLIKATOR.get(enhet, 1.0)
    return max(lyd_s * faktor * hw_mult + _DIARISER_OVERHEAD_S, 5.0)


class LokalBatchTranskriberer:
    """Local nb-whisper batch model with speaker diarization."""

    def __init__(self, modell_id: str):
        import logging

        logging.getLogger("transformers").setLevel(logging.ERROR)

        from transformers import pipeline

        self.modell_id = modell_id
        self.enhet = velg_enhet()
        print(f"[arbeider] Laster modell: {modell_id}  (enhet: {self.enhet}) …", flush=True)
        self._asr = pipeline(
            "automatic-speech-recognition",
            model=modell_id,
            device=self.enhet,
            ignore_warning=True,
        )
        print("[arbeider] Modell klar.", flush=True)

    def transkriber(
        self,
        lydfil: Path,
        *,
        n_talere: int = 0,
        status_callback: StatusCallback | None = None,
    ) -> TranskripsjonSvar:
        wav_sti = None
        advarsler: list[str] = []
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _tmp:
                wav_sti = Path(_tmp.name)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(lydfil),
                 "-ar", "16000", "-ac", "1", str(wav_sti)],
                check=True, capture_output=True,
            )

            pcm = np.frombuffer(
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(wav_sti),
                     "-ar", "16000", "-ac", "1", "-f", "f32le", "pipe:1"],
                    check=True, capture_output=True,
                ).stdout,
                dtype="<f4",
            ).copy()
            lyd_varighet_s = len(pcm) / 16000

            if status_callback is not None:
                status_callback({"fase": "transkriberer", "lyd_varighet_s": lyd_varighet_s})

            resultat = self._asr(
                str(wav_sti),
                chunk_length_s=28,
                return_timestamps="word",
                generate_kwargs={"num_beams": 1, "task": "transcribe", "language": "no"},
            )

            tekst = resultat["text"].strip()
            ord_liste = resultat.get("chunks", [])

            # Fiks None-tidsstempler
            siste_slutt = next(
                (c["timestamp"][1] for c in reversed(ord_liste) if c["timestamp"][1] is not None),
                0.0,
            )
            for c in ord_liste:
                ts0 = c["timestamp"][0] or 0.0
                ts1 = c["timestamp"][1] if c["timestamp"][1] is not None else siste_slutt
                c["timestamp"] = (ts0, ts1)

            ord_liste = trim_null_ord(ord_liste)
            ord_liste = trim_etter_stille(ord_liste, pcm)
            if not ord_liste:
                tekst = ""

            if status_callback is not None:
                status_callback({"fase": "diariserer"})
            try:
                diari_segs, _ = diariser(pcm, n_talere=n_talere)
            except Exception as diar_exc:
                print(f"[arbeider] Diarisering feilet: {diar_exc}", flush=True)
                advarsler.append(f"Diarisering feilet: {diar_exc}")
                diari_segs = []

            segmenter = []
            if diari_segs:
                gjeldende_taler = "SPEAKER_00"
                gjeldende_ord: list[str] = []
                gjeldende_start = 0.0
                gjeldende_slutt = 0.0

                for c in ord_liste:
                    ts0, ts1 = c["timestamp"]
                    ord_tekst = c["text"]
                    taler = tilordne_taler(ts0, ts1, diari_segs, gjeldende_taler)

                    if taler != gjeldende_taler and gjeldende_ord:
                        t = fjern_hallusinasjon("".join(gjeldende_ord).strip())
                        if t:
                            segmenter.append({
                                "start": round(gjeldende_start, 1),
                                "slutt": round(ts0, 1),
                                "tekst": t,
                                "taler": gjeldende_taler,
                            })
                        gjeldende_ord = []
                        gjeldende_start = ts0

                    if not gjeldende_ord:
                        gjeldende_start = ts0
                    gjeldende_taler = taler
                    gjeldende_ord.append(ord_tekst)
                    gjeldende_slutt = ts1

                if gjeldende_ord:
                    t = fjern_hallusinasjon("".join(gjeldende_ord).strip())
                    if t:
                        segmenter.append({
                            "start": round(gjeldende_start, 1),
                            "slutt": round(gjeldende_slutt, 1),
                            "tekst": t,
                            "taler": gjeldende_taler,
                        })

                # Slå sammen svært korte stubb-segmenter (< 3 ord) med naboer
                MIN_ORD = 3
                i = 0
                while i < len(segmenter):
                    antall_ord = len(segmenter[i]["tekst"].split())
                    if antall_ord < MIN_ORD:
                        gjeldende = segmenter[i]
                        har_forrige = i > 0
                        har_neste   = i + 1 < len(segmenter)
                        same_forrige = har_forrige and segmenter[i-1]["taler"] == gjeldende["taler"]
                        same_neste   = har_neste   and segmenter[i+1]["taler"] == gjeldende["taler"]
                        if same_forrige and not same_neste:
                            slå_inn_forrige = True
                        elif same_neste and not same_forrige:
                            slå_inn_forrige = False
                        elif har_forrige and har_neste:
                            tid_til_forrige = gjeldende["start"] - segmenter[i-1]["slutt"]
                            tid_til_neste   = segmenter[i+1]["start"] - gjeldende["slutt"]
                            slå_inn_forrige = tid_til_forrige <= tid_til_neste
                        else:
                            slå_inn_forrige = har_forrige
                        if slå_inn_forrige:
                            prev = segmenter[i - 1]
                            prev["tekst"] = prev["tekst"].rstrip() + " " + gjeldende["tekst"]
                            prev["slutt"] = gjeldende["slutt"]
                            segmenter.pop(i)
                        elif har_neste:
                            nxt = segmenter[i + 1]
                            nxt["tekst"] = gjeldende["tekst"] + " " + nxt["tekst"]
                            nxt["start"] = gjeldende["start"]
                            segmenter.pop(i)
                        else:
                            i += 1
                    else:
                        i += 1

                # Slå sammen påfølgende segmenter med samme taler
                i = 0
                while i + 1 < len(segmenter):
                    if segmenter[i]["taler"] == segmenter[i+1]["taler"]:
                        segmenter[i]["tekst"] = segmenter[i]["tekst"].rstrip() + " " + segmenter[i+1]["tekst"]
                        segmenter[i]["slutt"] = segmenter[i+1]["slutt"]
                        segmenter.pop(i + 1)
                    else:
                        i += 1
            else:
                # Fallback uten diarisering
                gjeldende_ord_grp: list[str] = []
                grp_start = 0.0
                for c in ord_liste:
                    if not gjeldende_ord_grp:
                        grp_start = c["timestamp"][0]
                    gjeldende_ord_grp.append(c["text"])
                if gjeldende_ord_grp:
                    t = fjern_hallusinasjon(" ".join(gjeldende_ord_grp).strip())
                    if t:
                        segmenter.append({
                            "start": round(grp_start, 1),
                            "slutt": round(siste_slutt, 1),
                            "tekst": t,
                            "taler": "SPEAKER_00",
                        })

            return TranskripsjonSvar(
                tekst=tekst,
                segmenter=[Segment.model_validate(s) for s in segmenter],
                advarsler=advarsler,
            )
        finally:
            if wav_sti:
                wav_sti.unlink(missing_ok=True)


def velg_enhet() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
