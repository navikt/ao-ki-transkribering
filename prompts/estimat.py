# Estimerte sekundar for LLM-generering per modell (kalibrert på Apple M-seriens MPS).
# Skaler med transkripsjonslengde i beregn_llm_estimat().
_LLM_ESTIMAT_SEK: dict[str, float] = {
    "borealis:12b":         20.0,
    "borealis:27b":         40.0,
    "borealis:4b":           8.0,
    "qwen3:32b":            45.0,
    "qwen3.5:latest":       10.0,
    "qwen3.5-128k:latest":  10.0,
    "gemma4:26b":           35.0,
    "glm-4.7-flash:latest": 20.0,
}

_STANDARD_MODELL = "borealis:12b"


def beregn_llm_estimat(modell: str | None, transkripsjon: str, fallback: str = _STANDARD_MODELL) -> int:
    """Returner estimert genereringstid i sekunder for valgt modell og transkripsjonslengde."""
    m = modell or fallback
    base = next((v for k, v in _LLM_ESTIMAT_SEK.items() if k in m), 35.0)
    ord_antall = len(transkripsjon.split())
    # +10% per 500 ord over 500 (lengre kontekst = tregere prefill)
    if ord_antall > 500:
        base *= 1.0 + (ord_antall - 500) / 5000
    return max(5, round(base))
