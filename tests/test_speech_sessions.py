from types import SimpleNamespace

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
