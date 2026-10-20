#!/usr/bin/env python3
"""Exercise Borealis grounding with synthetic data, never reference transcripts."""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from frontend.grounded_summary import _draft_rules, generate
from frontend.long_transcript import _size
from frontend.routes.referat import _bygg_referat_prompt
from shared.services import llm_proxy_client as llm
from worker.prompts.normalisering import normaliser_til_bokmal

LINES = [
    "Vegard: Vi foreslår intern simulering, men ingen test er godkjent ennå.",
    "Marte: Behovet for samtykke og PVK er uavklart og må undersøkes nærmere.",
    "Mitra: Vi har ikke konkludert om behandlingen innebærer biometriske opplysninger.",
    "Gro: Jeg lager første utkast til ledernotatet. Ingen frist er satt her.",
    "Vegard: Roman og jeg vurderer hvordan foreløpige kostnader kan presenteres, ikke personvernvurderingen.",
    "Fredrik: Veilederne trenger forståelig informasjon om databehandling og KI-kompetanse.",
    "Roman: Vi planlegger en teknisk demonstrasjon for ledelsen. Kostnadene er usikre estimater.",
    "Gro: Simulering er første forslag. En eventuell pilot med brukere krever videre avklaringer.",
]

# Independent lexical coverage for this known synthetic fixture, not a factual oracle.
EXPECTED_MARKERS = ("Gro", "Vegard", "Roman", "kompetanse", "PVK", "samtykke",
                    "biometr", "kostnad", "demonstrasjon", "simuler")


async def main(args):
    source = "Syntetisk testmøte, ikke en virkelig samtale.\n" + "\n".join(
        line for line in LINES for _ in range(90 if args.long else 1)
    )
    llm._OPTIONS["num_ctx"] = 32768
    request = llm.LlmForesporsel(transkripsjon=source, scenario="utfyllende_sammendrag")
    build = lambda text: _bygg_referat_prompt(request.model_copy(update={"transkripsjon": text}))[:2]
    calls, counts = [], []
    injected = False
    repaired = False
    async with httpx.AsyncClient(timeout=300, trust_env=False) as native:
        async def count(system, user, model):
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            template = await native.post(args.native_url + "/apply-template", json={"messages": messages})
            template.raise_for_status()
            response = await native.post(args.native_url + "/tokenize", json={
                "content": template.json()["prompt"], "add_special": True, "parse_special": True,
            })
            response.raise_for_status()
            result = len(response.json()["tokens"])
            counts.append(result)
            return result

        async def call(system, user, model, *, max_tokens=None, raw=False, json_mode=False, response_schema=None):
            payload = llm._build_payload(system, user, model, stream=False, max_tokens=max_tokens)
            if json_mode:
                payload["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "transkriberit_" + response_schema["title"], "strict": True, "schema": response_schema,
                }} if response_schema is not None else {"type": "json_object"}
                payload["temperature"] = 0
            started = time.monotonic()
            response = await native.post(args.native_url + "/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            calls.append({"json_mode": json_mode, "max_tokens": max_tokens, "usage": data.get("usage"),
                          "elapsed_s": round(time.monotonic() - started, 2)})
            if data["choices"][0].get("finish_reason") == "length":
                raise llm.IncompleteModelResponse("Synthetic response truncated")
            text = data["choices"][0]["message"]["content"]
            if args.debug and json_mode:
                print(json.dumps({"synthetic_model_json": text}, ensure_ascii=False), flush=True)
            return text if raw else normaliser_til_bokmal(text)

        if args.native_url:
            # This is a localhost kubectl tunnel to the cloud GPU, not a local model.
            expected = await count("Svar kort.", "Svar bare OK.", "borealis-12b")
            probe = await native.post(args.native_url + "/v1/chat/completions", json={
                "model": "borealis-12b", "messages": [{"role": "system", "content": "Svar kort."},
                    {"role": "user", "content": "Svar bare OK."}], "max_tokens": 1, "temperature": 0,
            })
            probe.raise_for_status()
            assert probe.json()["usage"]["prompt_tokens"] == expected, "Template count differs from actual inference"
        else:
            count, call = llm.count_tokens, llm.kall
        if args.count_only:
            system, user = build(source)
            system += _draft_rules(source)
            actual = await count(system, user, "borealis-12b")
            print(json.dumps({"source_characters": len(source), "tokenizer_input_tokens": actual,
                "estimated_input_tokens": _size(system, user), "input_budget": llm.input_token_budget(3072),
                "whole_transcript_fits": actual <= llm.input_token_budget(3072) if actual is not None else None}), flush=True)
            return
        backend_call = call

        async def checked_call(system, user, model, **options):
            nonlocal injected
            if args.inject_bad_draft and not options.get("json_mode") and not injected:
                injected = True
                return "Det ble enighet om å starte testen uten PVK. Vegard skal avklare PVK."
            return await backend_call(system, user, model, **options)

        started = time.monotonic()
        async for result in generate(source, build, "borealis-12b", call=checked_call, count=count, max_tokens=3072):
            if result["type"] == "progress":
                repaired = repaired or result["melding"].startswith(("Retter", "Fjerner"))
                print(json.dumps(result, ensure_ascii=False), flush=True)
            else:
                if args.inject_bad_draft:
                    assert repaired, "The deliberately false draft was accepted without correction"
                missing = [marker for marker in EXPECTED_MARKERS if marker.casefold() not in result["tekst"].casefold()]
                print(json.dumps({"status": "missing_fixture_topics" if missing else "checks_passed",
                    "missing_fixture_markers": missing, "source_characters": len(source),
                    "elapsed_s": round(time.monotonic() - started, 2), "calls": calls,
                    "largest_counted_prompt": max(counts, default=None), "synthetic_summary": result["tekst"]}, ensure_ascii=False), flush=True)
                assert not missing, "Known fixture coverage missing: " + ", ".join(missing)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--long", action="store_true")
    parser.add_argument("--count-only", action="store_true", help="Inspect the fitting decision without generating a summary")
    parser.add_argument("--debug", action="store_true", help="Show structured replies for the built-in synthetic fixture only")
    parser.add_argument("--inject-bad-draft", action="store_true", help="Require rejection of a deliberately invented approval and assignment")
    parser.add_argument("--native-url", default="", help="Optional localhost-only kubectl tunnel to the cloud GPU")
    args = parser.parse_args()
    if args.native_url:
        url = urlparse(args.native_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path not in {"", "/"} or url.username or url.password or url.query or url.fragment:
            parser.error("The native test URL must be a localhost-only HTTP tunnel")
        args.native_url = args.native_url.rstrip("/")
    asyncio.run(main(args))
