"""
Tests for the <think>-filtering state machine in stream_tokens.
Mocks the Ollama HTTP stream — no running Ollama needed.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _linje(response: str = "", done: bool = False, thinking: str = "") -> str:
    chunk = {"response": response, "done": done}
    if thinking:
        chunk["thinking"] = thinking
    return json.dumps(chunk)


def _chunks(*tokens: str, done_last: bool = True) -> list[str]:
    linjer = [_linje(t) for t in tokens]
    if done_last:
        linjer.append(_linje(done=True))
    return linjer


async def _mock_stream(linjer: list[str]):
    """Simulerer resp.aiter_lines()."""
    for linje in linjer:
        yield linje


async def _samle_tokens(linjer: list[str]) -> tuple[list[str], str]:
    """Køyrer stream_tokens med mocka HTTP og returnerer (yieldta tokens, sluttekst)."""
    from ollama.klient import stream_tokens

    yielda = []
    sluttekst = ""

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = lambda: _mock_stream(linjer)

    mock_stream_cm = MagicMock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

    mock_klient = MagicMock()
    mock_klient.stream = MagicMock(return_value=mock_stream_cm)

    mock_klient_cm = MagicMock()
    mock_klient_cm.__aenter__ = AsyncMock(return_value=mock_klient)
    mock_klient_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("ollama.klient.httpx.AsyncClient", return_value=mock_klient_cm):
        async for token, ferdig, tekst in stream_tokens("system", "bruker"):
            if ferdig:
                sluttekst = tekst
            elif token:
                yielda.append(token)

    return yielda, sluttekst


# ---------------------------------------------------------------------------
# Utan thinking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vanleg_svar_vert_yieldta():
    linjer = _chunks("**Bakgrunn**", "\n", "Vi snakket om AAP.")
    tokens, sluttekst = await _samle_tokens(linjer)
    assert tokens
    assert "AAP" in sluttekst


# ---------------------------------------------------------------------------
# Med <think>-blokk (heil i éin token)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_think_blokk_vert_filtrert():
    linjer = _chunks("<think>intern tanke</think>", "**Bakgrunn**", "\nSvar her.")
    tokens, sluttekst = await _samle_tokens(linjer)
    full = "".join(tokens)
    assert "<think>" not in full
    assert "intern tanke" not in full
    assert "Svar" in sluttekst


# ---------------------------------------------------------------------------
# Med <think>-blokk splitta over fleire tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_think_blokk_splitta_over_tokens():
    linjer = _chunks("<", "think", ">intern tanke</", "think", ">", "Svar her.")
    tokens, sluttekst = await _samle_tokens(linjer)
    full = "".join(tokens)
    assert "intern tanke" not in full
    assert "Svar" in sluttekst


# ---------------------------------------------------------------------------
# Utan <think>-blokk — ingen forseinking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingen_think_gir_innhald_i_sluttekst():
    # Tokens kan vere buffra i UNDECIDED — sjekk at innhald kjem gjennom
    linjer = _chunks("Første", " token", " her.")
    tokens, sluttekst = await _samle_tokens(linjer)
    assert "Første token her." in ("".join(tokens) + sluttekst)


# ---------------------------------------------------------------------------
# Benchmark-konfigurasjonar
# ---------------------------------------------------------------------------

from benchmarks.optimalisering import Konfig


def test_konfig_til_options_inkluderer_num_ctx():
    k = Konfig("test", num_ctx=4096, num_predict=-1, temperature=0.25)
    opts = k.til_options()
    assert opts["num_ctx"] == 4096
    assert opts["temperature"] == 0.25
    assert "num_predict" not in opts  # -1 = ubegrensa, skal ikkje sendast


def test_konfig_til_options_med_num_predict():
    k = Konfig("test", num_ctx=4096, num_predict=400, temperature=0.1)
    opts = k.til_options()
    assert opts["num_predict"] == 400
