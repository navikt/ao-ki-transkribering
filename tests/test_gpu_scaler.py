import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["local", "cluster"])
def scaler(request, tmp_path):
    if request.param == "local":
        return ["bash", str(ROOT / "scripts/start-gpu-deployment-with-fallback.sh")]
    resources = list(yaml.safe_load_all((ROOT / "k8s/working-hours-scaler.yaml").read_text()))
    config = next(resource for resource in resources if resource["kind"] == "ConfigMap")
    script = tmp_path / "scaler.sh"
    script.write_text(config["data"]["start-gpu-deployment-with-fallback.sh"])
    return ["sh", str(script)]


def run_scaler(scaler, tmp_path, scenario):
    fake = tmp_path / "kubectl"
    fake.write_text(f"#!{sys.executable}\n" + """
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ['CALL_LOG']).open('a') as log:
    log.write(json.dumps(args) + '\\n')
if 'scale' in args and '--replicas=1' in args:
    Path(os.environ['ALLOCATED']).touch()
if 'get' in args and 'pods' in args and '--no-headers' not in args:
    print('model-pod', end='')
elif 'get' in args and 'pod' in args:
    query = args[-1]
    assigned = os.environ['SCENARIO'] == 'existing' or (
        os.environ['SCENARIO'] == 'allocate' and Path(os.environ['ALLOCATED']).exists()
    )
    if 'nodeName' in query and assigned:
        print('gpu-node', end='')
    elif 'nodeSelector' in query:
        print('gpu-l4-a', end='')
    elif 'conditions' in query:
        print('False', end='')
    elif 'phase' in query:
        print('Pending', end='')
""")
    fake.chmod(0o755)
    call_log = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CALL_LOG": str(call_log),
        "ALLOCATED": str(tmp_path / "allocated"),
        "SCENARIO": scenario,
        "WAIT_ROLLOUT": "false",
        "KEEP_PENDING": "true",
        "SCHEDULE_TIMEOUT_SECONDS": "10" if scenario == "allocate" else "0",
        "POOLS": "gpu-l4-a gpu-l4-b gpu-l4-c",
    }
    result = subprocess.run([*scaler, "vllm-whisper"], env=env, capture_output=True, text=True, timeout=15)
    calls = [json.loads(line) for line in call_log.read_text().splitlines()]
    return result, calls


def test_preserves_assigned_node_even_while_model_is_loading(scaler, tmp_path):
    result, calls = run_scaler(scaler, tmp_path, "existing")
    assert result.returncode == 0
    assert "result=preserved" in result.stdout
    assert "ready=False" in result.stdout
    assert all("patch" not in call and "scale" not in call for call in calls)


def test_logs_success_and_stops_zone_rotation(scaler, tmp_path):
    result, calls = run_scaler(scaler, tmp_path, "allocate")
    assert result.returncode == 0
    assert "result=scheduled pool=gpu-l4-a" in result.stdout
    assert "elapsed_s=" in result.stdout
    assert len([call for call in calls if "patch" in call]) == 1


def test_logs_all_failed_pools_and_leaves_request_pending(scaler, tmp_path):
    result, calls = run_scaler(scaler, tmp_path, "unavailable")
    assert result.returncode == 1
    for zone in "abc":
        assert f"result=timeout pool=gpu-l4-{zone}" in result.stdout
    assert "result=pending pool=gpu-l4-c" in result.stdout
    scale_calls = [call for call in calls if "scale" in call]
    assert "--replicas=1" in scale_calls[-1]
