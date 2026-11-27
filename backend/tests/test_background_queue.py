"""In-process job processing used on Vercel (no Celery, no worker)."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from app.adapters.background_queue import WaitUntilInferenceQueue, vercel_wait_until


class RecordingProcessor:
    def __init__(self, error: Exception | None = None) -> None:
        self.jobs: list[UUID] = []
        self.error = error

    def process(self, job_id: UUID) -> None:
        self.jobs.append(job_id)
        if self.error is not None:
            raise self.error


def test_job_is_processed_after_scheduling() -> None:
    processor = RecordingProcessor()
    scheduled: list[asyncio.Future] = []
    queue = WaitUntilInferenceQueue(
        lambda: processor, schedule=lambda: lambda aw: scheduled.append(asyncio.ensure_future(aw))
    )
    job_id = uuid4()

    async def run() -> None:
        await queue.enqueue_async(job_id)
        assert processor.jobs == []  # nothing runs before the response
        await asyncio.gather(*scheduled)

    asyncio.run(run())
    assert processor.jobs == [job_id]


def test_processor_errors_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    processor = RecordingProcessor(RuntimeError("database down"))
    queue = WaitUntilInferenceQueue(lambda: processor)
    job_id = uuid4()

    async def run() -> None:
        await queue.enqueue_async(job_id)
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert processor.jobs == [job_id]
    assert "Background processing of inference job" in caplog.text


def test_outside_vercel_work_runs_on_the_event_loop() -> None:
    # No Vercel invocation context here, so wait_until would drop the work.
    async def run() -> int:
        done = asyncio.Event()

        async def work() -> int:
            done.set()
            return 1

        vercel_wait_until()(work())
        await asyncio.wait_for(done.wait(), timeout=1)
        return 1

    assert asyncio.run(run()) == 1


def test_sync_enqueue_is_not_supported() -> None:
    with pytest.raises(RuntimeError):
        WaitUntilInferenceQueue(lambda: RecordingProcessor()).enqueue(uuid4())
