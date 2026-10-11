import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from images.litellm.scoped_auth import authorize
from k8s.model_security import ModelAccessMiddleware
from shared.services import model_auth

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_identity_token_preserves_application_key(monkeypatch):
    monkeypatch.setattr(model_auth, "MODEL_API_IAM_AUTH", True)
    monkeypatch.setattr(model_auth, "MODEL_API_AUDIENCE", "https://gateway.run.app")
    monkeypatch.setattr(model_auth, "_identity_token", lambda audience: "identity-token")
    headers = await model_auth.authenticated_headers(
        "https://gateway.run.app/v1/models", {"Authorization": "Bearer application-key"}
    )
    assert headers == {
        "Authorization": "Bearer application-key",
        "X-Serverless-Authorization": "Bearer identity-token",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["http://gateway.run.app/v1/models", "https://other.run.app/v1/models"])
async def test_identity_token_cannot_leak_to_other_service(monkeypatch, target):
    monkeypatch.setattr(model_auth, "MODEL_API_IAM_AUTH", True)
    monkeypatch.setattr(model_auth, "MODEL_API_AUDIENCE", "https://gateway.run.app")
    with pytest.raises(ValueError):
        await model_auth.authenticated_headers(target, {})


@pytest.mark.asyncio
async def test_identity_failure_does_not_fall_back_to_api_key(monkeypatch):
    monkeypatch.setattr(model_auth, "MODEL_API_IAM_AUTH", True)
    monkeypatch.setattr(model_auth, "MODEL_API_AUDIENCE", "https://gateway.run.app")
    def fail(audience):
        raise RuntimeError("metadata unavailable")
    monkeypatch.setattr(model_auth, "_identity_token", fail)
    with pytest.raises(RuntimeError):
        await model_auth.authenticated_headers("https://gateway.run.app/v1/models", {})


def gateway_app():
    app = FastAPI()
    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
    async def endpoint(request: Request):
        environment = await authorize(request, request.headers.get("authorization", "").removeprefix("Bearer "))
        return JSONResponse({"environment": environment})
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["dev", "prod"])
async def test_scoped_keys_can_infer_but_not_administer(monkeypatch, environment):
    monkeypatch.setenv("NAIS_DEV_API_KEY", "dev-key")
    monkeypatch.setenv("NAIS_PROD_API_KEY", "prod-key")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app()), base_url="https://gateway") as client:
        headers = {"Authorization": f"Bearer {environment}-key"}
        response = await client.post("/v1/chat/completions", headers=headers, json={"model": "borealis-12b", "messages": []})
        assert response.json() == {"environment": environment}
        assert (await client.get("/v1/models", headers=headers)).status_code == 200
        assert (await client.post("/key/generate", headers=headers, json={})).status_code == 403
        assert (await client.post("/v1/chat/completions", headers=headers, json={"model": "other"})).status_code == 403
        assert (await client.post("/v1/chat/completions", headers=headers, json={"model": "borealis-12b", "api_base": "http://attacker"})).status_code == 403
        assert (await client.get("/v1/models?api_base=http://attacker", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_audio_auth_accepts_multipart_and_rejects_master_key(monkeypatch):
    monkeypatch.setenv("NAIS_DEV_API_KEY", "dev-key")
    monkeypatch.setenv("NAIS_PROD_API_KEY", "prod-key")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app()), base_url="https://gateway") as client:
        response = await client.post("/v1/audio/transcriptions", headers={"Authorization": "Bearer dev-key"},
                                     files={"file": ("audio.wav", b"wav")}, data={"model": "nb-whisper-large", "language": "no"})
        assert response.status_code == 200
        assert (await client.get("/v1/models", headers={"Authorization": "Bearer master-key"})).status_code == 401
        assert (await client.get("/v1/models")).status_code == 401


@pytest.mark.asyncio
async def test_scoped_auth_preserves_uploaded_body_for_downstream(monkeypatch):
    monkeypatch.setenv("NAIS_DEV_API_KEY", "dev-key")
    app = FastAPI()
    @app.post("/v1/audio/transcriptions")
    async def endpoint(request: Request):
        await authorize(request, "dev-key")
        body = await request.body()
        form = await request.form()
        return {"raw_body_preserved": b"synthetic-audio" in body, "file": (await form["file"].read()).decode()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://gateway") as client:
        response = await client.post("/v1/audio/transcriptions", files={"file": ("audio.wav", b"synthetic-audio")}, data={"model": "nb-whisper-large"})
        assert response.json() == {"raw_body_preserved": True, "file": "synthetic-audio"}


@pytest.mark.asyncio
async def test_model_middleware_blocks_alternate_inference_routes(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "model-key")
    app = FastAPI()
    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def endpoint():
        return {"status": "ok"}
    app.add_middleware(ModelAccessMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://model") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.post("/v1/chat/completions")).status_code == 401
        headers = {"Authorization": "Bearer model-key"}
        assert (await client.post("/v1/chat/completions", headers=headers)).status_code == 200
        for path in ("/invocations", "/metrics", "/docs", "/tokenize"):
            assert (await client.post(path, headers=headers)).status_code == 403


def test_tls_renderer_keeps_credentials_out_of_manifests():
    spec = importlib.util.spec_from_file_location("model_renderer", ROOT / "scripts/render-model-manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("whisper", "borealis"):
        deployment, service = module.render(ROOT / f"k8s/vllm-{name}.yaml", "10.164.15.200", secure=True, gateway_cidr="10.250.0.0/26")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        assert "--ssl-certfile" in container["args"]
        assert "--middleware" in container["args"]
        assert container["startupProbe"]["httpGet"]["scheme"] == "HTTPS"
        assert service["spec"]["loadBalancerSourceRanges"] == ["10.250.0.0/26"]
        assert service["spec"]["externalTrafficPolicy"] == "Local"
        assert container["env"][0]["valueFrom"]["secretKeyRef"]["name"] == "model-api-auth"
        assert "BEGIN PRIVATE KEY" not in json.dumps(deployment)
