"""Explicit feedback packages. Meeting content never enters tracing or application logs."""
import hashlib
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from frontend.routes.referat import _job_owner, summary_jobs
from shared.services import llm_observability

router = APIRouter()
MAX_BYTES = 2 * 1024 * 1024


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int = Field(ge=1, le=5, strict=True)
    category: Literal["general", "transcription", "speakers", "summary_accuracy", "missing_content", "speed", "usability"]
    comment: str = Field(default="", max_length=4000)
    job_id: UUID | None = None
    attach: bool = False
    transcript: str | None = Field(default=None, max_length=500000)


def enabled():
    return bool(os.getenv("FEEDBACK_BUCKET"))


def attachments_enabled():
    return enabled() and os.getenv("FEEDBACK_ATTACHMENTS_ENABLED", "false").lower() == "true"


@lru_cache(maxsize=4)
def bucket(name):
    from google.cloud import storage
    return storage.Client().bucket(name)


def save(case_id, payload, fingerprint):
    """Conditional creation makes the package atomic and retries idempotent."""
    from google.api_core.exceptions import PreconditionFailed
    blob = bucket(os.environ["FEEDBACK_BUCKET"]).blob(f"cases/{case_id}.json")
    blob.metadata = {"fingerprint": fingerprint, "schema_version": "1"}
    try:
        blob.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json",
                                if_generation_match=0, timeout=20)
    except PreconditionFailed:
        blob.reload(timeout=10)
        if (blob.metadata or {}).get("fingerprint") != fingerprint:
            raise HTTPException(409, "Referansen er allerede brukt. Start en ny tilbakemelding.") from None


@router.get("/feedback/config")
def config(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": enabled(), "attachments": attachments_enabled(), "audio": False}


@router.put("/feedback/{submission_id}")
async def submit(submission_id: UUID, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    owner = _job_owner(request)
    if not enabled():
        raise HTTPException(503, "Tilbakemelding er ikke tilgjengelig nå.")
    # Browser submissions must originate from this application.
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.headers.get('host', '')}":
        raise HTTPException(403, "Ugyldig avsender")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Tilbakemeldingen er for stor.")
        raw.extend(chunk)
    try:
        feedback = Feedback.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Kontroller vurderingen og tekstfeltene.") from None
    if feedback.transcript is not None and (not feedback.attach or feedback.job_id):
        raise HTTPException(422, "Ugyldig vedlegg")
    if feedback.attach and not attachments_enabled():
        raise HTTPException(403, "Deling av møteinnhold er ikke aktivert.")
    job = summary_jobs._read(str(feedback.job_id), owner) if feedback.job_id else None
    if feedback.attach and job and (job["status"] != "ferdig" or "feedback_source" not in job):
        raise HTTPException(409, "Referatet kan ikke deles. Generer et nytt referat først.")
    if feedback.attach and not job and not (feedback.transcript or "").strip():
        raise HTTPException(422, "Transkripsjon mangler")
    case_id = hashlib.sha256(f"{owner}:{submission_id}".encode()).hexdigest()
    canonical = feedback.model_dump(mode="json")
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    package = {
        "schema_version": 1,
        "case_id": case_id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "feedback": {"rating": feedback.rating, "category": feedback.category, "comment": feedback.comment},
        "metadata": {"release": os.getenv("NAIS_APP_IMAGE", "unknown"),
                     **({key: job[key] for key in ("modell", "handling", "scenario", "prompt", "elapsed_s") if key in job} if job else {})},
        "trace_id": job.get("trace_id") if job else None,
        "attachments": None,
    }
    if feedback.attach:
        package["attachments"] = ({"transcript": job["feedback_source"], "summary": job["tekst"],
                                    "warnings": job.get("advarsler", [])} if job else {"transcript": feedback.transcript})
    try:
        await run_in_threadpool(save, case_id, package, fingerprint)
    except HTTPException:
        raise
    except Exception:
        # SDK exceptions may contain request data; do not log or return them.
        raise HTTPException(503, "Kunne ikke lagre. Prøv igjen med samme tilbakemelding.") from None
    if job and job.get("trace_id"):
        await run_in_threadpool(llm_observability.feedback_score, case_id, job["trace_id"], feedback.rating)
    return {"case_id": case_id}
