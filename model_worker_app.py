"""Compatibility entrypoint for uvicorn model_worker_app:app."""

from apps.model_worker.app import app

__all__ = ["app"]
