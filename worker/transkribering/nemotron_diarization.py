"""Bounded Nemotron speaker inference; state belongs to a recording, not the model."""

import copy
import hashlib
import threading
import time
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000


class NemotronDiarizer:
    def __init__(self, model_path, device):
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor

        self.processor = AutoProcessor.from_pretrained(model_path)
        self.model = AutoModelForAudioFrameClassification.from_pretrained(model_path).to(device).eval()
        self.device = device

    def infer(self, pcm, cache=None):
        import torch

        turns = []
        # Limit feature/attention memory even for a two-hour uploaded recording.
        with torch.inference_mode():
            for start in range(0, len(pcm), 24 * SAMPLE_RATE):
                window = pcm[start:start + 24 * SAMPLE_RATE]
                inputs = self.processor(window, sampling_rate=SAMPLE_RATE, return_tensors="pt")
                inputs = inputs.to(self.device, dtype=self.model.dtype)
                output = self.model(**inputs, speaker_cache=cache, num_lookahead_frames=0)
                cache = output.speaker_cache
                for turn in self.processor.extract_speaker_dict(output.logits, inputs.attention_mask)[0]:
                    turns.append({"start": start / SAMPLE_RATE + turn["Start"],
                                  "end": min((start + len(window)) / SAMPLE_RATE,
                                             start / SAMPLE_RATE + turn["End"]),
                                  "speaker": f"SPEAKER_{turn['Speaker']:02d}"})
        return turns, cache


def label_words(chunks, turns):
    result = []
    for chunk in chunks:
        start, end = chunk["timestamp"]
        overlaps = [(max(0, min(end, t["end"]) - max(start, t["start"])), t) for t in turns]
        best = max(overlaps, key=lambda pair: pair[0], default=(0, None))
        speaker = best[1]["speaker"] if best[0] > 0 else "SPEAKER_UNKNOWN"
        # RNNT emission timestamps can follow the acoustic word; allow a small gap.
        if speaker == "SPEAKER_UNKNOWN" and turns:
            nearest = min(turns, key=lambda t: max(t["start"] - end, start - t["end"], 0))
            if max(nearest["start"] - end, start - nearest["end"], 0) <= 0.4:
                speaker = nearest["speaker"]
        result.append({"id": len(result), "text": chunk["text"], "start": start,
                       "end": end, "speaker": speaker})
    return result


@dataclass
class Recording:
    cache: object = None
    end: float = 0
    turns: list = field(default_factory=list)
    fingerprint: tuple | None = None
    result: dict | None = None
    touched: float = field(default_factory=time.monotonic)


class SpeechSessions:
    def __init__(self, ttl=300, capacity=8, clock=time.monotonic):
        self.records = {}
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.lock = threading.Lock()

    def purge(self):
        with self.lock:
            self._purge()

    def _purge(self):
        now = self.clock()
        self.records = {key: value for key, value in self.records.items() if now - value.touched < self.ttl}

    def transcribe(self, pcm, asr, diarizer, session_id=None, chunk_start=0):
        with self.lock:
            self._purge()
            previous = self.records.get(session_id)
            if session_id and previous is None and len(self.records) >= self.capacity:
                raise OverflowError("Too many active recordings")
            fingerprint = (chunk_start, hashlib.sha256(pcm.tobytes()).digest())
            if previous and previous.fingerprint == fingerprint:
                previous.touched = self.clock()
                return previous.result
            if previous and chunk_start + len(pcm) / SAMPLE_RATE <= previous.end:
                raise ValueError("Recording chunk is out of order")
            # Model cache updates are mutable: commit only after both inference stages succeed.
            state = copy.deepcopy(previous) if previous else Recording()
            overlap = max(0, round((state.end - chunk_start) * SAMPLE_RATE)) if previous else 0
            turns, state.cache = diarizer.infer(pcm[overlap:], state.cache)
            offset = chunk_start + overlap / SAMPLE_RATE
            state.turns.extend({**t, "start": t["start"] + offset, "end": t["end"] + offset} for t in turns)
            result = asr.transkriber(pcm)
            relative_turns = [{**t, "start": t["start"] - chunk_start, "end": t["end"] - chunk_start}
                              for t in state.turns]
            result = {"text": result["text"], "segments": label_words(result["chunks"], relative_turns),
                      "duration": len(pcm) / SAMPLE_RATE, "language": "norwegian",
                      "warnings": result.get("warnings", [])}
            if session_id:
                if previous is None and chunk_start > 0:
                    result["warnings"] = [*result["warnings"], "Talernumre er startet pa nytt etter avbrudd."]
                state.end = chunk_start + len(pcm) / SAMPLE_RATE
                state.turns = [t for t in state.turns if t["end"] >= state.end - 40]
                state.fingerprint, state.result, state.touched = fingerprint, result, self.clock()
                self.records[session_id] = state
            return result
