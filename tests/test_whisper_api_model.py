import numpy as np

from worker.transkribering.whisper_api_model import WhisperTranskriberer


def test_quiet_speech_and_missing_final_timestamp_are_preserved():
    model = WhisperTranskriberer.__new__(WhisperTranskriberer)
    calls = []

    def pipeline(pcm, **kwargs):
        calls.append((pcm.copy(), kwargs))
        return {"text": " Hei der", "chunks": [
            {"text": " Hei", "timestamp": (0, .4)},
            {"text": " der", "timestamp": (.4, None)},
        ]}

    model.pipeline = pipeline
    audio = np.full(16000, .0001, dtype=np.float32)
    result = model.transkriber(audio)
    np.testing.assert_array_equal(calls[0][0], audio)
    assert calls[0][1]["generate_kwargs"]["language"] == "no"
    assert result["chunks"][-1]["timestamp"] == (.4, 1)
    assert result["text"] == " Hei der"


def test_digital_silence_does_not_generate_hallucinations():
    model = WhisperTranskriberer.__new__(WhisperTranskriberer)
    assert model.transkriber(np.zeros(16000)) == {"text": "", "chunks": []}
