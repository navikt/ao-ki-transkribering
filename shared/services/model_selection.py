"""Bound website choices to the configured backend and supported cloud aliases."""


def transcription_models(backend, default):
    alternatives = ["nb-whisper-large", "nb-whisper-small"]
    return list(dict.fromkeys([default, *(alternatives if backend == "remote" and default in alternatives else [])]))


def summary_models(backend, default):
    alternatives = ["borealis-12b", "borealis-4b"]
    return list(dict.fromkeys([default, *(alternatives if backend == "openai" and default in alternatives else [])]))


def resolve_model(value, choices):
    if value is None:
        return choices[0]
    if value not in choices:
        raise ValueError("Modellvalget er ikke tilgjengelig med dette oppsettet")
    return value
