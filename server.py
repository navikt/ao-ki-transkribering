"""Compatibility entrypoint for uvicorn server:app."""

from app_factory import create_app

app = create_app()
