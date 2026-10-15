"""Observe the WebSocket without recording audio, text, headers or user identity."""

import asyncio
import time

from shared.diagnostics import event, failure


class RecordingSocket:
    def __init__(self, socket):
        self.socket = socket
        self.started = time.monotonic()
        self.last_audio = None
        self.last_result = None
        self.audio_bytes = 0
        self.audio_packets = 0
        self.results = 0
        self.close_code = None
        self.stop_requested = False
        self.queue = RecordingQueue()
        self.send_lock = asyncio.Lock()

    def snapshot(self):
        now = time.monotonic()
        return dict(elapsed_s=round(now - self.started, 2),
                    audio_packets=self.audio_packets, audio_bytes=self.audio_bytes,
                    last_audio_age_s=round(now - self.last_audio, 2) if self.last_audio else None,
                    last_result_age_s=round(now - self.last_result, 2) if self.last_result else None,
                    results=self.results, close_code=self.close_code,
                    stop_requested=self.stop_requested, queue_depth=self.queue.qsize(),
                    chunk_inflight_s=round(now - self.queue.inflight, 2) if self.queue.inflight else None)

    async def receive(self):
        try:
            message = await self.socket.receive()
        except Exception as exc:
            failure("recording.receive_failed", exc, **self.snapshot())
            raise
        if message.get("type") == "websocket.disconnect":
            self.close_code = message.get("code")
            event("recording.peer_disconnected", **self.snapshot())
        raw = message.get("bytes")
        if raw:
            self.last_audio = time.monotonic()
            self.audio_bytes += len(raw)
            self.audio_packets += 1
        return message

    async def send_json(self, data):
        try:
            async with self.send_lock:
                await self.socket.send_json(data)
        except Exception as exc:
            failure("recording.send_failed", exc, message_type=data.get("type"), **self.snapshot())
            raise
        if data.get("type") == "segment":
            self.results += 1
            self.last_result = time.monotonic()
            event("recording.result_sent", characters=len(data.get("tekst", "")), **self.snapshot())
        elif data.get("type") == "heartbeat":
            event("recording.heartbeat", **self.snapshot())

    async def close(self):
        try:
            await self.socket.close()
            event("recording.close_sent", **self.snapshot())
        except Exception as exc:
            failure("recording.close_failed", exc, **self.snapshot())
            raise


class RecordingQueue(asyncio.Queue):
    def __init__(self):
        super().__init__(maxsize=4)
        self.enqueued = {}
        self.inflight = None

    async def put(self, item):
        started = time.monotonic()
        if item is not None:
            self.enqueued[id(item)] = started
        if self.full():
            event("recording.queue_backpressure", queue_depth=self.qsize())
        await super().put(item)
        if item is not None:
            event("recording.chunk_queued", chunk_start_s=item.start_s,
                  audio_seconds=len(item.pcm) / 16000, queue_depth=self.qsize(),
                  enqueue_wait_s=round(time.monotonic() - started, 3))

    async def get(self):
        item = await super().get()
        if item is not None:
            self.inflight = time.monotonic()
            event("recording.chunk_started", chunk_start_s=item.start_s,
                  queue_wait_s=round(self.inflight - self.enqueued.pop(id(item), self.inflight), 3))
        return item

    def task_done(self):
        if self.inflight is not None:
            event("recording.chunk_finished", elapsed_s=round(time.monotonic() - self.inflight, 3))
            self.inflight = None
        super().task_done()


def observe_task(task, role):
    def completed(done):
        if not done.cancelled() and (exc := done.exception()) is not None:
            failure("recording.task_failed", exc, role=role)
    task.add_done_callback(completed)
    return task
