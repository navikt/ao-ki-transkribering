"""Bounded Nemotron speaker inference; state belongs to a recording, not the model."""

import copy
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000
logger = logging.getLogger(__name__)
MAX_WORD_GAP_S = 2.0
FRAMES_PER_SECOND = 100
# Short windows lose speaker identity without right context (4 speakers on a 3-voice clip at 15 s);
# 25 frames matched offline diarization. Windows of 20 s or more need none.
LOOKAHEAD_FRAMES = int(os.environ.get("NEMOTRON_DIARIZATION_LOOKAHEAD", "25"))
LOOKAHEAD_MIN_CHUNK_S = 8
LOOKAHEAD_MAX_CHUNK_S = 20
LOOKAHEAD_SECONDS_PER_FRAME = 0.08


class NemotronDiarizer:
    def __init__(self, model_path, device):
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor

        self.processor = AutoProcessor.from_pretrained(model_path)
        self.model = AutoModelForAudioFrameClassification.from_pretrained(model_path).to(device).eval()
        self.device = device
        self._inference_lock = threading.Lock()

    def infer(self, pcm, cache=None, lookahead=0):
        with self._inference_lock:
            started = time.monotonic()
            result = self._infer(pcm, cache, lookahead)
            logger.warning("diarization_inference seconds=%.3f audio_seconds=%.3f",
                           time.monotonic() - started, len(pcm) / SAMPLE_RATE)
            return result

    def _infer(self, pcm, cache=None, lookahead=0):
        """Return (turns, cache, scored_seconds); the last `lookahead` frames are attended but not scored."""
        import torch

        turns = []
        scored = 0.0
        # Limit feature/attention memory even for a three-hour uploaded recording.
        with torch.inference_mode():
            for start in range(0, len(pcm), 32 * SAMPLE_RATE):
                window = pcm[start:start + 32 * SAMPLE_RATE]
                last = start + len(window) >= len(pcm)
                inputs = self.processor(window, sampling_rate=SAMPLE_RATE, return_tensors="pt")
                inputs = inputs.to(self.device, dtype=self.model.dtype)
                output = self.model(**inputs, speaker_cache=cache,
                                    num_lookahead_frames=lookahead if last else 0)
                cache = output.speaker_cache
                frames = output.logits.shape[1]
                mask = inputs.attention_mask[:, :frames]
                scored = start / SAMPLE_RATE + frames / FRAMES_PER_SECOND
                for turn in self.processor.extract_speaker_dict(output.logits, mask)[0]:
                    turns.append({"start": start / SAMPLE_RATE + turn["Start"],
                                  "end": min(scored, start / SAMPLE_RATE + turn["End"]),
                                  "speaker": f"SPEAKER_{turn['Speaker']:02d}"})
        return turns, cache, min(scored, len(pcm) / SAMPLE_RATE)


def merge_to_dominant(turns, limit=2, totals=None, names=None):
    """Keep the speakers with most speech; give other turns to the nearest kept speaker's turn.

    `totals` (speech seconds per raw speaker over the whole recording) and `names` (a mapping that is
    updated in place) keep labels stable across chunks of one recording.
    """
    if totals is None:
        totals = {}
        for t in turns:
            totals[t["speaker"]] = totals.get(t["speaker"], 0) + t["end"] - t["start"]
    present = {t["speaker"] for t in turns}
    if len(totals) <= limit and not names:
        return turns
    names = {} if names is None else names
    first = {}
    for t in turns:
        first.setdefault(t["speaker"], t["start"])
    ranked = sorted(present, key=lambda s: totals.get(s, 0), reverse=True)
    newcomers = [s for s in ranked if s not in names][:max(0, limit - len(names))]
    for old in sorted(newcomers, key=first.get):
        used = set(names.values())
        wanted = old if old.startswith("SPEAKER_") and int(old[8:]) < limit and old not in used else None
        names[old] = wanted or next(f"SPEAKER_{i:02d}" for i in range(limit) if f"SPEAKER_{i:02d}" not in used)
    if len(present) <= limit and all(s in names for s in present):
        return [{**t, "speaker": names[t["speaker"]]} for t in turns]
    anchors = [t for t in turns if t["speaker"] in names]
    if not anchors:
        return turns
    merged = []
    for t in turns:
        if t["speaker"] in names:
            speaker = names[t["speaker"]]
        else:
            nearest = min(anchors, key=lambda a: max(a["start"] - t["end"], t["start"] - a["end"], 0))
            speaker = names[nearest["speaker"]]
        merged.append({**t, "speaker": speaker})
    return merged


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
            if max(nearest["start"] - end, start - nearest["end"], 0) <= MAX_WORD_GAP_S:
                speaker = nearest["speaker"]
        result.append({"id": len(result), "text": chunk["text"], "start": start,
                       "end": end, "speaker": speaker})
    return result


@dataclass
class Recording:
    cache: object = None
    end: float = 0
    scored_end: float = 0
    tail: object = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    turns: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    fingerprint: tuple | None = None
    result: dict | None = None
    touched: float = field(default_factory=time.monotonic)


class SpeechSessions:
    def __init__(self, ttl=300, capacity=8, clock=time.monotonic):
        self.records = {}
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.active = set()

    def purge(self):
        with self.lock:
            self._purge()

    def _purge(self):
        now = self.clock()
        self.records = {key: value for key, value in self.records.items()
                        if key in self.active or now - value.touched < self.ttl}

    def transcribe(self, pcm, asr, diarizer, session_id=None, chunk_start=0, speakers=2):
        with self.condition:
            while session_id and session_id in self.active:
                self.condition.wait()
            self._purge()
            previous = self.records.get(session_id)
            if session_id and previous is None and len(set(self.records) | self.active) >= self.capacity:
                raise OverflowError("Too many active recordings")
            fingerprint = (chunk_start, hashlib.sha256(pcm.tobytes()).digest())
            if previous and previous.fingerprint == fingerprint:
                previous.touched = self.clock()
                return previous.result
            if previous and chunk_start + len(pcm) / SAMPLE_RATE <= previous.end:
                raise ValueError("Recording chunk is out of order")
            # Model cache updates are mutable: commit only after both inference stages succeed.
            state = copy.deepcopy(previous) if previous else Recording()
            if session_id:
                self.active.add(session_id)
        try:
            seconds = len(pcm) / SAMPLE_RATE
            lookahead = LOOKAHEAD_FRAMES if LOOKAHEAD_MIN_CHUNK_S <= seconds < LOOKAHEAD_MAX_CHUNK_S else 0
            # Audio whose last frames were only look-ahead is kept and fed again with the next chunk.
            if previous and chunk_start <= state.end:
                fresh = pcm[min(len(pcm), round((state.end - chunk_start) * SAMPLE_RATE)):]
                feed, offset = np.concatenate([state.tail, fresh]), state.scored_end
            else:
                feed, offset = pcm, chunk_start
            if lookahead and len(feed) < (lookahead * LOOKAHEAD_SECONDS_PER_FRAME + 1) * SAMPLE_RATE:
                turns, scored = [], 0.0
            else:
                turns, state.cache, scored = diarizer.infer(feed, state.cache, lookahead)
            state.tail = feed[round(scored * SAMPLE_RATE):]
            state.scored_end = offset + scored
            state.turns.extend({**t, "start": t["start"] + offset, "end": t["end"] + offset} for t in turns)
            for t in turns:
                state.totals[t["speaker"]] = state.totals.get(t["speaker"], 0) + t["end"] - t["start"]
            result = asr.transkriber(pcm)
            relative_turns = [{**t, "start": t["start"] - chunk_start, "end": t["end"] - chunk_start}
                              for t in state.turns]
            result = {"text": result["text"],
                      "segments": label_words(result["chunks"], merge_to_dominant(relative_turns, speakers, state.totals, state.names)),
                      "duration": len(pcm) / SAMPLE_RATE, "language": "norwegian",
                      "warnings": result.get("warnings", [])}
            if session_id:
                if previous is None and chunk_start > 0:
                    result["warnings"] = [*result["warnings"], "Talernumre er startet pa nytt etter avbrudd."]
                state.end = chunk_start + len(pcm) / SAMPLE_RATE
                state.turns = [t for t in state.turns if t["end"] >= state.end - 40]
                state.fingerprint, state.result, state.touched = fingerprint, result, self.clock()
                with self.condition:
                    self.records[session_id] = state
            return result
        finally:
            if session_id:
                with self.condition:
                    self.active.discard(session_id)
                    self.condition.notify_all()
