"""
Konverter nb-whisper-modeller fra transformers-format til CTranslate2-format
for bruk med faster-whisper (sanntidsmodus).

Modellvektene er identiske – kun inferensmotoren byttes ut (4× raskere).

Kjøres én gang (krever nettverkstilgang for tokenizer-nedlasting):
  python konverter_modeller.py

Konverterte modeller lagres i ./modeller/
"""

import os
import subprocess
import sys
from pathlib import Path

# Midlertidig fjern offline-modus for å laste ned tokenizer-filer
_OFFLINE = os.environ.pop("HF_HUB_OFFLINE", None)

from huggingface_hub import hf_hub_download, snapshot_download

MODELLER = {
    "tiny":   ("NbAiLab/nb-whisper-tiny",   "openai/whisper-tiny"),
    "base":   ("NbAiLab/nb-whisper-base",   "openai/whisper-base"),
    "small":  ("NbAiLab/nb-whisper-small",  "openai/whisper-small"),
    "medium": ("NbAiLab/nb-whisper-medium", "openai/whisper-medium"),
    "large":  ("NbAiLab/nb-whisper-large",  "openai/whisper-large-v3"),
}

TOKENIZER_FILER = ["tokenizer.json", "vocab.json", "merges.txt",
                   "normalizer.json", "added_tokens.json", "special_tokens_map.json"]
UTMAPPE.mkdir(exist_ok=True)

CT2_CONVERTER = Path(sys.executable).parent / "ct2-transformers-converter"


def hent_snapshot_sti(modell_id: str) -> Path:
    """Returnerer lokal snapshots-sti for en allerede nedlastet modell."""
    sti = snapshot_download(repo_id=modell_id, local_files_only=True)
    return Path(sti)


def last_ned_tokenizer(openai_id: str):
    """Sikrer at openai/whisper-{size} tokenizer er cachet lokalt."""
    for fil in TOKENIZER_FILER:
        try:
            hf_hub_download(repo_id=openai_id, filename=fil)
        except Exception:
            pass  # Ikke alle filer eksisterer for alle størrelser


def konverter(navn: str, nb_id: str, openai_id: str) -> Path:
    utsti = UTMAPPE / f"nb-whisper-{navn}"

    if utsti.exists() and (utsti / "model.bin").exists():
        print(f"  ✓ {navn}: allerede konvertert ({utsti})")
        return utsti

    print(f"  ⬇  {navn}: laster ned tokenizer …")
    last_ned_tokenizer(openai_id)

    print(f"  ⚙  {navn}: konverterer til CTranslate2 (int8) …")
    snapshot_sti = hent_snapshot_sti(nb_id)

    resultat = subprocess.run(
        [str(CT2_CONVERTER),
         "--model", str(snapshot_sti),
         "--output_dir", str(utsti),
         "--quantization", "int8",
         "--force"],
        capture_output=True, text=True,
    )
    if resultat.returncode != 0:
        print(f"  ✗ Feil ved konvertering av {navn}:\n{resultat.stderr}", file=sys.stderr)
        return None

    størrelse = sum(f.stat().st_size for f in utsti.rglob("*") if f.is_file())
    print(f"  ✓ {navn}: ferdig → {utsti}  ({størrelse / 1e6:.0f} MB)")
    return utsti


def sjekk_status():
    """Vis hvilke modeller som er konvertert og klare."""
    print("\nStatus konverterte modeller:")
    alle_ok = True
    for navn in MODELLER:
        utsti = UTMAPPE / f"nb-whisper-{navn}"
        ok = utsti.exists() and (utsti / "model.bin").exists()
        print(f"  {'✓' if ok else '✗'} nb-whisper-{navn}")
        if not ok:
            alle_ok = False
    return alle_ok


if __name__ == "__main__":
    kun_manglende = "--alle" not in sys.argv

    print("Konverterer nb-whisper-modeller til CTranslate2 (faster-whisper)…\n")
    print("NB: krever nettverkstilgang for tokenizer-nedlasting.\n")

    feil = []
    for navn, (nb_id, openai_id) in MODELLER.items():
        utsti = UTMAPPE / f"nb-whisper-{navn}"
        if kun_manglende and utsti.exists() and (utsti / "model.bin").exists():
            print(f"  ✓ {navn}: hopper over (allerede konvertert)")
            continue
        sti = konverter(navn, nb_id, openai_id)
        if sti is None:
            feil.append(navn)

    print()
    sjekk_status()

    if feil:
        print(f"\n✗ Disse feilet: {', '.join(feil)}", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nAlle modeller klare for offline sanntidstranskribering.")

    # Gjenopprett offline-modus
    if _OFFLINE:
        os.environ["HF_HUB_OFFLINE"] = _OFFLINE
