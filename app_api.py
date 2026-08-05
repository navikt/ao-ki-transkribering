"""Compatibility entrypoint for uvicorn app_api:app."""

from apps.api.app import app

__all__ = ["app"]
