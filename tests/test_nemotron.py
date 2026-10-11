import asyncio
import contextlib
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from worker.transkribering import batch, nemotron, sanntid


def test_batch_startup_selects_nemotron_without_loading_whisper(monkeypatch):
    fake = SimpleNamespace(enhet="cpu")
    selected = []

    def load(model_id):
        selected.append(model_id)
        return fake

    monkeypatch.setattr(batch, "LOKAL_ASR_BACKEND", "nemotron")
    monkeypatch.setattr(nemotron, "NemotronTranskriberer", load)
    transcriber = batch.LokalBatchTranskriberer("test/nemotron")
    assert selected == ["test/nemotron"]
    assert transcriber._asr is fake
    assert transcriber.enhet == "cpu"


def test_system_info_shows_nemotron_for_batch_and_recording(monkeypatch):
    from frontend.routes import helse

    monkeypatch.setattr(helse, "TRANSKRIPSJON_BACKEND", "local")
    monkeypatch.setattr(helse, "LOKAL_ASR_BACKEND", "nemotron")
    monkeypatch.setattr(helse, "MODELL_ID", "test/nemotron")
    asr = helse.system_info()["asr"]
    assert asr["batch_modell"] == asr["sanntid_modell"] == "test/nemotron"
    assert asr["lokal_motor"] == "nemotron"


def test_subword_timestamps_group_words_without_losing_text():
    tokens = [{"token": " Hei", "start": .1, "end": .18},
              {"token": " ver", "start": .3, "end": .38},
              {"token": "den.", "start": .4, "end": .48}]
    words = nemotron.tokens_til_ord(tokens, 1)
    assert "".join(w["text"] for w in words) == " Hei verden."
    assert words == [{"text": " Hei", "timestamp": (.1, .18)},
                     {"text": " verden.", "timestamp": (.3, .48)}]
    assert nemotron.tokens_til_ord([], 1) == []


def test_bounded_windows_keep_context_words_once_and_preserve_audio_end(monkeypatch):
    calls = []
    outputs = iter([
        [{"token": "A", "start": 22, "end": 22.08}, {"token": " B", "start": 25, "end": 25.08}],
        [{"token": "A", "start": 1, "end": 1.08}, {"token": " B", "start": 4, "end": 4.08},
         {"token": " C", "start": 28, "end": 28.08}],
        [{"token": "C", "start": 4, "end": 4.08}, {"token": " END", "start": 15, "end": 15.08}],
    ])

    class Processor:
        def __call__(self, pcm, **kwargs):
            assert kwargs["language"] == "nb-NO"
            calls.append(len(pcm) / 16000)
            return SimpleNamespace(to=lambda *args, **kwargs: {})

        def decode(self, *args, **kwargs):
            return "unused", [next(outputs)]

    model = nemotron.NemotronTranskriberer.__new__(nemotron.NemotronTranskriberer)
    model._processor = Processor()
    model._model = SimpleNamespace(dtype="float32", generate=lambda **kwargs: SimpleNamespace(sequences=[], durations=[]))
    model.enhet = "cpu"
    model._lock = threading.Lock()
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=contextlib.nullcontext))
    result = model.transkriber(np.zeros(60 * 16000, dtype="<f4"))
    assert calls == [27, 30, 15]
    assert result["text"] == "A B C END"
    assert [w["timestamp"][0] for w in result["chunks"]] == [22, 25, 49, 60]
    assert model.transkriber(np.array([], dtype="<f4")) == {"text": "", "chunks": []}
    with pytest.raises(ValueError, match="finite mono"):
        model.transkriber(np.array([np.nan], dtype="<f4"))


def test_batch_nemotron_preserves_short_speaker_turns_and_trailing_words(monkeypatch):
    pcm = np.full(3 * 16000, .1, dtype="<f4")
    monkeypatch.setattr(batch.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=pcm.tobytes()))
    monkeypatch.setattr(batch, "diariser", lambda *args, **kwargs: ([
        {"start": 0, "slutt": 1, "taler": "SPEAKER_00"},
        {"start": 1, "slutt": 3, "taler": "SPEAKER_01"}], None))
    decoder = SimpleNamespace(transkriber=lambda pcm: {
        "text": "Ja. Avtale mandag.",
        "chunks": [{"text": "Ja.", "timestamp": (.1, .5)},
                   {"text": " Avtale", "timestamp": (1.3, 1.8)},
                   {"text": " mandag.", "timestamp": (3, 3)}]})
    transcriber = batch.LokalBatchTranskriberer.__new__(batch.LokalBatchTranskriberer)
    transcriber.asr_backend = "nemotron"
    transcriber._asr = decoder
    result = transcriber.transkriber(Path("test.wav"))
    assert result.tekst == "Ja. Avtale mandag."
    assert [s.taler for s in result.segmenter] == ["SPEAKER_00", "SPEAKER_01"]
    assert result.advarsler == [nemotron.TIMESTAMP_WARNING]


def test_live_nemotron_preserves_speaker_prototypes(monkeypatch):
    previous = np.zeros((2, 192))
    updated = np.ones((2, 192))
    monkeypatch.setattr(sanntid, "LOKAL_ASR_BACKEND", "nemotron")
    monkeypatch.setattr(sanntid, "hent_sanntidsmodell", lambda: SimpleNamespace(transkriber=lambda pcm: {
        "chunks": [{"text": "Hei.", "timestamp": (.1, .5)},
                   {"text": " Ja.", "timestamp": (1.1, 1.5)}]}))

    def diarise(pcm, prototyper):
        assert prototyper is previous
        return [{"start": 0, "slutt": 1, "taler": "SPEAKER_00"},
                {"start": 1, "slutt": 2, "taler": "SPEAKER_01"}], updated

    monkeypatch.setattr(sanntid, "diariser", diarise)
    result, prototypes = sanntid.transkriber_pcm(np.ones(2 * 16000, dtype="<f4"), previous)
    assert result["tekst"] == "Hei. Ja."
    assert [s["taler"] for s in result["segmenter"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert prototypes is updated


@pytest.mark.asyncio
async def test_live_inference_error_is_reported_without_hanging(monkeypatch):
    from frontend.routes import sanntid as routes

    class Socket:
        def __init__(self):
            self.messages = iter([{"bytes": np.ones(2 * 16000, dtype="<f4").tobytes()},
                                  {"text": '{"type":"stopp"}'}])
            self.sent = []

        async def receive(self):
            return next(self.messages)

        async def send_json(self, message):
            self.sent.append(message)

        async def close(self):
            self.closed = True

    def fail(*args):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(routes, "hent_sanntidsmodell", lambda: None)
    monkeypatch.setattr(routes, "transkriber_pcm", fail)
    socket = Socket()
    await asyncio.wait_for(routes._sanntid_lokal(socket), timeout=2)
    assert socket.closed
    assert socket.sent[0]["type"] == "feil"
    assert "decode failed" in socket.sent[0]["melding"]
