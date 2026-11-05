"""Own a bounded, private llama.cpp process inside the speech GPU allocation."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from shared.diagnostics import event

MODEL = "borealis-4b"
URL = "http://127.0.0.1:8002"


def command(model_path):
    if not Path(model_path).is_file():
        raise RuntimeError("Co-hosted Borealis model file is missing")
    return ["/app/llama-server", "--model", str(model_path), "--alias", MODEL,
            "--host", "127.0.0.1", "--port", "8002", "--device", "CUDA0",
            "--n-gpu-layers", "99", "--ctx-size", "32768", "--parallel", "1",
            "--batch-size", "256", "--ubatch-size", "128", "--threads", "4",
            "--flash-attn", "on", "--jinja", "--no-context-shift", "--no-webui",
            "--log-verbosity", "0"]


@asynccontextmanager
async def serve(model_path):
    process = await asyncio.create_subprocess_exec(*command(model_path),
                                                  stdout=asyncio.subprocess.DEVNULL,
                                                  stderr=asyncio.subprocess.DEVNULL)
    try:
        async with httpx.AsyncClient(base_url=URL, trust_env=False,
                                    timeout=httpx.Timeout(300, connect=5),
                                    limits=httpx.Limits(max_connections=36)) as client:
            async def ready():
                while True:
                    if process.returncode is not None:
                        raise RuntimeError("Co-hosted Borealis process exited during startup")
                    try:
                        response = await client.get("/health", timeout=2)
                        if response.status_code == 200:
                            return
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(1)

            await asyncio.wait_for(ready(), timeout=180)
            event("model.cohost_ready", model=MODEL, context_tokens=32768, slots=1)
            yield client
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
