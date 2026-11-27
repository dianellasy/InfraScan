"""Run inference jobs in the API function after the response (Vercel).

On Vercel there is no always-on worker. `POST /inference/jobs` commits the job,
answers 202, and hands the job to `wait_until`, which keeps the invocation
alive (up to the function's max duration) while `InferenceJobProcessor` runs
in a worker thread. This is the same processor the Celery task uses locally,
so the database results are identical.

It is not a durable queue: a job whose invocation is killed (timeout, crash)
stays in its last status and is not retried.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

logger = logging.getLogger(__name__)

Schedule = Callable[[Awaitable[object]], None]


class JobProcessor(Protocol):
    def process(self, job_id: UUID) -> object: ...


def vercel_wait_until() -> Schedule:
    """Vercel's `wait_until` when running inside a Vercel invocation.

    Elsewhere (local runs, tests) the work is scheduled on the running event
    loop instead, so it still happens rather than being silently dropped.
    """
    try:
        from vercel.cache.context import get_context
        from vercel.functions import wait_until

        if get_context().wait_until is not None:
            return wait_until
    except ImportError:
        pass
    return _schedule_on_loop


_background: set[asyncio.Task[object]] = set()


def _schedule_on_loop(awaitable: Awaitable[object]) -> None:
    task = asyncio.ensure_future(awaitable)
    _background.add(task)
    task.add_done_callback(_background.discard)


class WaitUntilInferenceQueue:
    """`InferenceQueueAdapter` that processes the job after the HTTP response."""

    def __init__(
        self,
        build_processor: Callable[[], JobProcessor],
        schedule: Callable[[], Schedule] = vercel_wait_until,
    ) -> None:
        self._build_processor = build_processor
        self._schedule = schedule

    def enqueue(self, job_id: UUID) -> None:
        raise RuntimeError("WaitUntilInferenceQueue must be used through enqueue_async")

    async def enqueue_async(self, job_id: UUID) -> None:
        # Called on the request's event loop, so wait_until sees its context.
        self._schedule()(asyncio.to_thread(self._run, job_id))

    def _run(self, job_id: UUID) -> None:
        try:
            self._build_processor().process(job_id)
        except Exception:
            # The processor records failures on the job itself; this only
            # catches errors before it could (for example, no database).
            logger.exception("Background processing of inference job %s failed", job_id)
