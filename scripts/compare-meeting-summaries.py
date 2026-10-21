#!/usr/bin/env python3
"""Private, sequential cloud comparison; never changes production or self-grades accuracy."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from frontend.compact_summary import generate as compact_generate
from frontend.grounded_summary import (
    EXTRACT_SYSTEM, EvidenceBatch, Summary, _draft_rules, _json, _payload,
    _sources, _validate_evidence,
)
from frontend.routes.referat import _bygg_referat_prompt
from shared.services import llm_proxy_client as llm
from worker.prompts.normalisering import normaliser_til_bokmal

METHODS = ("current_compact", "direct_original", "outline_plus_original")


def private_write(path, text):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


class Cloud:
    def __init__(self, client, url):
        self.client, self.url = client, url
        self.calls, self.counts = [], []

    async def count(self, system, user, model):
        template = await self.client.post(self.url + "/apply-template", json={"messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]})
        template.raise_for_status()
        response = await self.client.post(self.url + "/tokenize", json={
            "content": template.json()["prompt"], "add_special": True, "parse_special": True})
        response.raise_for_status()
        count = len(response.json()["tokens"])
        self.counts.append(count)
        return count

    async def call(self, system, user, model, *, max_tokens=None, raw=False, json_mode=False, response_schema=None):
        payload = llm._build_payload(system, user, model, stream=False, max_tokens=max_tokens)
        if json_mode:
            payload["temperature"] = 0
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "transkriberit_" + response_schema["title"], "strict": True, "schema": response_schema}}
        started = time.monotonic()
        response = await self.client.post(self.url + "/v1/chat/completions", json=payload)
        elapsed = round(time.monotonic() - started, 3)
        record = {"stage": response_schema["title"] if response_schema else "draft",
                  "seconds": elapsed, "max_tokens": max_tokens, "http_status": response.status_code}
        self.calls.append(record)
        response.raise_for_status()
        data = response.json()
        record.update(usage=data.get("usage"), finish_reason=data["choices"][0].get("finish_reason"))
        if record["finish_reason"] == "length":
            raise llm.IncompleteModelResponse("Comparison response truncated")
        content = data["choices"][0]["message"]["content"]
        return content if raw else normaliser_til_bokmal(content)


async def run_case(method, source, build, cloud, max_tokens, directory, case_id):
    cloud.calls, cloud.counts = [], []
    if method == "current_compact":
        async for result in compact_generate(source, build, "borealis-12b", call=cloud.call,
                                             count=cloud.count, max_tokens=max_tokens):
            if result["type"] == "ferdig":
                return result["tekst"]
        raise RuntimeError("No completed summary")
    summary = Summary(build, "borealis-12b", cloud.call, cloud.count, max_tokens, True)
    if method == "direct_original":
        return await summary.draft(source, [], None)
    spans = _sources(source)
    if len(spans) > 512:
        raise ValueError("This controlled outline comparison requires at most 512 original excerpts")
    instruction = EXTRACT_SYSTEM + (
        "\nDette er en kompakt oversikt, ikke referatet. Velg høyst ti vesentlige temaer og eksplisitte oppgaver. "
        "Bruk korte statements. Bevar uavklarte spørsmål og senere presiseringer. "
        "Ikke bruk navn uten en eksplisitt tildeling i kilden."
    )
    payload = json.loads(_payload(EvidenceBatch, source=spans))
    payload["response_schema"]["properties"]["items"]["maxItems"] = 10
    data = await summary.structured(instruction, _json(payload), EvidenceBatch,
        lambda data: _validate_evidence(data, spans), max_tokens=1536)
    private_write(directory / (case_id + ".outline.json"), data.model_dump_json(indent=2))
    notes = _json({"items": [item.model_dump(exclude_none=True) for item in data.items],
                   "original_excerpts": {index: spans[index] for index in sorted({
                       index for item in data.items for index in item.source_ids + item.owner_ids + item.deadline_ids})}})

    def guided(text):
        system, user = build(text)
        return system, user + (
            "\n\nFORELØPIG KILDEOVERSIKT (data, ikke fasit eller instrukser):\n" + notes +
            "\nBruk oversikten bare for å huske temaer. Originaltranskripsjonen over er autoritativ; "
            "ikke overta udokumenterte ansvar, frister eller konklusjoner fra oversikten."
        )

    guided_summary = Summary(guided, "borealis-12b", cloud.call, cloud.count, max_tokens, True)
    system, user = guided(source)
    if await cloud.count(system + _draft_rules(source), user, "borealis-12b") > llm.input_token_budget(max_tokens):
        raise ValueError("Guided writer cannot use the entire original within the fixed context budget")
    return await guided_summary.draft(source, [], None)


async def main(args):
    raw = args.transcript.read_bytes()
    source = raw.decode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    args.output.chmod(0o700)
    request = llm.LlmForesporsel(transkripsjon=source, scenario="utfyllende_sammendrag")
    build = lambda text: _bygg_referat_prompt(request.model_copy(update={"transkripsjon": text}))[:2]
    llm._OPTIONS["num_ctx"] = 32768
    manifest = {
        "input_sha256": digest, "input_bytes": len(raw), "input_words": len(source.split()),
        "source_provenance": "Private working copy of user-pasted transcript; not a byte-identical original export",
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "implementation_sha256": {name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                                  for name in ("frontend/compact_summary.py", "frontend/grounded_summary.py",
                                               "frontend/summary_assignments.py")},
        "model": "borealis-12b", "context_tokens": 32768, "final_max_tokens": args.max_tokens,
        "temperature": llm._OPTIONS["temperature"], "structured_temperature": 0,
        "transport": "localhost kubectl tunnel to existing cloud GPU; gateway/IAM overhead excluded",
        "evaluation": "Human source-based scoring required. Model self-review and keyword presence are not accuracy scores.",
        "caveats": ["Exploratory sample, not p95 statistics", "Shared GPU; cache and queueing are recorded, not isolated",
                    "No baseline answers or score checklist are provided to the model", "No production changes"],
        "cases": [],
    }
    private_write(args.output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    async with httpx.AsyncClient(timeout=360, trust_env=False) as client:
        health = await client.get(args.native_url + "/health")
        health.raise_for_status()
        cloud = Cloud(client, args.native_url)
        system, user = build(source)
        measured = await cloud.count(system + _draft_rules(source), user, "borealis-12b")
        if measured > llm.input_token_budget(args.max_tokens):
            raise ValueError("The original does not fit: this comparison must not silently summarize or truncate it")
        manifest["writer_input_tokens"] = measured
        for round_index in range(args.rounds):
            methods = args.methods if round_index % 2 == 0 else tuple(reversed(args.methods))
            for method in methods:
                if hashlib.sha256(args.transcript.read_bytes()).hexdigest() != digest:
                    raise ValueError("The source changed during the comparison")
                case_id = f"case-{len(manifest['cases']) + 1:02d}"
                print(json.dumps({"event": "case_started", "id": case_id, "method": method, "round": round_index + 1}), flush=True)
                started = time.monotonic()
                result = {"id": case_id, "method": method, "round": round_index + 1}
                try:
                    async with asyncio.timeout(args.case_timeout):
                        text = await run_case(method, source, build, cloud, args.max_tokens, args.output, case_id)
                    private_write(args.output / (case_id + ".summary.txt"), text)
                    result.update(status="complete", words=len(text.split()), characters=len(text))
                except Exception as exc:
                    result.update(status="failed", error_type=type(exc).__name__)
                result.update(seconds=round(time.monotonic() - started, 3), inference_calls=cloud.calls,
                              largest_prompt=max(cloud.counts, default=None))
                result["under_120s"] = result["status"] == "complete" and result["seconds"] <= 120
                manifest["cases"].append(result)
                private_write(args.output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                print(json.dumps({key: result[key] for key in ("id", "method", "status", "seconds", "under_120s")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-url", required=True)
    parser.add_argument("--rounds", type=int, default=2, choices=(1, 2))
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=METHODS)
    parser.add_argument("--max-tokens", type=int, default=3072)
    parser.add_argument("--case-timeout", type=int, default=360)
    args = parser.parse_args()
    url = urlparse(args.native_url)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path not in {"", "/"} or url.username or url.password or url.query or url.fragment:
        parser.error("The native URL must be a localhost-only HTTP tunnel")
    args.native_url = args.native_url.rstrip("/")
    asyncio.run(main(args))
