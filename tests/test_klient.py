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
    from worker.ollama.klient import stream_tokens

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

    with patch("worker.ollama.klient.httpx.AsyncClient", return_value=mock_klient_cm):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("ending", ["missing_done", "length", "complete"])
async def test_ollama_requires_done_and_rejects_length_for_both_call_modes(monkeypatch, streaming, ending):
    import httpx

    from shared.services.llm_proxy_client import IncompleteModelResponse
    from worker.ollama import klient

    answer = "Et ferdig svar på bokmål."
    chunks = [{"response": answer, "done": False}]
    if ending != "missing_done":
        chunks.append({"response": "", "done": True, "done_reason": "length" if ending == "length" else "stop"})
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text="\n".join(json.dumps(chunk) for chunk in chunks) + "\n")

    original = httpx.AsyncClient
    monkeypatch.setattr(klient.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    events = []

    async def consume():
        if streaming:
            async for event in klient.stream_tokens("Regler", "Kilde", "modell", max_tokens=4096):
                events.append(event)
            return events[-1][2]
        return await klient.kall("Regler", "Kilde", "modell", max_tokens=4096)

    if ending == "complete":
        assert await consume() == answer
        if streaming:
            assert events[-1] == ("", True, answer)
            assert sum(done for _, done, _ in events) == 1
    else:
        with pytest.raises(IncompleteModelResponse):
            await consume()
        assert all(not done and full == "" for _, done, full in events)
    assert len(requests) == 1
    assert requests[0]["options"]["num_predict"] == 4096
    assert requests[0]["model"] == "modell"
    assert requests[0]["stream"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("body", ["", _linje(response="Partial response", done=False)])
async def test_ollama_sync_missing_done_never_returns_even_an_empty_or_raw_response(monkeypatch, raw, body):
    import httpx

    from shared.services.llm_proxy_client import IncompleteModelResponse
    from worker.ollama import klient

    original = httpx.AsyncClient
    monkeypatch.setattr(klient.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)), **kwargs))
    with pytest.raises(IncompleteModelResponse):
        await klient.kall("Regler", "Kilde", raw=raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("chunk", [
    {"error": "PRIVATE_PROVIDER_ERROR"},
    {"done": True, "done_reason": "content_filter"},
    {"done": True, "refusal": "PRIVATE_REFUSAL"},
])
async def test_ollama_provider_errors_and_refusals_are_not_incomplete_outputs(monkeypatch, streaming, chunk):
    import httpx

    from shared.services.llm_proxy_client import IncompleteModelResponse, ModelResponseError, RefusedModelResponse
    from worker.ollama import klient

    original = httpx.AsyncClient
    monkeypatch.setattr(klient.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=json.dumps(chunk) + "\n")), **kwargs))
    expected = ModelResponseError if "error" in chunk else RefusedModelResponse
    with pytest.raises(expected) as caught:
        if streaming:
            async for _ in klient.stream_tokens("Regler", "Kilde"):
                pytest.fail("A failed response must not complete")
        else:
            await klient.kall("Regler", "Kilde")
    assert not isinstance(caught.value, IncompleteModelResponse)
    assert "PRIVATE" not in str(caught.value)
