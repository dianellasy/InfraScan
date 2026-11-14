from inference.celery.celery_app import celery_app

app = celery_app

__all__ = ["app", "celery_app"]
