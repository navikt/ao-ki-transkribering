import os
import tempfile
import threading
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from shared.core.settings import LOKAL_ASR_BACKEND

from worker.transkribering.konstanter import (
    SAMPLE_RATE,
    FRAME_SAMPLES,
    ENERGI_TERSKEL,
    STILLHET_TERSKEL_S,
    MAKS_BUFFER_S,
    OVERLAPP_S,
    MIN_TALE_S,
)
from worker.transkribering.hallusinasjon import er_hallusinasjon, trim_null_ord_fw
from worker.transkribering.diarisering import diariser, tilordne_taler
from worker.transkribering.nav_ordliste import normaliser_nav_ord

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
                from faster_whisper.feature_extractor import FeatureExtractor
                # Older local conversions omitted preprocessor_config.json (Large v3 needs 128 bins).
                if _fw_modell.feature_extractor.mel_filters.shape[0] != _fw_modell.model.n_mels:
                    _fw_modell.feat_kwargs["feature_size"] = _fw_modell.model.n_mels
                    _fw_modell.feature_extractor = FeatureExtractor(**_fw_modell.feat_kwargs)
                print("[sanntid] Klar.", flush=True)
    return _fw_modell


def hent_sanntidsmodell():
    if LOKAL_ASR_BACKEND == "nemotron":
        from worker.transkribering.nemotron import hent_nemotron_modell

        return hent_nemotron_modell()
    return hent_fw_modell()


def _transkriber_nemotron(pcm, prototyper):
    resultat = hent_sanntidsmodell().transkriber(pcm)
    if not resultat["chunks"]:
        return None, prototyper
    try:
        diari_segs, ny_prototyper = diariser(pcm, prototyper=prototyper)
    except Exception:
        diari_segs, ny_prototyper = [], prototyper
    segmenter = []
    for word in resultat["chunks"]:
        start, end = word["timestamp"]
        previous = segmenter[-1]["taler"] if segmenter else "SPEAKER_00"
        speaker = tilordne_taler(start, end, diari_segs, previous)
        if segmenter and segmenter[-1]["taler"] == speaker:
            segmenter[-1]["tekst"] += word["text"]
            segmenter[-1]["slutt"] = end
        else:
            segmenter.append({"start": start, "slutt": end, "tekst": word["text"], "taler": speaker})
    for segment in segmenter:
        segment["tekst"] = normaliser_nav_ord(segment["tekst"].strip())
        segment["start"] = round(segment["start"], 2)
        segment["slutt"] = round(segment["slutt"], 2)
    return {"tekst": " ".join(s["tekst"] for s in segmenter), "segmenter": segmenter}, ny_prototyper


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

    if LOKAL_ASR_BACKEND == "nemotron":
        return _transkriber_nemotron(pcm, prototyper)

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

            t = normaliser_nav_ord(t)
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

    finally:
        wav_sti.unlink(missing_ok=True)


@dataclass
class LydChunk:
    pcm: np.ndarray
    start_s: float
    overlapp_s: float = 0.0


class SegmentSamler:
    """Remove matching boundary words only when adjacent chunks share audio."""

    def __init__(self):
        self._siste_ord: list[str] = []
        self._siste_slutt_s = 0.0

    def legg_til(self, resultat: dict | None, chunk: LydChunk) -> dict | None:
        forrige_slutt = self._siste_slutt_s
        self._siste_slutt_s = chunk.start_s + len(chunk.pcm) / SAMPLE_RATE
        if not resultat or not resultat.get("tekst", "").strip():
            self._siste_ord = []
            return None
        segmenter = [dict(seg) for seg in resultat.get("segmenter", [])]
        if not segmenter:
            segmenter = [{"start": 0.0, "slutt": len(chunk.pcm) / SAMPLE_RATE,
                          "tekst": resultat["tekst"], "taler": "SPEAKER_00"}]
        tekst = " ".join(seg["tekst"] for seg in segmenter)
        ord_liste = [word.casefold().strip('.,:;!?()"') for word in tekst.split()]
        fjern = 0
        if (chunk.overlapp_s and segmenter[0].get("start", 0.0) < chunk.overlapp_s
                and abs(forrige_slutt - chunk.start_s - chunk.overlapp_s) < 0.02):
            maks_ord = min(len(self._siste_ord), len(ord_liste), int(chunk.overlapp_s * 8))
            for antall in range(maks_ord, 1, -1):
                if self._siste_ord[-antall:] == ord_liste[:antall]:
                    fjern = antall
                    break
        nye_segmenter = []
        for seg in segmenter:
            ord_spenn = list(re.finditer(r"\S+", seg["tekst"]))
            kutt = min(fjern, len(ord_spenn))
            fjern -= kutt
            if kutt:
                seg["tekst"] = seg["tekst"][ord_spenn[kutt - 1].end():].lstrip()
                seg["start"] = max(seg.get("start", 0.0), chunk.overlapp_s)
            if not seg["tekst"].strip():
                continue
            seg["start"] = round(chunk.start_s + seg.get("start", 0.0), 2)
            seg["slutt"] = round(chunk.start_s + seg.get("slutt", len(chunk.pcm) / SAMPLE_RATE), 2)
            nye_segmenter.append(seg)
        tekst = " ".join(seg["tekst"] for seg in nye_segmenter).strip()
        if tekst:
            self._siste_ord = (self._siste_ord + [
                word.casefold().strip('.,:;!?()"') for word in tekst.split()
            ])[-int(OVERLAPP_S * 8):]
        return {"tekst": tekst, "segmenter": nye_segmenter} if tekst else None


class VadBuffer:
    """
    Energibasert VAD-buffer. Samler PCM-frames og avgjør når det er trygt
    å sende til Whisper (ved naturlig pause eller maks bufferlengde).
    """
    def __init__(self, *, preserve_audio: bool = False):
        self._preserve_audio = preserve_audio
        self._frames: list[np.ndarray] = []
        self._total_samples: int = 0
        self._tale_samples: int = 0
        self._stille_samples: int = 0
        self._harTale: bool = False
        self._mottatt_samples = 0
        self._start_sample = 0
        self._overlapp_samples = 0

    def legg_til(self, samples: np.ndarray) -> list[LydChunk]:
        """Consume the entire packet, returning every completed chunk."""
        chunks = []
        for start in range(0, len(samples), FRAME_SAMPLES):
            frame = samples[start:start + FRAME_SAMPLES]
            if len(frame) == 0:
                continue

            rms = float(np.sqrt(np.mean(frame ** 2)))
            er_tale = rms > ENERGI_TERSKEL

            self._frames.append(frame)
            self._total_samples += len(frame)
            self._mottatt_samples += len(frame)

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
                chunks.append(self._flush(behold_overlapp=stille_s < STILLHET_TERSKEL_S))
            elif not self._harTale and total_s >= MAKS_BUFFER_S:
                if self._preserve_audio:
                    chunks.append(self._flush(behold_overlapp=True))
                else:
                    self._reset()

        return chunks

    def flush_alt(self) -> LydChunk | None:
        """Tøm buffer ved stopp-kommando."""
        if (self._harTale and self._tale_samples > 0) or (
            self._preserve_audio and self._total_samples > self._overlapp_samples
        ):
            return self._flush()
        self._reset()
        return None

    def _flush(self, behold_overlapp: bool = False) -> LydChunk:
        data = np.concatenate(self._frames)
        chunk = LydChunk(data, self._start_sample / SAMPLE_RATE, self._overlapp_samples / SAMPLE_RATE)
        self._reset()
        if behold_overlapp and OVERLAPP_S > 0:
            self._overlapp_samples = min(int(OVERLAPP_S * SAMPLE_RATE), len(data))
            self._frames = [data[-self._overlapp_samples:].copy()]
            self._total_samples = self._overlapp_samples
            self._start_sample = self._mottatt_samples - self._overlapp_samples
        return chunk

    def _reset(self):
        self._frames = []
        self._total_samples = 0
        self._tale_samples = 0
        self._stille_samples = 0
        self._harTale = False
        self._overlapp_samples = 0
        self._start_sample = self._mottatt_samples
