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
            audio_path=self._path_in_work_dir(f"{job_id}{suffix}"),
            result_path=self.result_path(job_id),
        )

    def result_path(self, job_id: str) -> Path:
        return self._path_in_work_dir(f"{self._validate_job_id(job_id)}.json")

    def _validate_job_id(self, job_id: str) -> str:
        try:
            parsed = uuid.UUID(job_id)
        except ValueError:
            raise ValueError("Ugyldig jobb-ID") from None
        if str(parsed) != job_id:
            raise ValueError("Ugyldig jobb-ID")
        return job_id

    def _path_in_work_dir(self, name: str) -> Path:
        path = (self.work_dir / name).resolve()
        work_dir = self.work_dir.resolve()
        if path.parent != work_dir:
            raise ValueError("Ugyldig jobbfil")
        return path

    def exists(self, job_id: str) -> bool:
        try:
            return self.result_path(job_id).exists()
        except ValueError:
            return False

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
        if result_path.resolve().parent != self.work_dir.resolve():
            raise ValueError("Ugyldig jobbfil")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = result_path.with_name(f"{result_path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False))
        tmp_path.chmod(0o600)
        tmp_path.replace(result_path)

    def cleanup_completed(self, retention_seconds: int, *, now: float | None = None) -> int:
        """Expire terminal results without deleting jobs still being processed."""
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        cutoff = (time.time() if now is None else now) - retention_seconds
        removed = 0
        for path in self.work_dir.glob("*.json"):
            try:
                self._validate_job_id(path.stem)
                if path.is_symlink() or path.stat().st_mtime >= cutoff:
                    continue
                if self.read_path(path).get("status") not in {"ferdig", "feil"}:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
            except (ValueError, OSError):
                continue
        return removed
