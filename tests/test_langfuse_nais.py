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
    assert set(trigger) == {"workflow_dispatch", "workflow_call"}
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


def test_private_clickhouse_uses_persistent_storage_and_no_public_ingress():
    pvc = yaml.safe_load((ROOT / ".nais/langfuse/clickhouse-data-dev.yaml").read_text())
    assert pvc["spec"]["resources"]["requests"]["storage"] == "50Gi"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    app, service, inbound, outbound = yaml.safe_load_all((ROOT / ".nais/langfuse/clickhouse-dev.yaml").read_text())
    assert app["spec"]["strategy"] == {"type": "Recreate"}
    assert app["spec"]["replicas"] == {"min": 1, "max": 1}
    assert not app["spec"].get("ingresses")
    assert app["spec"]["filesFrom"][0]["persistentVolumeClaim"] == pvc["metadata"]["name"]
    assert service["spec"]["type"] == "ClusterIP"
    assert {p["port"] for p in service["spec"]["ports"]} == {8443, 9440}
    assert {p["port"] for p in inbound["spec"]["ingress"][0]["ports"]} == {8443, 9440}
    assert outbound["spec"]["policyTypes"] == ["Egress"]


def test_internal_clickhouse_mounts_ca_and_does_not_create_public_egress():
    host = "ao-ki-langfuse-clickhouse-tls.ao-ki-taskforce.svc.cluster.local"
    apps = renderer.render(IMAGE, IMAGE, "team-langfuse-dev-events", host)
    for app in apps:
        env = {e["name"]: e["value"] for e in app["spec"]["env"]}
        assert env["LANGFUSE_CLICKHOUSE_CA_FILE"] == "/var/run/clickhouse-ca/ca.crt"
        assert "NODE_EXTRA_CA_CERTS" not in env
        assert "SSL_CERT_FILE" not in env
        assert host not in {e["host"] for e in app["spec"]["accessPolicy"]["outbound"]["external"]}


def test_branch_dispatch_cannot_deploy_the_main_application():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    for name in ("build-frontend", "build-backend"):
        assert "github.ref_name == 'main'" in workflow["jobs"][name]["if"]
    assert "github.ref_name == 'feat/langfuse-nais-dev'" in workflow["jobs"]["langfuse-dev"]["if"]
