import os
import tempfile
import threading
from pathlib import Path

import numpy as np

from transkribering.konstanter import (
    SAMPLE_RATE,
    FRAME_SAMPLES,
    ENERGI_TERSKEL,
    STILLHET_TERSKEL_S,
    MAKS_BUFFER_S,
    MIN_TALE_S,
)
from transkribering.hallusinasjon import er_hallusinasjon, trim_null_ord_fw
from transkribering.diarisering import diariser, tilordne_taler

_CT2_MODELL_STI = os.getenv("WHISPER_SANNTID_MODELL", "modeller/nb-whisper-medium")

_fw_modell = None
_fw_lock = threading.Lock()


def hent_fw_modell():
    """Laster faster-whisper-modellen én gang (thread-safe lazy init)."""
    global _fw_modell
    if _fw_modell is None:
        with _fw_lock:
            if _fw_modell is None:
                from faster_whisper import WhisperModel
                sti = _CT2_MODELL_STI
                if not Path(sti).exists():
                    raise FileNotFoundError(
                        f"Sanntidsmodellen '{sti}' finnes ikke. "
                        "Kjør konverter_modeller.py for å opprette CTranslate2-modellen, "
                        "eller sett WHISPER_SANNTID_MODELL til en gyldig sti."
                    )
                print(f"[sanntid] Laster {sti} …", flush=True)
                _fw_modell = WhisperModel(sti, device="auto", compute_type="default")
                print("[sanntid] Klar.", flush=True)
    return _fw_modell


def transkriber_pcm(
    pcm: np.ndarray,
    prototyper: "np.ndarray | None" = None,
) -> "tuple[dict | None, np.ndarray | None]":
    """
    Transkriberer en float32 numpy-array (16 kHz, mono).
    Kjøres via asyncio.to_thread – CTranslate2 slipper GIL.
    Returnerer (resultat, oppdaterte_prototyper).
    """
    if len(pcm) < SAMPLE_RATE * MIN_TALE_S:
        return None, prototyper

    modell = hent_fw_modell()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _tmp:
        wav_sti = Path(_tmp.name)
    try:
        import soundfile as sf
        sf.write(str(wav_sti), pcm, SAMPLE_RATE, subtype="FLOAT")

        tekst_deler = []
        segmenter_liste = []
        segments, _ = modell.transcribe(
            str(wav_sti),
            language="no",
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
        )
        for seg in segments:
            t = seg.text.strip()
            if not t:
                continue

            ord_liste_fw = [
                {"timestamp": (w.start, w.end), "text": w.word}
                for w in (seg.words or [])
            ]
            if ord_liste_fw:
                ord_liste_fw = trim_null_ord_fw(ord_liste_fw)
                if not ord_liste_fw:
                    print(f"[sanntid] Droppet segment (bare null-varighet ord): '{t}'", flush=True)
                    continue
                t = " ".join(w["text"].strip() for w in ord_liste_fw).strip()

            # Ord-rate-sjekk: > 8 ord/sek = hallusinasjon (normal tale: 2–4 ord/sek)
            varighet = seg.end - seg.start
            antall_ord = len(t.split())
            if varighet > 0.1 and (antall_ord / varighet) > 8:
                print(f"[sanntid] Droppet segment med urealistisk ord-rate "
                      f"({antall_ord/varighet:.1f} ord/sek): '{t}'", flush=True)
                continue

            if er_hallusinasjon(t):
                continue

            tekst_deler.append(t)
            segmenter_liste.append(
                {"start": round(seg.start, 1), "slutt": round(seg.end, 1), "tekst": t}
            )

        if not tekst_deler:
            return None, prototyper

        try:
            diari_segs, ny_prototyper = diariser(pcm, prototyper=prototyper)
            forrige = "SPEAKER_00"
            for seg in segmenter_liste:
                taler = tilordne_taler(seg["start"], seg["slutt"], diari_segs, forrige)
                seg["taler"] = taler
                forrige = taler
        except Exception:
            for seg in segmenter_liste:
                seg["taler"] = "SPEAKER_00"
            ny_prototyper = prototyper

        tekst = " ".join(tekst_deler)
        return {"tekst": tekst, "segmenter": segmenter_liste}, ny_prototyper

    except Exception as exc:
        print(f"[sanntid] Feil: {exc}", flush=True)
        return None, prototyper
    finally:
        wav_sti.unlink(missing_ok=True)


class VadBuffer:
    """
    Energibasert VAD-buffer. Samler PCM-frames og avgjør når det er trygt
    å sende til Whisper (ved naturlig pause eller maks bufferlengde).
    """
    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._total_samples: int = 0
        self._tale_samples: int = 0
        self._stille_samples: int = 0
        self._harTale: bool = False

    def legg_til(self, samples: np.ndarray) -> "np.ndarray | None":
        """Legg til samples. Returner buffer for transkribering ved VAD-flush, ellers None."""
        for start in range(0, len(samples), FRAME_SAMPLES):
            frame = samples[start:start + FRAME_SAMPLES]
            if len(frame) == 0:
                continue

            rms = float(np.sqrt(np.mean(frame ** 2)))
            er_tale = rms > ENERGI_TERSKEL

            self._frames.append(frame)
            self._total_samples += len(frame)

            if er_tale:
                self._harTale = True
                self._tale_samples += len(frame)
                self._stille_samples = 0
            else:
                self._stille_samples += len(frame)

            stille_s = self._stille_samples / SAMPLE_RATE
            total_s  = self._total_samples  / SAMPLE_RATE

            if self._harTale and (
                stille_s >= STILLHET_TERSKEL_S or total_s >= MAKS_BUFFER_S
            ):
                return self._flush()

        return None

    def flush_alt(self) -> "np.ndarray | None":
        """Tøm buffer ved stopp-kommando."""
        if self._harTale and self._tale_samples > 0:
            return self._flush()
        self._reset()
        return None

    def _flush(self) -> np.ndarray:
        data = np.concatenate(self._frames)
        self._reset()
        return data

    def _reset(self):
        self._frames = []
        self._total_samples = 0
        self._tale_samples = 0
        self._stille_samples = 0
        self._harTale = False
