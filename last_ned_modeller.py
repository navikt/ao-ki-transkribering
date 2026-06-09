"""
Last ned (eller oppdater) alle NB-Whisper-modeller til lokal cache.

Kjøres manuelt ved første gangs oppsett eller når du ønsker å oppdatere:
  python last_ned_modeller.py

Krever nettverkstilgang. Etter nedlasting fungerer alt offline
(HF_HUB_OFFLINE=1 settes automatisk av transkriber.py og server.py).
"""

import sys
from pathlib import Path
from huggingface_hub import snapshot_download

MODELLER = [
    "NbAiLab/nb-whisper-tiny",    #   ~148 MB
    "NbAiLab/nb-whisper-base",    #   ~295 MB
    "NbAiLab/nb-whisper-small",   #   ~926 MB
    "NbAiLab/nb-whisper-medium",  #  ~2.8 GB
    "NbAiLab/nb-whisper-large",   #  ~5.8 GB
]


def sjekk_status() -> dict[str, bool]:
    """Returnerer hvilke modeller som allerede er cachet (offline-sjekk)."""
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    status = {}
    for modell in MODELLER:
        navn = modell.split("/")[-1]
        try:
            snapshot_download(repo_id=modell, local_files_only=True)
            status[navn] = True
        except Exception:
            status[navn] = False
    return status


def last_ned(kun_manglende: bool = True):
    status = sjekk_status()

    print("Status for lokale modeller:")
    for navn, ok in status.items():
        print(f"  {'✓' if ok else '✗'} {navn}")
    print()

    å_laste = [
        m for m in MODELLER
        if not kun_manglende or not status[m.split("/")[-1]]
    ]

    if not å_laste:
        print("Alle modeller er allerede nedlastet. Ingenting å gjøre.")
        return

    print(f"Laster ned {len(å_laste)} modell(er) …\n")
    for modell in å_laste:
        navn = modell.split("/")[-1]
        print(f"⬇  {navn} …")
        try:
            sti = snapshot_download(repo_id=modell)
            print(f"   ✓ Lagret i: {sti}\n")
        except Exception as e:
            print(f"   ✗ Feil: {e}\n", file=sys.stderr)

    print("Ferdig.")


if __name__ == "__main__":
    force = "--alle" in sys.argv
    if force:
        print("Tvungen nedlasting av alle modeller (--alle)\n")
    last_ned(kun_manglende=not force)
