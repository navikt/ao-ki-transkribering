import pytest


@pytest.fixture(autouse=True)
def offline_route_token_counter(monkeypatch):
    """Unit routes never contact a real model unless a test explicitly injects it."""
    from frontend.routes import referat

    async def unavailable(*args):
        return None

    monkeypatch.setattr(referat, "count_llm_tokens", unavailable)
