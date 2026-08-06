from contracts.transcription import TranskripsjonSvar


def test_transkripsjon_svar_validates_segments():
    svar = TranskripsjonSvar.model_validate(
        {
            "tekst": "Hei der.",
            "segmenter": [
                {
                    "start": 0,
                    "slutt": 1.2,
                    "tekst": "Hei der.",
                    "taler": "SPEAKER_00",
                }
            ],
            "advarsler": [],
        }
    )

    assert svar.segmenter[0].taler == "SPEAKER_00"
    assert svar.model_dump()["segmenter"][0]["slutt"] == 1.2
