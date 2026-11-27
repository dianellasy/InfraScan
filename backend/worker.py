"""Celery worker entrypoint for Vercel.

Vercel builds this module into a private function that only Vercel Queues can
invoke (see `[[tool.vercel.subscribers]]` in pyproject.toml). Each invocation
runs one task. Locally, run `celery -A inference.celery worker` instead.
"""

import inference.celery.tasks  # noqa: F401  (registers the tasks)
from inference.celery.celery_app import celery_app as app

__all__ = ["app"]
