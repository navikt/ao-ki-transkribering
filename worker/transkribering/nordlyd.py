"""Adapter for the pinned Nordlyd publisher helper in an isolated runtime."""

import importlib.util
import os
import tempfile
import threading
from pathlib import Path

SAMPLE_RATE = 16000
MAX_AUDIO_SECONDS = 180
MODEL_NAME = "nordlyd-qwen06b-preview"


class NordlydTranskriberer:
    inference_concurrency = 1
    max_audio_seconds = MAX_AUDIO_SECONDS

    def __init__(self):
        model_path = Path(os.environ["NORDLYD_MODELL"]).resolve(strict=True)
        aligner_path = Path(os.environ["NORDLYD_ALIGNER_MODELL"]).resolve(strict=True)
        helper_path = model_path / "inference.py"
        spec = importlib.util.spec_from_file_location("nordlyd_publisher_inference", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        self.enhet = "cuda:0"
        self._model = helper.NordlydASR(model=str(model_path), device=self.enhet)
        self._model.load_aligner(model=str(aligner_path), device=self.enhet, revision=None)
        self._lock = threading.Lock()

    def transkriber(self, pcm):
        import soundfile

        duration = len(pcm) / SAMPLE_RATE
        if duration > MAX_AUDIO_SECONDS:
            raise ValueError("Nordlyd word alignment supports at most 180 seconds")
        if not pcm.any():
            return {"text": "", "chunks": []}
        with tempfile.TemporaryDirectory(prefix="nordlyd-") as directory:
            path = Path(directory) / "audio.wav"
            soundfile.write(path, pcm, SAMPLE_RATE, subtype="FLOAT")
            with self._lock:
                result = self._model.transcribe(str(path), target_lang="nob", style="verbatim", timestamps=True)

        transcript = result["text"].strip()
        chunks = []
        for word in result["timestamps"]:
            start, end = float(word["start"]), min(float(word["end"]), duration)
            if 0 <= start <= end:
                chunks.append({"text": word["text"], "timestamp": (start, end)})
        warnings = []
        if result["truncated"]:
            warnings.append("Nordlyd nådde grensen for lengden på transkripsjonen.")
        if transcript and not chunks:
            warnings.append("Nordlyd returnerte tekst uten tidsstempler for ordene.")
        return {"text": transcript, "chunks": chunks, "warnings": warnings}
