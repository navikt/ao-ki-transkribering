import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_uses_the_verified_artifact_and_no_context_shift():
    spec = json.loads((ROOT / "k8s/borealis-q8.json").read_text())
    deployment, _ = yaml.safe_load_all((ROOT / "k8s/vllm-borealis.yaml").read_text())
    pod = deployment["spec"]["template"]["spec"]
    download = pod["initContainers"][0]["command"][-1]
    for name in ("revision", "filename", "sha256", "bucket"):
        assert spec[name] in download
    llama = next(c for c in pod["containers"] if c["name"] == "llama")
    assert "--no-context-shift" in llama["args"]
    assert llama["args"][llama["args"].index("--ctx-size") + 1] == "65536"
    assert llama["args"][llama["args"].index("--parallel") + 1] == "2"
    for name in ("dev", "prod"):
        app = yaml.safe_load((ROOT / f".nais/{name}.yaml").read_text().replace("{{image}}", "test-image"))
        env = {item["name"]: item["value"] for item in app["spec"]["env"]}
        assert int(env["LLM_CONTEXT_TOKENS"]) == 65536 // 2
    assert llama["args"][llama["args"].index("--device") + 1] == "CUDA0"
    env = {item["name"]: item["value"] for item in llama["env"]}
    assert "/usr/local/nvidia/lib64" in env["LD_LIBRARY_PATH"].split(":")


@pytest.mark.parametrize("valid", [True, False])
def test_staging_checks_hash_before_finalizing_or_writing_provenance(monkeypatch, valid):
    module_spec = importlib.util.spec_from_file_location("stage_gguf", ROOT / "scripts/stage-borealis-gguf.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    events = []

    class Sink(io.BytesIO):
        def close(self):
            events.append("finalize")
            super().close()

    process = SimpleNamespace(stdin=Sink(), kill=lambda: events.append("kill"), wait=lambda: 0)
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kw: process)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kw: events.append("metadata"))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kw: io.BytesIO(b"abc"))
    spec = {"repository": "publisher/model", "revision": "revision", "filename": "model.gguf",
            "bucket": "test-bucket", "size": 3,
            "sha256": hashlib.sha256(b"abc" if valid else b"wrong").hexdigest()}
    if valid:
        module.stage_model(spec)
        assert events == ["finalize", "metadata", "metadata", "metadata"]
    else:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            module.stage_model(spec)
        assert events == ["kill"]


def test_additional_models_have_separate_pinned_artifacts_and_whisper_runtime_metadata():
    borealis = json.loads((ROOT / "k8s/borealis-4b-q8.json").read_text())
    whisper = json.loads((ROOT / "k8s/nb-whisper-small-artifact.json").read_text())
    assert borealis["prefix"] == "borealis-4b-gguf"
    assert whisper["prefix"] == "whisper/nb-whisper-small"
    assert whisper["filename"] == "model.safetensors"
    assert {"config.json", "tokenizer.json", "preprocessor_config.json"} <= set(whisper["metadata_files"])
    for spec in (borealis, whisper):
        assert len(spec["revision"]) == 40
        assert len(spec["sha256"]) == 64
        assert spec["size"] > 0


def test_staging_respects_artifact_prefix_and_metadata_files(monkeypatch):
    module_spec = importlib.util.spec_from_file_location("stage_model", ROOT / "scripts/stage-borealis-gguf.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    commands = []
    process = SimpleNamespace(stdin=io.BytesIO(), kill=lambda: None, wait=lambda: 0)
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kw: commands.append(command) or process)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kw: commands.append(command))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kw: io.BytesIO(b"abc"))
    module.stage_model({"repository": "publisher/small", "revision": "revision", "filename": "model.safetensors",
                       "bucket": "test", "prefix": "whisper/small", "size": 3,
                       "sha256": hashlib.sha256(b"abc").hexdigest(), "metadata_files": ["config.json"]})
    assert "gs://test/whisper/small/revision/model.safetensors" in commands[0]
    assert commands[1][-1] == "gs://test/whisper/small/revision/config.json"
    assert commands[2][-1] == "gs://test/whisper/small/revision/provenance.json"
