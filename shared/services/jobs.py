import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JobPaths:
    job_id: str
    audio_path: Path
    result_path: Path


class JobStore:
    """File-backed transcription job state store."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def create_paths(self, suffix: str) -> JobPaths:
        job_id = str(uuid.uuid4())
        return JobPaths(
            job_id=job_id,
            audio_path=self.work_dir / f"{job_id}{suffix}",
            result_path=self.result_path(job_id),
        )

    def result_path(self, job_id: str) -> Path:
        return self.work_dir / f"{job_id}.json"

    def exists(self, job_id: str) -> bool:
        return self.result_path(job_id).exists()

    def read(self, job_id: str) -> dict[str, Any]:
        return self.read_path(self.result_path(job_id))

    def read_path(self, result_path: Path) -> dict[str, Any]:
        return json.loads(result_path.read_text())

    def write_queued(self, result_path: Path) -> None:
        self.write_path(result_path, {"status": "venter"})

    def write_transcribing(
        self,
        result_path: Path,
        *,
        model_id: str,
        device: str,
    ) -> None:
        self.write_path(
            result_path,
            {
                "status": "transkriberer",
                "fase": "konverterer",
                "start_tid": time.time(),
                "modell_id": model_id,
                "enhet": device,
                "lyd_varighet_s": None,
            },
        )

    def update_path(self, result_path: Path, values: dict[str, Any]) -> dict[str, Any]:
        data = self.read_path(result_path)
        data.update(values)
        self.write_path(result_path, data)
        return data

    def write_done(
        self,
        result_path: Path,
        *,
        text: str,
        segments: list[dict[str, Any]],
        warnings: list[str] | None = None,
    ) -> None:
        data = {"status": "ferdig", "tekst": text, "segmenter": segments}
        if warnings:
            data["advarsler"] = warnings
        self.write_path(result_path, data)

    def write_failed(self, result_path: Path, message: str) -> None:
        self.write_path(result_path, {"status": "feil", "feilmelding": message})

    def write_path(self, result_path: Path, data: dict[str, Any]) -> None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = result_path.with_name(f"{result_path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False))
        tmp_path.replace(result_path)
