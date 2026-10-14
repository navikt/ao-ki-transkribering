from types import SimpleNamespace
from pathlib import Path
import sys

import numpy as np
import pytest

from worker.transkribering.ctranslate2_api_model import CTranslate2Transkriberer


def test_cloud_ct2_preserves_quiet_audio_and_disables_vad_filtering():
    model = CTranslate2Transkriberer.__new__(CTranslate2Transkriberer)
    calls = []

    def transcribe(pcm, **options):
        calls.append((pcm.copy(), options))
        word = SimpleNamespace(word=" Hei", start=0.1, end=0.5)
        return iter([SimpleNamespace(text=" Hei", words=[word])]), None

    model.model = SimpleNamespace(transcribe=transcribe)
    pcm = np.full(16000, 0.00001, dtype=np.float32)
    result = model.transkriber(pcm)
    np.testing.assert_array_equal(calls[0][0], pcm)
    assert calls[0][1]["vad_filter"] is False
    assert calls[0][1]["no_speech_threshold"] is None
    assert calls[0][1]["beam_size"] == 5
    assert calls[0][1]["language"] == "no"
    assert result == {"text": " Hei", "chunks": [{"text": " Hei", "timestamp": (0.1, 0.5)}]}


def test_cloud_ct2_retains_text_when_word_alignment_is_missing():
    model = CTranslate2Transkriberer.__new__(CTranslate2Transkriberer)
    model.model = SimpleNamespace(transcribe=lambda *a, **k: (
        iter([SimpleNamespace(text="Hei", words=[], start=0, end=2)]), None))
    assert model.transkriber(np.ones(16000))["chunks"] == [{"text": "Hei", "timestamp": (0, 1)}]


def test_cloud_ct2_skips_only_digital_silence():
    model = CTranslate2Transkriberer.__new__(CTranslate2Transkriberer)
    assert model.transkriber(np.zeros(16000)) == {"text": "", "chunks": []}


def test_conversion_preserves_preprocessor_and_loads_fp16_workers(monkeypatch, tmp_path):
    calls = []
    target = tmp_path / "converted"
    monkeypatch.setenv("WHISPER_MODELL", str(tmp_path / "source"))
    monkeypatch.setenv("WHISPER_CT2_MODELL", str(target))
    monkeypatch.setenv("SPEECH_INFERENCE_CONCURRENCY", "2")

    class Converter:
        def __init__(self, source, **options):
            calls.append((source, options))

        def convert(self, output, **options):
            assert options == {"quantization": "float16"}
            Path(output).mkdir()
            (Path(output) / "model.bin").write_bytes(b"fake")

    def whisper(path, **options):
        assert path == str(target)
        assert options == {"device": "cuda", "compute_type": "float16", "num_workers": 2,
                           "cpu_threads": 2, "local_files_only": True}
        return SimpleNamespace(feature_extractor=SimpleNamespace(mel_filters=np.zeros((128, 1))),
                               model=SimpleNamespace(n_mels=128))

    monkeypatch.setitem(sys.modules, "ctranslate2.converters", SimpleNamespace(TransformersConverter=Converter))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper))
    assert CTranslate2Transkriberer().inference_concurrency == 2
    assert calls == [(str(tmp_path / "source"), {"load_as_float16": True,
                     "copy_files": ["tokenizer.json", "preprocessor_config.json"]})]
    CTranslate2Transkriberer()
    assert len(calls) == 1  # Reuse a complete conversion within the same pod.


def test_mismatched_preprocessor_fails_at_startup(monkeypatch, tmp_path):
    (tmp_path / "model.bin").write_bytes(b"fake")
    monkeypatch.setenv("WHISPER_CT2_MODELL", str(tmp_path))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **k:
        SimpleNamespace(feature_extractor=SimpleNamespace(mel_filters=np.zeros((80, 1))),
                        model=SimpleNamespace(n_mels=128))))
    with pytest.raises(RuntimeError, match="mel bins"):
        CTranslate2Transkriberer()
