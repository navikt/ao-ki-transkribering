import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from worker import cohost_llama, nemotron_api
from worker.transkribering import ctranslate2_api_model
from tests.test_nemotron_api import wav_audio


def test_cohosted_models_share_authenticated_endpoint_and_keep_sessions_separate(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-model-key")
    monkeypatch.setenv("SPEECH_ASR_BACKEND", "faster-whisper")
    monkeypatch.setenv("WHISPER_SMALL_MODELL", "/models/small")
    monkeypatch.setenv("BOREALIS_SMALL_MODELL", "/models/small.gguf")
    monkeypatch.setenv("NEMOTRON_DIAR_MODELL", "")
    loaded, calls = [], []

    def model(**kwargs):
        loaded.append(kwargs)
        return SimpleNamespace(enhet="cuda", inference_concurrency=1,
                               transkriber=lambda pcm: {"text": "small" if kwargs else "large", "chunks": []})

    monkeypatch.setattr(ctranslate2_api_model, "CTranslate2Transkriberer", model)

    @asynccontextmanager
    async def llama(path):
        assert path == "/models/small.gguf"

        def upstream(request):
            payload = json.loads(request.content)
            calls.append(payload)
            return httpx.Response(200, json={"model": payload["model"], "choices": []})

        async with httpx.AsyncClient(base_url="http://127.0.0.1:8002", transport=httpx.MockTransport(upstream)) as client:
            yield client

    monkeypatch.setattr(nemotron_api, "serve_llama", llama)
    auth = {"Authorization": "Bearer test-model-key"}
    with TestClient(nemotron_api.app) as client:
        assert client.get("/v1/models").status_code == 401
        ids = [item["id"] for item in client.get("/v1/models", headers=auth).json()["data"]]
        assert ids == [nemotron_api.MODEL_NAME, "nb-whisper-small", "borealis-4b"]
        assert client.get("/props", headers=auth).status_code == 403
        for name, text in [(nemotron_api.MODEL_NAME, "large"), ("nb-whisper-small", "small")]:
            response = client.post("/v1/audio/transcriptions", headers=auth,
                                   files={"file": ("test.wav", wav_audio())}, data={"model": name})
            assert response.status_code == 200
            assert response.json()["text"] == text
        assert nemotron_api.app.state.sessions is not nemotron_api.app.state.small_sessions
        payload = {"model": "borealis-4b", "messages": [{"role": "user", "content": "Hei"}]}
        assert client.post("/v1/chat/completions", json=payload).status_code == 401
        assert client.post("/v1/chat/completions", headers=auth, json=payload).json()["model"] == "borealis-4b"
        assert client.post("/v1/chat/completions", headers=auth, json={**payload, "model": "borealis-12b"}).status_code == 400
        assert len(calls) == 1
    assert loaded == [{}, {"source": "/models/small", "target": "/models/small-ct2-fp16", "inference_concurrency": 1}]


def test_llama_command_is_bounded_and_loopback_only(tmp_path):
    path = tmp_path / "model.gguf"
    path.touch()
    args = cohost_llama.command(path)
    for option, value in [("--host", "127.0.0.1"), ("--device", "CUDA0"),
                          ("--parallel", "1"), ("--ctx-size", "32768")]:
        assert args[args.index(option) + 1] == value
    assert "--jinja" in args and "--no-webui" in args
    with pytest.raises(RuntimeError, match="missing"):
        cohost_llama.command(tmp_path / "absent.gguf")


def test_manifest_keeps_all_evaluation_models_in_one_gpu_allocation():
    from pathlib import Path
    import yaml

    deployment, _ = yaml.safe_load_all((Path(__file__).parents[1] / "k8s/vllm-whisper.yaml").read_text())
    pod = deployment["spec"]["template"]["spec"]
    assert len(pod["containers"]) == 1
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "1"
    env = {entry["name"]: entry["value"] for entry in pod["containers"][0]["env"]}
    assert env["SPEECH_MODEL_NAME"] == "nb-whisper-large"
    assert env["WHISPER_SMALL_MODELL"] and env["BOREALIS_SMALL_MODELL"]
    downloader = pod["initContainers"][0]["command"][-1]
    assert "sha256sum -c" in downloader
    assert '"$SMALL/model.safetensors"' in downloader


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_llama_process_is_cleaned_up_on_normal_and_failed_body(monkeypatch, tmp_path, failed):
    events = []
    path = tmp_path / "model.gguf"
    path.touch()
    process = SimpleNamespace(returncode=None, terminate=lambda: events.append("terminate"),
                              kill=lambda: events.append("kill"))

    async def wait():
        process.returncode = 0

    async def spawn(*args, **kwargs):
        return process

    process.wait = wait
    monkeypatch.setattr(cohost_llama.asyncio, "create_subprocess_exec", spawn)
    original = httpx.AsyncClient
    monkeypatch.setattr(cohost_llama.httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200))))

    async def run():
        async with cohost_llama.serve(path) as client:
            assert client.base_url == "http://127.0.0.1:8002"
            if failed:
                raise ValueError("synthetic failure")

    if failed:
        with pytest.raises(ValueError):
            await run()
    else:
        await run()
    assert events == ["terminate"]
