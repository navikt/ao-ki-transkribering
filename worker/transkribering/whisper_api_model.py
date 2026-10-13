"""Whisper word timestamps for the shared speech/diarization endpoint."""

import os

import numpy as np


class WhisperTranskriberer:
    def __init__(self):
        import torch
        from transformers import pipeline

        self.enhet = "cuda" if torch.cuda.is_available() else "cpu"
        self.pipeline = pipeline(
            "automatic-speech-recognition",
            model=os.getenv("WHISPER_MODELL", "/models/nb-whisper-large"),
            device=self.enhet,
            dtype=torch.float16 if self.enhet == "cuda" else torch.float32,
            model_kwargs={"attn_implementation": "eager"},
        )

    def transkriber(self, pcm):
        if not len(pcm) or not np.any(pcm):
            return {"text": "", "chunks": []}
        result = self.pipeline(
            pcm, chunk_length_s=30, stride_length_s=(5, 2), return_timestamps="word",
            generate_kwargs={"language": "no", "task": "transcribe", "num_beams": 5},
        )
        # Keep every recognized word, even when Whisper omits its final timestamp.
        previous = 0.0
        chunks = []
        duration = len(pcm) / 16000
        for word in result.get("chunks", []):
            start, end = word["timestamp"]
            start = previous if start is None else min(max(start, 0), duration)
            end = duration if end is None else min(max(end, start), duration)
            chunks.append({"text": word["text"], "timestamp": (start, end)})
            previous = end
        if not chunks and result["text"].strip():
            chunks = [{"text": result["text"], "timestamp": (0, duration)}]
        return {"text": result["text"], "chunks": chunks}
