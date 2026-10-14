"""Combine independent speech requests into one Whisper GPU forward batch."""

import logging
import queue
import threading
import time
from concurrent.futures import Future

logger = logging.getLogger(__name__)


class BatchedWhisper:
    def __init__(self, model, batch_size=2, wait_seconds=0.05):
        if not 1 <= batch_size <= 8 or wait_seconds < 0:
            raise ValueError("Invalid Whisper batch settings")
        self.model, self.enhet = model, model.enhet
        self.batch_size, self.wait_seconds = batch_size, wait_seconds
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self.batch_counts = {}
        self._thread = threading.Thread(target=self._work, name="whisper-batches", daemon=True)
        self._thread.start()

    def transkriber(self, pcm):
        future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("Whisper batcher is closed")
            self._queue.put((pcm, future))
        return future.result()

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)
        self._thread.join()

    def _work(self):
        stopping = False
        while not stopping:
            first = self._queue.get()
            if first is None:
                break
            batch = [first]
            deadline = time.monotonic() + self.wait_seconds
            while len(batch) < self.batch_size:
                try:
                    item = self._queue.get(timeout=max(0, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if item is None:
                    stopping = True
                    break
                batch.append(item)
            start = time.monotonic()
            try:
                outputs = self.model.transkriber_batch([pcm for pcm, _ in batch])
                if len(outputs) != len(batch):
                    raise RuntimeError("Whisper returned an incomplete batch")
                self.batch_counts[len(batch)] = self.batch_counts.get(len(batch), 0) + 1
                for (_, future), output in zip(batch, outputs):
                    future.set_result(output)
                logger.warning("whisper_batch size=%d seconds=%.3f", len(batch), time.monotonic() - start)
            except Exception as exc:
                logger.error("whisper_batch_failed size=%d error=%s", len(batch), type(exc).__name__)
                for _, future in batch:
                    future.set_exception(exc)
