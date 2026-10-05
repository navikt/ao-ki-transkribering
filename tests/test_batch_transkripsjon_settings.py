from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from worker.transkribering import batch


def test_batch_preserves_trailing_words_and_removes_text_beyond_speech(monkeypatch):
    pcm = np.concatenate([np.full(16000, 0.5, dtype="<f4"), np.zeros(48000, dtype="<f4")])
    monkeypatch.setattr(batch.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=pcm.tobytes()))
    monkeypatch.setattr(batch, "diariser", lambda *args, **kwargs: ([], None))
    transcriber = batch.LokalBatchTranskriberer.__new__(batch.LokalBatchTranskriberer)
    transcriber._asr = Mock(return_value={
        "text": "Hei der. Takk for meg.",
        "chunks": [
            {"timestamp": (0.2, 0.8), "text": "Hei"},
            {"timestamp": (1.5, 1.9), "text": " der."},
            {"timestamp": (3.0, 3.5), "text": " Takk for meg."},
        ],
    })

    result = transcriber.transkriber(Path("test.wav"))

    assert result.tekst == "Hei der."
    assert result.segmenter[0].tekst == result.tekst
    kwargs = transcriber._asr.call_args.kwargs
    assert kwargs["generate_kwargs"]["num_beams"] == 5
    assert kwargs["stride_length_s"] == (5, 2)
