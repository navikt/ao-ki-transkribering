#!/usr/bin/env python3
"""Run synthetic concurrent model requests from an authenticated NAIS runtime.

Print status/latency metadata only. Never read recordings or print generated text.
"""

import asyncio
import io
import json
import time
import wave

import httpx
import numpy as np

from shared.core.settings import AI_PROXY_API_KEY, AI_PROXY_URL
from shared.services.model_auth import authenticated_headers

MODELS = ("nb-whisper-large", "nb-whisper-small", "borealis-12b", "borealis-4b")


def synthetic_audio():
    samples = np.random.default_rng(42).normal(0, 0.005, 15 * 16000)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes((samples * 32767).astype("<i2").tobytes())
    return output.getvalue()


async def main():
    root = AI_PROXY_URL.rstrip("/")
    headers = await authenticated_headers(root, {"Authorization": f"Bearer {AI_PROXY_API_KEY}"})
    audio = synthetic_audio()
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        response = await client.get(root + "/v1/models", headers=headers)
        response.raise_for_status()
        listed = {item["id"] for item in response.json()["data"]}
        if not set(MODELS) <= listed:
            raise RuntimeError("Expected model aliases are not all available")

        async def request(model):
            started = time.monotonic()
            try:
                if model.startswith("nb-whisper"):
                    response = await client.post(root + "/v1/audio/transcriptions", headers=headers,
                        data={"model": model, "language": "no", "response_format": "verbose_json"},
                        files={"file": ("synthetic.wav", audio, "audio/wav")})
                else:
                    response = await client.post(root + "/v1/chat/completions", headers=headers,
                        json={"model": model, "max_tokens": 96, "temperature": 0,
                              "messages": [{"role": "user", "content":
                                  "Skriv et kort sammendrag på norsk: Et syntetisk møte tok opp testing. "
                                  "Kvalitet og kostnader er fortsatt uavklart. Ingen oppgaver ble tildelt."}]})
                success = response.is_success
                if success:
                    payload = response.json()
                    success = isinstance(payload.get("text"), str) if model.startswith("nb-whisper") else bool(payload.get("choices"))
                return {"model": model, "status": response.status_code, "ok": success,
                        "elapsed_s": round(time.monotonic() - started, 3)}
            except (httpx.HTTPError, ValueError):
                return {"model": model, "status": None, "ok": False,
                        "elapsed_s": round(time.monotonic() - started, 3)}

        results = await asyncio.gather(*(request(model) for model in MODELS for _ in range(2)))
    print(json.dumps({"kind": "synthetic_smoke_not_quality_benchmark", "concurrent_requests": 8,
                      "results": results}), flush=True)
    if not all(item["ok"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
