#!/usr/bin/env python3
"""Concurrent smoke/stress test for the LiteLLM model API.

Defaults are intentionally modest. Increase --concurrency-levels and --requests
when both one-GPU model deployments are up and you actually want load.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Result:
    ok: bool
    status_code: int | None
    seconds: float
    error: str = ""


def tofu_output(name: str, tf_dir: str) -> str:
    return subprocess.check_output(
        ["tofu", f"-chdir={tf_dir}", "output", "-raw", name],
        text=True,
    ).strip()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def summarize(label: str, concurrency: int, results: list[Result]) -> dict[str, Any]:
    latencies = [r.seconds for r in results]
    ok = [r for r in results if r.ok]
    errors = [r for r in results if not r.ok]
    return {
        "model": label,
        "concurrency": concurrency,
        "requests": len(results),
        "ok": len(ok),
        "errors": len(errors),
        "avg_s": statistics.mean(latencies) if latencies else 0.0,
        "p50_s": percentile(latencies, 50),
        "p90_s": percentile(latencies, 90),
        "p95_s": percentile(latencies, 95),
        "max_s": max(latencies) if latencies else 0.0,
        "first_error": errors[0].error[:180] if errors else "",
    }


def print_summary(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    headers = ["model", "conc", "req", "ok", "err", "avg", "p50", "p90", "p95", "max", "first_error"]
    print("\n" + " | ".join(headers))
    print("-" * 110)
    for row in rows:
        print(
            " | ".join(
                [
                    str(row["model"]),
                    str(row["concurrency"]),
                    str(row["requests"]),
                    str(row["ok"]),
                    str(row["errors"]),
                    f"{row['avg_s']:.2f}",
                    f"{row['p50_s']:.2f}",
                    f"{row['p90_s']:.2f}",
                    f"{row['p95_s']:.2f}",
                    f"{row['max_s']:.2f}",
                    row["first_error"],
                ]
            )
        )


async def chat_once(client: httpx.AsyncClient, url: str, model: str, prompt: str, max_tokens: int) -> Result:
    start = time.perf_counter()
    try:
        response = await client.post(
            f"{url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
        )
        seconds = time.perf_counter() - start
        if response.status_code >= 400:
            return Result(False, response.status_code, seconds, response.text)
        response.json()
        return Result(True, response.status_code, seconds)
    except Exception as exc:  # noqa: BLE001 - benchmark should capture all failures
        return Result(False, None, time.perf_counter() - start, repr(exc))


async def audio_once(client: httpx.AsyncClient, url: str, model: str, audio_path: Path) -> Result:
    start = time.perf_counter()
    try:
        content = audio_path.read_bytes()
        files = {"file": (audio_path.name, content, "audio/wav")}
        data = {"model": model, "response_format": "verbose_json", "language": "no"}
        response = await client.post(f"{url}/v1/audio/transcriptions", data=data, files=files)
        seconds = time.perf_counter() - start
        if response.status_code >= 400:
            return Result(False, response.status_code, seconds, response.text)
        response.json()
        return Result(True, response.status_code, seconds)
    except Exception as exc:  # noqa: BLE001
        return Result(False, None, time.perf_counter() - start, repr(exc))


async def run_level(label: str, concurrency: int, requests: int, call) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def limited_call() -> Result:
        async with semaphore:
            return await call()

    started = time.perf_counter()
    results = await asyncio.gather(*(limited_call() for _ in range(requests)))
    row = summarize(label, concurrency, results)
    row["wall_s"] = time.perf_counter() - started
    return row


async def main() -> int:
    parser = argparse.ArgumentParser(description="Stress-test LiteLLM/vLLM model endpoints.")
    parser.add_argument("--tf-dir", default=os.getenv("TF_DIR", "terraform/gke"))
    parser.add_argument("--url", default=os.getenv("LITELLM_URL", ""))
    parser.add_argument("--key", default=os.getenv("LITELLM_KEY", ""))
    parser.add_argument("--models", default=os.getenv("STRESS_MODELS", "whisper,borealis"))
    parser.add_argument("--concurrency-levels", default=os.getenv("CONCURRENCY_LEVELS", "1,2,4"))
    parser.add_argument("--requests", type=int, default=int(os.getenv("REQUESTS_PER_LEVEL", "4")))
    parser.add_argument("--audio", default=os.getenv("TEST_AUDIO", "testdata/tre_stemmer_test.wav"))
    parser.add_argument("--chat-model", default=os.getenv("CHAT_MODEL", "borealis-12b"))
    parser.add_argument("--audio-model", default=os.getenv("AUDIO_MODEL", "nb-whisper-large"))
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("MAX_TOKENS", "80")))
    parser.add_argument(
        "--prompt",
        default=os.getenv(
            "CHAT_PROMPT",
            "Skriv et kort norsk sammendrag i tre punkter om arbeid, helse og oppfølging.",
        ),
    )
    parser.add_argument("--timeout", type=float, default=float(os.getenv("HTTP_TIMEOUT_SECONDS", "600")))
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON in addition to table.")
    args = parser.parse_args()

    url = (args.url or tofu_output("litellm_url", args.tf_dir)).rstrip("/")
    key = args.key or tofu_output("litellm_api_key", args.tf_dir)
    levels = [int(item.strip()) for item in args.concurrency_levels.split(",") if item.strip()]
    selected_models = {item.strip().lower() for item in args.models.split(",") if item.strip()}
    headers = {"Authorization": f"Bearer {key}"}
    timeout = httpx.Timeout(10.0, read=args.timeout, write=args.timeout, pool=args.timeout)
    limits = httpx.Limits(max_connections=max(levels) + 4, max_keepalive_connections=max(levels) + 4)
    rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits) as client:
        models_response = await client.get(f"{url}/v1/models")
        print(f"models HTTP {models_response.status_code}: {models_response.text[:500]}")
        models_response.raise_for_status()

        if "whisper" in selected_models:
            audio_path = Path(args.audio)
            if not audio_path.exists():
                print(f"Audio file not found: {audio_path}", file=sys.stderr)
                return 2
            for level in levels:
                rows.append(
                    await run_level(
                        "whisper",
                        level,
                        args.requests,
                        lambda: audio_once(client, url, args.audio_model, audio_path),
                    )
                )

        if "borealis" in selected_models:
            for level in levels:
                rows.append(
                    await run_level(
                        "borealis",
                        level,
                        args.requests,
                        lambda: chat_once(client, url, args.chat_model, args.prompt, args.max_tokens),
                    )
                )

    print_summary(rows)
    if args.json:
        print("\n" + json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if all(row["errors"] == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
