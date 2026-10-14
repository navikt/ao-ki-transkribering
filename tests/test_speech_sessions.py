from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import pytest

from worker.transkribering.nemotron_diarization import SpeechSessions, label_words


def engines():
    calls = []

    def infer(pcm, cache):
        calls.append((len(pcm), cache))
        return [{"start": 0, "end": len(pcm) / 16000, "speaker": "SPEAKER_01"}], (cache or 0) + 1

    asr = SimpleNamespace(transkriber=lambda pcm: {"text": "Hei", "chunks": [
        {"text": "Hei", "timestamp": (0, len(pcm) / 16000)}]})
    return asr, SimpleNamespace(infer=infer), calls


def test_speakers_are_preserved_across_chunks_and_isolated_between_recordings():
    sessions = SpeechSessions()
    asr, diar, calls = engines()
    audio = np.ones(32000, dtype=np.float32)
    first = sessions.transcribe(audio, asr, diar, "meeting-a", 0)
    assert first["segments"][0]["speaker"] == "SPEAKER_01"
    assert sessions.transcribe(audio, asr, diar, "meeting-a", 0) is first
    sessions.transcribe(audio, asr, diar, "meeting-a", 1)
    sessions.transcribe(audio, asr, diar, "meeting-b", 0)
    assert calls == [(32000, None), (16000, 1), (32000, None)]
    with pytest.raises(ValueError, match="out of order"):
        sessions.transcribe(audio * .5, asr, diar, "meeting-a", 0)


def test_session_expiry_capacity_and_batch_isolation():
    now = [0]
    sessions = SpeechSessions(ttl=10, capacity=1, clock=lambda: now[0])
    asr, diar, calls = engines()
    audio = np.ones(16000, dtype=np.float32)
    sessions.transcribe(audio, asr, diar, "a")
    with pytest.raises(OverflowError):
        sessions.transcribe(audio, asr, diar, "b")
    sessions.transcribe(audio, asr, diar)
    assert list(sessions.records) == ["a"]
    now[0] = 11
    sessions.purge()
    assert sessions.records == {}
    result = sessions.transcribe(audio, asr, diar, "b", 10)
    assert result["warnings"]


def test_failed_asr_does_not_advance_speaker_state():
    sessions = SpeechSessions()
    asr, diar, calls = engines()
    audio = np.ones(16000, dtype=np.float32)
    sessions.transcribe(audio, asr, diar, "a")
    broken = SimpleNamespace(transkriber=lambda pcm: (_ for _ in ()).throw(RuntimeError("unavailable")))
    with pytest.raises(RuntimeError):
        sessions.transcribe(audio, broken, diar, "a", 1)
    assert sessions.records["a"].end == 1
    assert sessions.records["a"].cache == 1


def test_labels_use_overlap_and_do_not_invent_speakers_during_unassigned_speech():
    chunks = [{"text": "Hei", "timestamp": (0, .5)}, {"text": "Ja", "timestamp": (1, 1.5)},
              {"text": "Uklart", "timestamp": (5, 6)}]
    turns = [{"start": 0, "end": .7, "speaker": "SPEAKER_00"},
             {"start": 1, "end": 2, "speaker": "SPEAKER_01"}]
    assert [s["speaker"] for s in label_words(chunks, turns)] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_UNKNOWN"]


def test_distinct_recordings_can_reach_asr_concurrently_without_mixing_state():
    sessions = SpeechSessions()
    asr, diar, _ = engines()
    barrier = threading.Barrier(2)
    original = asr.transkriber

    def infer(pcm):
        barrier.wait(timeout=5)
        return original(pcm)

    asr.transkriber = infer
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(sessions.transcribe, np.ones(16000), asr, diar, key, 0) for key in ("a", "b")]
        assert all(job.result(timeout=10)["text"] == "Hei" for job in jobs)
    assert sessions.records["a"] is not sessions.records["b"]
    assert sessions.records["a"].cache == sessions.records["b"].cache == 1
    assert not sessions.active


def test_concurrent_retry_for_same_recording_does_not_repeat_inference():
    sessions = SpeechSessions()
    asr, diar, calls = engines()
    entered, release = threading.Event(), threading.Event()
    original = asr.transkriber

    def infer(pcm):
        entered.set()
        assert release.wait(timeout=5)
        return original(pcm)

    asr.transkriber = infer
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(sessions.transcribe, np.ones(16000), asr, diar, "a", 0)
        assert entered.wait(timeout=5)
        second = pool.submit(sessions.transcribe, np.ones(16000), asr, diar, "a", 0)
        release.set()
        assert first.result(timeout=5) is second.result(timeout=5)
    assert len(calls) == 1
