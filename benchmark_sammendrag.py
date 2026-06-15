"""
Benchmark av /sammendrag/stream mot testtranskripsjoner.

Måler:
  - Tid til første token (TTFT)
  - Total genereringstid
  - Antall streamede tokens/chunks
  - Tokens per sekund

Bruk:
  python benchmark_sammendrag.py
  python benchmark_sammendrag.py --modell qwen3.6:35b --runder 3
  python benchmark_sammendrag.py --url http://127.0.0.1:8765 --vis-sammendrag
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


STANDARD_FILER = [
    Path("testdata/conversation_en.md"),
    Path("testdata/conversation_nb.md"),
]


def _stream_sammendrag(url: str, transkripsjon: str, modell: str | None) -> dict:
    endpoint = f"{url.rstrip('/')}/sammendrag/stream"
    payload: dict[str, str] = {"transkripsjon": transkripsjon}
    if modell:
        payload["modell"] = modell

    ttft_s = None
    tokens = 0
    ferdig_tekst = ""
    brukt_modell = modell
    feil = None
    start = time.perf_counter()

    try:
        with httpx.stream(
            "POST",
            endpoint,
            json=payload,
            timeout=httpx.Timeout(15.0, read=600.0),
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            for linje in resp.iter_lines():
                if not linje.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(linje[6:])
                except json.JSONDecodeError:
                    continue

                meldingstype = chunk.get("type")
                if meldingstype == "start":
                    brukt_modell = chunk.get("modell") or brukt_modell
                elif meldingstype == "token":
                    if ttft_s is None:
                        ttft_s = time.perf_counter() - start
                    tokens += 1
                elif meldingstype == "ferdig":
                    ferdig_tekst = chunk.get("tekst", "")
                    brukt_modell = chunk.get("modell") or brukt_modell
                elif meldingstype == "feil":
                    feil = chunk.get("melding", "ukjent feil")
                    break
    except httpx.ConnectError:
        feil = "Kan ikke nå serveren. Start appen med uvicorn server:app --host 127.0.0.1 --port 8765"
    except httpx.HTTPStatusError as e:
        feil = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        feil = str(e)

    total_s = time.perf_counter() - start
    return {
        "ttft_s": round(ttft_s, 2) if ttft_s is not None else None,
        "total_s": round(total_s, 1),
        "tokens": tokens,
        "tps": round(tokens / total_s, 1) if total_s > 0 and tokens else 0.0,
        "tekst": ferdig_tekst,
        "modell": brukt_modell or "",
        "feil": feil,
    }


def _print_tabell(resultater: list[dict]) -> None:
    filbredde = max(4, *(len(r["fil"]) for r in resultater))
    modellbredde = max(6, *(len(r.get("modell") or "") for r in resultater))
    header = (
        f"{'Fil':<{filbredde}}  {'Modell':<{modellbredde}}  "
        f"{'Ord':>5}  {'TTFT':>7}  {'Total':>7}  {'Tokens':>6}  {'tok/s':>6}  Status"
    )
    print("\n" + header)
    print("-" * len(header))

    for r in resultater:
        if r["feil"]:
            ttft = "-"
            tokens = "-"
            tps = "-"
            status = f"FEIL: {r['feil'][:80]}"
        else:
            ttft = f"{r['ttft_s']:.2f}s" if r["ttft_s"] is not None else "-"
            tokens = str(r["tokens"])
            tps = f"{r['tps']:.1f}"
            status = "OK"
        print(
            f"{r['fil']:<{filbredde}}  {r.get('modell',''):<{modellbredde}}  "
            f"{r['ord']:>5}  {ttft:>7}  {r['total_s']:>6.1f}s  "
            f"{tokens:>6}  {tps:>6}  {status}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark /sammendrag/stream")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765",
        help="Base-URL til transkriberingsserveren",
    )
    parser.add_argument(
        "--modell",
        default=None,
        help="Overstyr Ollama-modell. Hvis utelatt brukes serverens standard.",
    )
    parser.add_argument(
        "--runder",
        type=int,
        default=1,
        help="Antall runder per fil",
    )
    parser.add_argument(
        "--filer",
        nargs="+",
        type=Path,
        default=STANDARD_FILER,
        help="Transkripsjonsfiler som skal testes",
    )
    parser.add_argument(
        "--vis-sammendrag",
        action="store_true",
        help="Skriv ut generert sammendrag etter hver fil",
    )
    args = parser.parse_args()

    resultater: list[dict] = []
    print(f"Tester {len(args.filer)} fil(er), {args.runder} runde(r) hver")
    print(f"Server: {args.url}")
    if args.modell:
        print(f"Modell: {args.modell}")

    for fil in args.filer:
        transkripsjon = fil.read_text(encoding="utf-8")
        ord_antall = len(transkripsjon.split())
        for runde in range(1, args.runder + 1):
            suffix = f" runde {runde}/{args.runder}" if args.runder > 1 else ""
            print(f"  Tester {fil}{suffix} ({ord_antall} ord) ...", end="", flush=True)
            res = _stream_sammendrag(args.url, transkripsjon, args.modell)
            rad = {
                **res,
                "fil": str(fil),
                "ord": ord_antall,
                "runde": runde,
            }
            resultater.append(rad)
            if res["feil"]:
                print(f" FEIL: {res['feil'][:80]}")
            else:
                print(f" {res['total_s']:.1f}s, TTFT {res['ttft_s']:.2f}s, {res['tokens']} tokens")
            if args.vis_sammendrag and res["tekst"]:
                print(f"\n--- Sammendrag: {fil} ---\n{res['tekst']}\n")

    _print_tabell(resultater)


if __name__ == "__main__":
    main()
