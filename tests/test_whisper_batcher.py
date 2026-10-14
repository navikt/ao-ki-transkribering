import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from worker.transkribering.whisper_batcher import BatchedWhisper


def test_independent_inputs_share_one_model_batch_and_keep_results_separate():
    calls = []

    def infer(inputs):
        calls.append(inputs)
        return [f"result-{value}" for value in inputs]

    model = BatchedWhisper(SimpleNamespace(enhet="test", transkriber_batch=infer), 2, 1)
    barrier = threading.Barrier(2)

    def submit(value):
        barrier.wait(timeout=5)
        return model.transkriber(value)

    try:
        with ThreadPoolExecutor(2) as pool:
            first, second = pool.submit(submit, "a"), pool.submit(submit, "b")
            assert first.result(timeout=5) == "result-a"
            assert second.result(timeout=5) == "result-b"
        assert len(calls) == 1
        assert set(calls[0]) == {"a", "b"}
        assert model.batch_counts == {2: 1}
    finally:
        model.close()


def test_batch_failure_reaches_callers_and_worker_recovers():
    def infer(inputs):
        if inputs == ["bad"]:
            raise RuntimeError("failed")
        return inputs

    model = BatchedWhisper(SimpleNamespace(enhet="test", transkriber_batch=infer), 1)
    try:
        with pytest.raises(RuntimeError, match="failed"):
            model.transkriber("bad")
        assert model.transkriber("good") == "good"
    finally:
        model.close()
    with pytest.raises(RuntimeError, match="closed"):
        model.transkriber("late")
