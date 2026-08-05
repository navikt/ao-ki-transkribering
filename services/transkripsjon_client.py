from pathlib import Path
from typing import Any

import httpx

from settings import TRANSKRIPSJON_SERVICE_URL


async def transkriber_remote(
    lydfil: Path,
    *,
    n_talere: int = 0,
    service_url: str = TRANSKRIPSJON_SERVICE_URL,
) -> dict[str, Any]:
    """Call the remote transcription model service over HTTP."""
    url = service_url.rstrip("/") + "/transkriber"
    timeout = httpx.Timeout(10.0, read=None, write=None, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as klient:
        with lydfil.open("rb") as f:
            files = {"lydfil": (lydfil.name, f, "application/octet-stream")}
            data = {"n_talere": str(n_talere)}
            resp = await klient.post(url, data=data, files=files)
            resp.raise_for_status()
            return resp.json()
