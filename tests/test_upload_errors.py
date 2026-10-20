import httpx
import pytest
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.routes import transkripsjon as routes
from shared.services.jobs import JobStore


@pytest.mark.parametrize("status,expected", [(413, "for stor"), (401, "autentisering"), (503, "ikke tilgjengelig")])
def test_failed_upload_returns_actionable_error_without_model_details(tmp_path, monkeypatch, status, expected):
    store = JobStore(tmp_path)
    monkeypatch.setattr(routes, "job_store", store)
    monkeypatch.setattr(routes, "TRANSKRIPSJON_BACKEND", "remote")

    async def fail(*args, **kwargs):
        request = httpx.Request("POST", "https://model.test/?key=PRIVATE")
        response = httpx.Response(status, request=request, text="PRIVATE")
        raise httpx.HTTPStatusError("PRIVATE", request=request, response=response)

    monkeypatch.setattr(routes, "transkriber_remote", fail)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        uploaded = client.post("/transkriber", files={"lydfil": ("PRIVATE.wav", b"PRIVATE", "audio/wav")})
        key = uploaded.json()["jobb_id"]
        state = client.get(f"/status/{key}")
        result = client.get(f"/resultat/{key}")
    assert state.json()["status"] == "feil"
    assert expected in state.json()["feilmelding"]
    assert expected in result.json()["detail"]
    assert "PRIVATE" not in state.text + result.text
    assert not list(tmp_path.glob("*.wav"))


@pytest.mark.asyncio
async def test_remote_upload_progress_uses_processed_audio_not_a_local_model_estimate(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    paths = store.create_paths(".wav")
    store.write_transcribing(paths.result_path, model_id="nb-whisper-large", device="remote")
    store.update_path(paths.result_path, {"start_tid": time.time() - 10, "lyd_varighet_s": 100,
                                          "behandlet_lyd_s": 50, "fase": "transkriberer"})
    monkeypatch.setattr(routes, "job_store", store)
    state = await routes.sjekk_status(paths.job_id)
    assert state["fremdrift"] == 0.475
    assert state["estimert_total_s"] == pytest.approx(20, abs=1)
