import os
import subprocess
import sys


def test_api_can_start_without_local_worker_flag():
    env = os.environ.copy()
    env["START_LOKAL_WORKER"] = "false"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from frontend.app import app; from shared.core.runtime import lokal_arbeider_aktiv; "
            "assert app.title; assert lokal_arbeider_aktiv is False",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
