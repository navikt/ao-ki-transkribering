"""API server entrypoint.

Run with:
  python -m uvicorn app_api:app --host 127.0.0.1 --port 8765
"""

from app_factory import create_app

app = create_app()
