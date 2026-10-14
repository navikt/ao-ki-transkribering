"""Bounded admission with exclusive access to a shared speech model."""

import asyncio


class QueueFull(Exception):
    pass


class SpeechRequestQueue:
    def __init__(self, capacity=32, concurrency=1):
        if capacity < 1 or not 1 <= concurrency <= capacity:
            raise ValueError("Queue capacity must be positive")
        self.capacity = capacity
        self.pending = 0
        self._lock = asyncio.Semaphore(concurrency)

    async def run(self, operation):
        if self.pending >= self.capacity:
            raise QueueFull
        self.pending += 1
        try:
            async with self._lock:
                task = asyncio.create_task(operation())
                try:
                    return await asyncio.shield(task)
                finally:
                    # Cancelling an HTTP task cannot stop its inference thread.
                    # Keep exclusive access until that thread's operation finishes.
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not task.cancelled():
                        task.exception()
        finally:
            self.pending -= 1
