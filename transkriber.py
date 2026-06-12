"""
Transkribering med NB-Whisper
Lokalt kjørende modell – ingen data sendes eksternt.

Bruk:
  python transkriber.py <lydfil> [--modell small|medium|large] [--tidsstempler]
"""

import argparse
import logging
import os
import sys

# Sett offline-modus FØR transformers/huggingface_hub importeres,
# slik at ingen kall gjøres mot HuggingFace Hub etter at modellen er cachet.
# os.environ["HF_HUB_OFFLINE"] = "1"

from pathlib import Path
from transformers import pipeline
import torch

# Dempe støyende transformers-advarsler
logging.getLogger("transformers").setLevel(logging.ERROR)


MODELLER = {
    "tiny":   "NbAiLab/nb-whisper-tiny",
    "base":   "NbAiLab/nb-whisper-base",
    "small":  "NbAiLab/nb-whisper-small",
    "medium": "NbAiLab/nb-whisper-medium",
    "large":  "NbAiLab/nb-whisper-large",
}


def velg_enhet() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def transkriber(lydfil: str, modellnavn: str = "small", tidsstempler: bool = False):
    lydfil = Path(lydfil)
    if not lydfil.exists():
        print(f"Feil: finner ikke filen '{lydfil}'", file=sys.stderr)
        sys.exit(1)

    modell_id = MODELLER.get(modellnavn)
    if not modell_id:
        print(f"Ukjent modell '{modellnavn}'. Velg: {', '.join(MODELLER)}", file=sys.stderr)
        sys.exit(1)

    enhet = velg_enhet()
    print(f"Laster modell: {modell_id}  (enhet: {enhet})")

    asr = pipeline(
        "automatic-speech-recognition",
        model=modell_id,
        device=enhet,
        ignore_warning=True,
    )

    print(f"Transkriberer: {lydfil.name} ...")
    resultat = asr(
        str(lydfil),
        chunk_length_s=28,
        return_timestamps="word" if tidsstempler else True,
        generate_kwargs={
            "num_beams": 5,
            "task": "transcribe",
            "language": "no",
        },
    )

    print("\n--- TRANSKRIPSJON ---\n")
    print(resultat["text"])

    if tidsstempler and "chunks" in resultat:
        print("\n--- TIDSSTEMPLER ---\n")
        for chunk in resultat["chunks"]:
            start, slutt = chunk["timestamp"]
            tekst = chunk["text"].strip()
            if tekst:
                print(f"[{start:6.1f}s – {slutt:6.1f}s]  {tekst}")

    return resultat


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transkriber lydfil med NB-Whisper")
    parser.add_argument("lydfil", help="Sti til lydfil (.mp3, .wav, .m4a, ...)")
    parser.add_argument(
        "--modell",
        choices=list(MODELLER.keys()),
        default="small",
        help="Modellstørrelse (standard: small)",
    )
    parser.add_argument(
        "--tidsstempler",
        action="store_true",
        help="Vis tidsstempler per ord",
    )
    args = parser.parse_args()
    transkriber(args.lydfil, args.modell, args.tidsstempler)
