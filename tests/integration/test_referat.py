"""
Integrasjonstest for møtereferat-generering (krever Ollama).

Bruk:
  pytest -m integration
  python tests/integration/test_referat.py
  python tests/integration/test_referat.py --fil testdata/transkription.md
  python tests/integration/test_referat.py --fil testdata/conversation_nb.md --modell qwen3:8b
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from ollama.klient import stream_tokens, MODELL
from prompts import SYSTEM_REFERAT, BRUKER_REFERAT
from prompts.normalisering import normaliser_til_bokmal


async def _kjoer_referat(fil: Path, modell: str | None, debug: bool = False) -> None:
    transkripsjon = fil.read_text(encoding="utf-8")
    prompt = BRUKER_REFERAT.format(transkripsjon=normaliser_til_bokmal(transkripsjon))

    print(f"Fil:    {fil}  ({len(transkripsjon.split())} ord)")
    print(f"Modell: {modell or MODELL}")
    print("─" * 60)

    if debug:
        # Raw mode: dump chunks directly from Ollama to diagnose state machine
        import httpx as _httpx
        from ollama.klient import URL, _OPTIONS
        from prompts import SYSTEM_REFERAT as _SYS
        import json as _json
        chunk_count = 0
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(10.0, read=300.0)) as klient:
            async with klient.stream("POST", f"{URL}/api/generate", json={
                "model": modell or MODELL, "system": _SYS, "prompt": prompt,
                "stream": True, "think": False, "options": _OPTIONS,
            }) as resp:
                async for linje in resp.aiter_lines():
                    if not linje:
                        continue
                    chunk = _json.loads(linje)
                    tok = chunk.get("response", "")
                    if tok:
                        chunk_count += 1
                        if chunk_count <= 30:
                            print(f"[chunk {chunk_count:03d}] {repr(tok)}")
                    if chunk.get("done"):
                        print(f"... {chunk_count} chunks total")
                        break
        return

    ttft = None
    full_tekst = ""
    t_start = time.perf_counter()

    async for token, ferdig, tekst in stream_tokens(SYSTEM_REFERAT, prompt, modell):
        if ferdig:
            full_tekst = tekst
        elif token:
            if ttft is None:
                ttft = time.perf_counter() - t_start
            print(token, end="", flush=True)

    total = time.perf_counter() - t_start
    print(f"\n{'─' * 60}")
    print(f"TTFT: {f'{ttft:.2f}s' if ttft else '—'}  |  Total: {total:.1f}s  |  Tegn: {len(full_tekst)}")

    if not full_tekst.strip() or full_tekst.strip() in ("**", ""):
        print("\n⚠️  Tom eller ugyldig output — sjekk <think>-filtrering")
        sys.exit(1)
    else:
        print("✅ OK")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_referat_conversation_nb():
    ROOT = Path(__file__).parent.parent.parent
    fil = ROOT / "testdata" / "conversation_nb.md"
    await _kjoer_referat(fil, modell=None, debug=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test møtereferat-generering")
    parser.add_argument(
        "--fil",
        type=Path,
        default=Path("testdata/transkription.md"),
        help="Transkripsjonsfil (standard: testdata/transkription.md)",
    )
    parser.add_argument(
        "--modell",
        default=None,
        help=f"Ollama-modell (standard: {MODELL})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Dump råe chunks frå Ollama (diagnose <think>-filtrering)",
    )
    args = parser.parse_args()

    if not args.fil.exists():
        print(f"Feil: finner ikke {args.fil}")
        sys.exit(1)

    asyncio.run(_kjoer_referat(args.fil, args.modell, args.debug))


if __name__ == "__main__":
    main()
