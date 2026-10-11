"""Experimental local Nemotron ASR, with bounded audio windows and emission timestamps."""

import os
import re
import threading

import numpy as np

from shared.core.settings import NEMOTRON_MODELL
from worker.transkribering.konstanter import SAMPLE_RATE

_CORE_S = 24
_CONTEXT_S = 3
_modell = None
_modell_lock = threading.Lock()
TIMESTAMP_WARNING = "Nemotron-tidsstempler er omtrentlige; talerskille er ikke validert."


def tokens_til_ord(tokens: list[dict], varighet_s: float) -> list[dict]:
    """Keep decoder text intact while grouping timestamped subword emissions into words."""
    tekst = "".join(t["token"] for t in tokens)
    spans = []
    offset = 0
    for token in tokens:
        end = offset + len(token["token"])
        spans.append((offset, end, token))
        offset = end
    words = []
    previous = 0
    for match in re.finditer(r"\S+", tekst):
        overlapping = [t for start, end, t in spans if start < match.end() and end > match.start()]
        start = min(max(float(t["start"]), 0.0) for t in overlapping)
        end = max(float(t["end"]) for t in overlapping)
        words.append({"text": tekst[previous:match.end()],
                      "timestamp": (min(start, varighet_s), min(max(start, end), varighet_s))})
        previous = match.end()
    return words


class NemotronTranskriberer:
    def __init__(self, modell_id: str = NEMOTRON_MODELL):
        import torch
        try:
            from transformers import AutoModelForRNNT, AutoProcessor
        except ImportError as exc:
            raise RuntimeError("Nemotron requires the optional Transformers runtime; use scripts/run-local-nemotron.sh") from exc

        self.modell_id = modell_id
        self.enhet = os.getenv("NEMOTRON_ENHET", "cuda" if torch.cuda.is_available() else "cpu")
        if self.enhet not in {"cpu", "cuda"}:
            raise ValueError("NEMOTRON_ENHET must be cpu or cuda; MPS is not validated")
        if self.enhet == "cpu":
            threads = int(os.getenv("NEMOTRON_CPU_THREADS", "4"))
            if threads < 1:
                raise ValueError("NEMOTRON_CPU_THREADS must be positive")
            torch.set_num_threads(threads)
        self._lock = threading.Lock()
        print(f"[nemotron] Loading {modell_id} ({self.enhet})", flush=True)
        self._processor = AutoProcessor.from_pretrained(modell_id)
        self._model = AutoModelForRNNT.from_pretrained(modell_id).to(self.enhet).eval()
        print("[nemotron] Ready", flush=True)

    def transkriber(self, pcm: np.ndarray) -> dict:
        import torch

        if pcm.ndim != 1 or not np.isfinite(pcm).all():
            raise ValueError("Nemotron expects finite mono float32 PCM at 16 kHz")
        if not len(pcm):
            return {"text": "", "chunks": []}
        words = []
        core = _CORE_S * SAMPLE_RATE
        context = _CONTEXT_S * SAMPLE_RATE
        # generate() mutates decoder state; shared instances must serialize inference.
        with self._lock, torch.inference_mode():
            for start in range(0, len(pcm), core):
                end = min(start + core, len(pcm))
                window_start = max(0, start - context)
                window_end = min(len(pcm), end + context)
                window = pcm[window_start:window_end]
                inputs = self._processor(window, sampling_rate=SAMPLE_RATE, language="nb-NO", return_tensors="pt")
                inputs = inputs.to(self.enhet, dtype=self._model.dtype)
                output = self._model.generate(**inputs, return_dict_in_generate=True)
                if output.durations is None:
                    raise RuntimeError("Nemotron runtime returned no emission timestamps; upgrade the optional runtime")
                _, timestamps = self._processor.decode(output.sequences, durations=output.durations,
                                                       skip_special_tokens=True)
                chunk_words = tokens_til_ord(timestamps[0], len(window) / SAMPLE_RATE)
                for word in chunk_words:
                    ts0, ts1 = word["timestamp"]
                    ts0 += window_start / SAMPLE_RATE
                    ts1 += window_start / SAMPLE_RATE
                    midpoint = (ts0 + ts1) / 2
                    if start / SAMPLE_RATE <= midpoint and (
                        midpoint < end / SAMPLE_RATE or end == len(pcm) and midpoint == end / SAMPLE_RATE
                    ):
                        words.append({"text": word["text"].lstrip() if not words else " " + word["text"].lstrip(),
                                      "timestamp": (ts0, ts1)})
        return {"text": "".join(w["text"] for w in words).strip(), "chunks": words}


def hent_nemotron_modell() -> NemotronTranskriberer:
    global _modell
    if _modell is None:
        with _modell_lock:
            if _modell is None:
                _modell = NemotronTranskriberer()
    return _modell
