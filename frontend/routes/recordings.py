"""Retryable, sample-addressed live audio. No server-side audio archive."""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from uuid import UUID

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from shared.core.settings import TRANSKRIPSJON_BACKEND
from shared.diagnostics import event, failure, recording_id
from shared.services.transkripsjon_client import transkriber_remote_pcm
from worker.transkribering.sanntid import LydChunk, SegmentSamler, transkriber_pcm

router = APIRouter()
RATE = 16000
TTL = 600
MAX_SESSIONS = 64


@dataclass
class Session:
    touched: float = field(default_factory=time.monotonic)
    sequence: int = -1
    end: int = 0
    digest: str = ""
    task: asyncio.Task | None = None
    result: dict | None = None
    speakers: object = None
    collector: SegmentSamler = field(default_factory=SegmentSamler)


sessions: dict[str, Session] = {}


def cleanup_sessions():
    now = time.monotonic()
    for key, session in list(sessions.items()):
        if now - session.touched > TTL and (session.task is None or session.task.done()):
            del sessions[key]


async def process(session, key, sequence, start, raw):
    token = recording_id.set(key)
    try:
        pcm = np.frombuffer(raw, dtype="<f4").copy()
        if TRANSKRIPSJON_BACKEND == "remote":
            reply = await transkriber_remote_pcm(pcm, session_id=key, chunk_start=start / RATE)
            result = reply.model_dump()
            speakers = None
        else:
            result, speakers = await asyncio.to_thread(transkriber_pcm, pcm, session.speakers)
        overlap = max(0, session.end - start) / RATE
        result = session.collector.legg_til(result, LydChunk(pcm, start / RATE, overlap))
        session.speakers = speakers
        session.end = start + len(pcm)
        session.sequence = sequence
        session.result = dict(type="segment", sequence=sequence, start_sample=start,
                              end_sample=session.end, state="processed",
                              **(result or {"tekst": "", "segmenter": []}))
        event("recording.chunk_processed", sequence=sequence, start_sample=start,
              end_sample=session.end)
        return session.result
    except Exception as exc:
        failure("recording.chunk_failed", exc, sequence=sequence)
        raise
    finally:
        session.touched = time.monotonic()
        recording_id.reset(token)


@router.post("/recordings/{key}/chunks/{sequence}")
async def upload_chunk(key: UUID, sequence: int, request: Request,
                       start_sample: int = Query(ge=0, le=16000 * 8 * 3600)):
    if sequence < 0 or sequence > 2200:
        raise HTTPException(422, "Invalid sequence")
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 15 * RATE * 4:
            raise HTTPException(413, "Chunk exceeds 15 seconds")
    if not raw or len(raw) % 4 or not np.isfinite(np.frombuffer(raw, dtype="<f4")).all():
        raise HTTPException(422, "Invalid PCM")
    key = str(key)
    now = time.monotonic()
    cleanup_sessions()
    session = sessions.get(key)
    if session is None:
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(429, "Recording capacity reached")
        # Browser retains processed results; after a restart it can resume at its
        # first unfinished chunk. Speaker continuity may reset in that case.
        session = sessions[key] = Session(sequence=sequence - 1, end=start_sample)
    digest = hashlib.sha256(str(start_sample).encode() + bytes(raw)).hexdigest()
    session.touched = now
    if session.task and not session.task.done():
        if sequence != session.sequence + 1 or digest != session.digest:
            raise HTTPException(409, "Another chunk is processing")
    elif sequence == session.sequence:
        if digest != session.digest:
            raise HTTPException(409, "Chunk identity conflict")
        return session.result
    else:
        if sequence != session.sequence + 1 or not max(0, session.end - RATE) <= start_sample <= session.end:
            raise HTTPException(409, "Non-contiguous timeline")
        session.digest = digest
        event("recording.chunk_received", recording_id=key, sequence=sequence,
              start_sample=start_sample, samples=len(raw) // 4)
        session.task = asyncio.create_task(process(session, key, sequence, start_sample, bytes(raw)))
        # A disconnected HTTP client must not cancel inference or lose its result.
        session.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    try:
        return await asyncio.shield(session.task)
    except Exception:
        raise HTTPException(503, "Chunk processing failed; retry the same chunk") from None
