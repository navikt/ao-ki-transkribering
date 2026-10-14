import asyncio

import pytest

from worker.transkribering.request_queue import QueueFull, SpeechRequestQueue


@pytest.mark.asyncio
async def test_32_requests_are_serialized_and_33rd_is_rejected():
    queue = SpeechRequestQueue()
    release = asyncio.Event()
    started = asyncio.Event()
    active = 0

    async def infer():
        nonlocal active
        active += 1
        assert active == 1
        started.set()
        await release.wait()
        active -= 1
        return "transcript"

    tasks = [asyncio.create_task(queue.run(infer)) for _ in range(32)]
    await started.wait()
    assert queue.pending == 32
    with pytest.raises(QueueFull):
        await queue.run(infer)
    release.set()
    assert await asyncio.gather(*tasks) == ["transcript"] * 32
    assert queue.pending == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_releases_admission_without_running():
    queue = SpeechRequestQueue()
    started, release = asyncio.Event(), asyncio.Event()

    async def active():
        started.set()
        await release.wait()

    async def forbidden():
        pytest.fail("Cancelled queued work must not run")

    first = asyncio.create_task(queue.run(active))
    await started.wait()
    second = asyncio.create_task(queue.run(forbidden))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert queue.pending == 1
    release.set()
    await first
    assert queue.pending == 0


@pytest.mark.asyncio
async def test_cancelled_inference_keeps_gpu_exclusive_until_finished():
    queue = SpeechRequestQueue()
    started, release = asyncio.Event(), asyncio.Event()

    async def active():
        started.set()
        await release.wait()

    async def following():
        assert release.is_set()
        return "next"

    first = asyncio.create_task(queue.run(active))
    await started.wait()
    first.cancel()
    second = asyncio.create_task(queue.run(following))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await second == "next"
    assert queue.pending == 0


@pytest.mark.asyncio
async def test_failed_inference_releases_queue():
    queue = SpeechRequestQueue()

    async def fail():
        raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError, match="inference failed"):
        await queue.run(fail)
    assert queue.pending == 0
    assert await queue.run(lambda: asyncio.sleep(0, result="recovered")) == "recovered"


@pytest.mark.asyncio
async def test_configured_concurrency_allows_two_operations_not_three():
    queue = SpeechRequestQueue(capacity=32, concurrency=2)
    release, two_started = asyncio.Event(), asyncio.Event()
    active = 0
    peak = 0

    async def infer():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1

    tasks = [asyncio.create_task(queue.run(infer)) for _ in range(3)]
    await asyncio.wait_for(two_started.wait(), timeout=2)
    assert queue.pending == 3
    assert active == 2
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 2
    assert queue.pending == 0
