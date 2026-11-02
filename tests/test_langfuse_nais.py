import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("render_langfuse", ROOT / "scripts/render-langfuse-nais.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
IMAGE = "europe-north1-docker.pkg.dev/nais-management-test/ao-ki-taskforce/langfuse:test"


def test_dev_applications_have_native_storage_scoped_credentials_and_narrow_auth_exception():
    web, worker = renderer.render(IMAGE, IMAGE, "team-langfuse-dev-events", "clickhouse.private.example.test")
    assert web["spec"]["ingresses"] == ["https://ao-ki-langfuse.intern.dev.nav.no"]
    assert not worker["spec"].get("ingresses")
    assert web["spec"]["azure"]["sidecar"]["autoLoginIgnorePaths"] == ["/api/public/otel/v1/traces"]
    assert web["spec"]["uses"]["postgres"][0]["role"] == "admin"
    assert worker["spec"]["uses"]["postgres"][0]["role"] == "readwrite"
    assert web["spec"]["gcp"]["buckets"][0]["publicAccessPrevention"]
    assert not worker["spec"]["gcp"].get("buckets")
    for app in (web, worker):
        env = {item["name"]: item["value"] for item in app["spec"]["env"]}
        assert env["LANGFUSE_USE_GOOGLE_CLOUD_STORAGE"] == "true"
        assert env["TELEMETRY_ENABLED"] == "false"
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
        assert app["metadata"]["namespace"] == "ao-ki-taskforce"
        assert app["spec"]["replicas"] == {"min": 1, "max": 1}
        assert "nodeSelector" not in app["spec"]
    assert "secret_key" not in yaml.safe_dump_all([web, worker])


@pytest.mark.parametrize("host", ["https://user:password@host", "127.0.0.1", "host;command", "HOST.example"])
def test_renderer_rejects_unreviewed_endpoint_forms(host):
    with pytest.raises(ValueError):
        renderer.render(IMAGE, IMAGE, "team-langfuse-dev-events", host)


def test_workflow_is_opt_in_dev_only_and_does_not_apply_backing_services():
    workflow = yaml.safe_load((ROOT / ".github/workflows/langfuse-dev.yml").read_text())
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["deploy"]["default"] is False
    assert inputs["prerequisites_verified"]["default"] is False
    rendered = yaml.safe_dump(workflow)
    assert "prod-gcp" not in rendered
    assert "prerequisites_verified" in workflow["jobs"]["deploy"]["if"]
    assert all(step.get("env", {}).get("CLUSTER", "dev-gcp") == "dev-gcp"
               for step in workflow["jobs"]["deploy"]["steps"])
    postgres = yaml.safe_load((ROOT / ".nais/langfuse/postgres-dev.yaml").read_text())
    valkey = yaml.safe_load((ROOT / ".nais/langfuse/valkey-dev.yaml").read_text())
    assert postgres["spec"]["majorVersion"] == "18"
    assert valkey["spec"]["maxMemoryPolicy"] == "noeviction"
    assert valkey["spec"]["persistence"]["disabled"] is False
