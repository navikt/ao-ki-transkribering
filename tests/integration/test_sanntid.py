"""
Integrasjonstest for sanntidsmodus (krever faster-whisper-modell).

Bruk:
  pytest -m integration
  python tests/integration/test_sanntid.py
  python tests/integration/test_sanntid.py --fil testdata/king.mp3
  python tests/integration/test_sanntid.py --hastigheit 1.0
"""

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from transkribering.konstanter import SAMPLE_RATE, FRAME_SAMPLES
from transkribering.sanntid import VadBuffer, transkriber_pcm


def les_pcm(lydfil: Path) -> np.ndarray:
    """Konverterer lydfil til 16 kHz mono float32 PCM via ffmpeg."""
    resultat = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(lydfil),
            "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-f", "f32le", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(resultat.stdout, dtype="<f4").copy()


async def simuler(lydfil: Path, hastigheit: float) -> None:
    pcm = les_pcm(lydfil)
    varighet_s = len(pcm) / SAMPLE_RATE
    print(f"Fil:      {lydfil}")
    print(f"Varighet: {varighet_s:.1f}s  ({len(pcm):,} samples)")
    print(f"Hastigheit: {hastigheit:.1f}× (pause {FRAME_SAMPLES / SAMPLE_RATE / hastigheit * 1000:.1f} ms per frame)")
    print("─" * 60)

    buf = VadBuffer()
    prototyper = None
    segment_nr = 0
    t_start = time.perf_counter()

    for offset in range(0, len(pcm), FRAME_SAMPLES):
        chunk = pcm[offset : offset + FRAME_SAMPLES]
        if len(chunk) == 0:
            break

        # Simuler sanntidshastigheit
        await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE / hastigheit)

        pcm_klar = buf.legg_til(chunk)
        if pcm_klar is not None:
            try:
                resultat, prototyper = await asyncio.to_thread(
                    transkriber_pcm, pcm_klar, prototyper
                )
            except FileNotFoundError as e:
                print(f"\n⚠️  {e}")
                print("Kjør først: python konverter_modeller.py")
                sys.exit(1)
            if resultat:
                segment_nr += 1
                elapsed = time.perf_counter() - t_start
                print(f"\n[{elapsed:.1f}s] Segment {segment_nr}:")
                for seg in resultat["segmenter"]:
                    taler = seg.get("taler", "?")
                    print(f"  {taler}  [{seg['start']:.1f}s–{seg['slutt']:.1f}s]  {seg['tekst']}")

    # Flush gjenverande buffer
    rest = buf.flush_alt()
    if rest is not None:
        resultat, _ = await asyncio.to_thread(transkriber_pcm, rest, prototyper)
        if resultat:
            segment_nr += 1
            elapsed = time.perf_counter() - t_start
            print(f"\n[{elapsed:.1f}s] Segment {segment_nr} (slutt):")
            for seg in resultat["segmenter"]:
                taler = seg.get("taler", "?")
                print(f"  {taler}  [{seg['start']:.1f}s–{seg['slutt']:.1f}s]  {seg['tekst']}")

    print(f"\n{'─' * 60}")
    print(f"Ferdig — {segment_nr} segment(ar) transkriberte")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sanntid_tre_stemmer():
    ROOT = Path(__file__).parent.parent.parent
    fil = ROOT / "testdata" / "tre_stemmer_test.wav"
    await simuler(fil, hastigheit=10.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simuleringr sanntidsmodus med lydfil")
    parser.add_argument(
        "--fil",
        type=Path,
        default=Path("testdata/tre_stemmer_test.wav"),
        help="Lydfil (standard: testdata/tre_stemmer_test.wav)",
    )
    parser.add_argument(
        "--hastigheit",
        type=float,
        default=10.0,
        help="Avspillingsfart relativt til sanntid (standard: 10.0×, bruk 1.0 for reell fart)",
    )
    args = parser.parse_args()

    if not args.fil.exists():
        print(f"Feil: finner ikke {args.fil}")
        sys.exit(1)

    asyncio.run(simuler(args.fil, args.hastigheit))


if __name__ == "__main__":
    main()
