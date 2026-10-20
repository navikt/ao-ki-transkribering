"""Summary generation belongs to a job, independently of browser connections."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable

from fastapi import HTTPException

from shared.diagnostics import event, failure
from shared.services.jobs import JobStore


class SummaryJobs:
    def __init__(self, store: JobStore, concurrency: int = 4, capacity: int = 16):
        self.store = store
        self.semaphore = asyncio.Semaphore(concurrency)
        self.capacity = capacity
        self.tasks: dict[str, asyncio.Task] = {}

    def _read(self, key: str, owner: str) -> dict:
        if not self.store.exists(key):
            raise HTTPException(404, "Genereringsjobben finnes ikke lenger. Start genereringen på nytt.")
        data = self.store.read(key)
        if data.get("kind") != "llm" or data.get("owner") != owner:
            raise HTTPException(404, "Ukjent genereringsjobb")
        return data

    def status(self, key: str, owner: str) -> dict:
        data = self._read(key, owner)
        if data["status"] not in {"ferdig", "feil"} and key not in self.tasks:
            data = self.store.update_path(self.store.result_path(key), {
                "status": "feil", "melding": "Genereringen ble avbrutt ved en omstart. Start den på nytt.",
            })
        return {name: value for name, value in data.items()
                if name in {"jobb_id", "status", "melding", "tekst", "modell", "handling", "scenario"}}

    def submit(self, key: str, owner: str, fingerprint: str, metadata: dict,
               generate: Callable[[], AsyncIterator[dict]], error_message: Callable[[Exception], str]) -> dict:
        if self.store.exists(key):
            data = self._read(key, owner)
            if data["fingerprint"] != fingerprint:
                raise HTTPException(409, "Jobb-ID er allerede brukt for en annen forespørsel")
            return self.status(key, owner)
        if len(self.tasks) >= self.capacity:
            raise HTTPException(429, "Køen for referater er full. Prøv igjen om litt.")
        self.store.write_path(self.store.result_path(key), {
            **metadata, "kind": "llm", "owner": owner, "fingerprint": fingerprint,
            "jobb_id": key, "status": "venter", "melding": "Venter på ledig kapasitet …",
        })
        task = asyncio.create_task(self._run(key, generate, error_message))
        self.tasks[key] = task

        def finished(task):
            self.tasks.pop(key, None)
            if not task.cancelled() and task.exception() is not None:
                failure("llm.job_task_failed", task.exception(), job_id=key)

        task.add_done_callback(finished)
        event("llm.job_submitted", job_id=key, pending=len(self.tasks))
        return self.status(key, owner)

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, key, generate, error_message):
        path = self.store.result_path(key)
        started = time.monotonic()
        try:
            async with self.semaphore:
                self.store.update_path(path, {"status": "genererer", "melding": "Genererer …"})
                event("llm.job_started", job_id=key)
                async for result in generate():
                    if result["type"] == "progress":
                        self.store.update_path(path, {"melding": result["melding"]})
                        event("llm.job_progress", job_id=key)
                    elif result["type"] == "ferdig":
                        self.store.update_path(path, {"status": "ferdig", "tekst": result["tekst"], "melding": "Ferdig"})
                        event("llm.job_completed", job_id=key, characters=len(result["tekst"]),
                              elapsed_s=round(time.monotonic() - started, 3))
                        return
                raise RuntimeError("Generation ended without a result")
        except asyncio.CancelledError:
            self.store.update_path(path, {"status": "feil", "melding": "Genereringen ble avbrutt ved en omstart."})
            event("llm.job_cancelled", job_id=key)
            raise
        except Exception as exc:
            failure("llm.job_failed", exc, job_id=key, elapsed_s=round(time.monotonic() - started, 3))
            self.store.update_path(path, {"status": "feil", "melding": error_message(exc)})
