import numpy as np

from shared.services.transkripsjon_client import _normalize_transkripsjon_payload, _pcm_to_wav_bytes


def test_normalize_transkripsjon_payload_supports_internal_contract():
    payload = {
        "tekst": "Hei",
        "segmenter": [{"start": 0.0, "slutt": 1.0, "tekst": "Hei", "taler": "SPEAKER_00"}],
        "advarsler": [],
    }

    svar = _normalize_transkripsjon_payload(payload)

    assert svar.tekst == "Hei"
    assert svar.segmenter[0].taler == "SPEAKER_00"


def test_normalize_transkripsjon_payload_supports_openai_verbose_json():
    payload = {
        "text": "Hei der",
        "segments": [{"start": 0.1, "end": 0.9, "text": "Hei der"}],
    }

    svar = _normalize_transkripsjon_payload(payload)

    assert svar.tekst == "Hei der"
    assert len(svar.segmenter) == 1
    assert svar.segmenter[0].start == 0.1
    assert svar.segmenter[0].slutt == 0.9
    assert svar.segmenter[0].taler == "SPEAKER_00"


def test_pcm_to_wav_bytes_creates_nonempty_payload():
    pcm = np.array([0.0, 0.25, -0.25, 0.5, -0.5], dtype=np.float32)

    wav = _pcm_to_wav_bytes(pcm)

    assert len(wav) > 44
    assert wav.startswith(b"RIFF")
