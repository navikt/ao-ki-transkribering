"""
Optimaliserings-benchmark for møtereferat-generering.

Tester ein matrise av LLM-innstillingar (num_ctx, num_predict, temperature)
mot alle tre modellane og rapporterer kva kombinasjon som er raskast.

Kallar Ollama direkte – krev ikkje at server.py køyrer.

Bruk:
  python benchmark_optimalisering.py
  python benchmark_optimalisering.py --ollama http://localhost:11434
  python benchmark_optimalisering.py --modeller qwen3:8b qwen3.5:9b
"""

import argparse
import json
import time
from dataclasses import dataclass, field

import httpx

# ---------------------------------------------------------------------------
# Modeller og transkripsjon
# ---------------------------------------------------------------------------

STANDARD_MODELLER = ["qwen3.6:35b", "qwen3:8b", "qwen3.5:9b"]

TRANSKRIPSJON = """\
Veileder: Hei, velkommen. Vi har satt av tid i dag for å snakke om din situasjon og hva vi kan gjøre
fremover. Kan du fortelle litt om hvor du er nå?

Bruker: Ja, altså jeg har vært sykmeldt i fire måneder nå. Ryggen har vært veldig problematisk.
Jeg jobbet som lagermedarbeider, men det er ikke mulig å gå tilbake til det.

Veileder: Forstår. Hva tenker du selv om veien videre? Er det noe du har lyst til å jobbe med?

Bruker: Jeg har alltid vært interessert i data og IT. Tenkte kanskje det var mulig å ta noe kurs
eller utdanning innen det. Jeg vet ikke om det er realistisk.

Veileder: Det høres absolutt realistisk ut. Vi kan se på arbeidsavklaringspenger som kan gi deg
inntekt mens du er i en omstillingsprosess. Vi bør også kartlegge hva slags IT-utdanning som
passer til din bakgrunn og dine mål.

Bruker: Det hadde vært veldig fint. Hvor lang tid tar det å søke om AAP?

Veileder: Saksbehandlingstiden er normalt fire til åtte uker. Vi kan starte søknadsprosessen i dag.
Jeg vil også sende deg en lenke til arbeidsplassen.nav.no der du kan se aktuelle kurs.

Bruker: Takk, det høres bra ut.

Veileder: Vi avtaler et nytt møte om tre uker for å følge opp søknaden og se på kursmuligheter.
"""

# Eksakt same prompts som server.py
_SYSTEM = """\
SPRÅK: Skriv ALLTID på bokmål. Aldri bruk nynorsk eller dialektformer, uansett hva transkripsjonene inneholder.

Du er en assistent som hjelper NAV-veiledere med å skrive samtalereferater etter § 14a-møter.
Referatet skrives inn i Aktivitetsplanen i Modia og deles direkte med brukeren.

VIKTIGSTE REGEL – INGEN HALLUSINASJONER:
Skriv BARE informasjon som faktisk finnes i transkripsjonene.
Dersom en seksjon ikke har relevant innhold fra samtalen, skriv «—» for den seksjonen.

KAN IKKE SKRIVES (§15-grensen):
- Helsediagnoser eller sykdomshistorikk
- Subjektive vurderinger av brukerens personlighet eller atferd

STIL:
- Skriv i vi/du-form («Vi avtalte at du …»)
- Klart og enkelt språk
- Kortfattet og faktabasert
- Svar BARE med selve referatteksten, ingen innledende kommentarer"""

_BRUKER = """\
Lag et samtalereferat basert på følgende transkripsjon.

**Bakgrunn for møtet**
[Hva var formålet med møtet]

**Hva vi snakket om**
[Arbeidsrettet innhold: mål, muligheter, ytelser, tiltak]

**Avtaler**
[Konkrete avtaler. Hvis ingen: «—»]

**Neste møte**
[Kun hvis dato ble avtalt. Hvis ikke: «—»]

TRANSKRIPSJON:
""" + TRANSKRIPSJON

# ---------------------------------------------------------------------------
# Optimaliserings-konfigurasjonar å teste
# ---------------------------------------------------------------------------

@dataclass
class Konfig:
    namn: str
    num_ctx: int
    num_predict: int   # -1 = ubegrensa
    temperature: float
    extra: dict = field(default_factory=dict)

    def til_options(self) -> dict:
        opts = {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
        }
        if self.num_predict != -1:
            opts["num_predict"] = self.num_predict
        opts.update(self.extra)
        return opts


KONFIGURASJONAR: list[Konfig] = [
    # Baseline (tidlegare standard)
    Konfig("ctx8192",              num_ctx=8192, num_predict=-1,  temperature=0.25),

    # Redusert kontekst
    Konfig("ctx4096",              num_ctx=4096, num_predict=-1,  temperature=0.25),
    Konfig("ctx2048",              num_ctx=2048, num_predict=-1,  temperature=0.25),

    # Avgrens output (referater treng sjeldan >400 token)
    Konfig("ctx4096+max400",       num_ctx=4096, num_predict=400, temperature=0.25),
    Konfig("ctx2048+max400",       num_ctx=2048, num_predict=400, temperature=0.25),

    # Lågare temperatur (meir deterministisk, litt raskare sampling)
    Konfig("ctx4096+temp0.1",      num_ctx=4096, num_predict=-1,  temperature=0.1),

    # Kombinasjon: lite kontekst + avgrensa output + lav temp
    Konfig("ctx2048+max400+t0.1",  num_ctx=2048, num_predict=400, temperature=0.1),
]

# ---------------------------------------------------------------------------
# Benchmark-logikk
# ---------------------------------------------------------------------------

def _kall_ollama(ollama_url: str, modell: str, konfig: Konfig) -> dict:
    """
    Kallar Ollama /api/generate direkte med streaming.
    Returnerer ttft_s, total_s, tokens, tps, feil.
    """
    ttft_s = None
    tokens = 0
    feil = None

    t_start = time.perf_counter()
    try:
        with httpx.stream(
            "POST",
            f"{ollama_url}/api/generate",
            json={
                "model": modell,
                "system": _SYSTEM,
                "prompt": _BRUKER,
                "stream": True,
                "think": False,
                "options": konfig.til_options(),
            },
            timeout=httpx.Timeout(15.0, read=300.0),
        ) as resp:
            resp.raise_for_status()
            for linje in resp.iter_lines():
                if not linje:
                    continue
                try:
                    chunk = json.loads(linje)
                except json.JSONDecodeError:
                    continue

                token = chunk.get("response", "")
                if token:
                    if ttft_s is None:
                        ttft_s = time.perf_counter() - t_start
                    tokens += 1

                if chunk.get("done"):
                    break

    except httpx.ConnectError:
        feil = "Ollama ikkje tilgjengeleg"
    except httpx.HTTPStatusError as e:
        feil = f"HTTP {e.response.status_code}"
    except Exception as e:
        feil = str(e)[:60]

    total_s = time.perf_counter() - t_start
    tps = tokens / total_s if total_s > 0 and tokens > 0 else 0.0

    return {
        "ttft_s":   round(ttft_s, 2) if ttft_s is not None else None,
        "total_s":  round(total_s, 1),
        "tokens":   tokens,
        "tps":      round(tps, 1),
        "feil":     feil,
    }


# ---------------------------------------------------------------------------
# Rapportering
# ---------------------------------------------------------------------------

def _print_per_modell(modell: str, rader: list[dict]):
    """Skriv ut resultat for éin modell, sortert etter total_s."""
    print(f"\n  {'Konfig':<24}  {'TTFT':>7}  {'Total':>7}  {'Tokens':>6}  {'tok/s':>6}  Status")
    print(f"  {'─'*24}  {'─'*7}  {'─'*7}  {'─'*6}  {'─'*6}  {'─'*6}")

    for r in sorted(rader, key=lambda x: x["total_s"]):
        if r["feil"]:
            print(f"  {r['konfig']:<24}  {'—':>7}  {r['total_s']:>6.1f}s  {'—':>6}  {'—':>6}  ❌ {r['feil']}")
        else:
            medal = " 🏆" if r == sorted(rader, key=lambda x: x["total_s"])[0] else ""
            ttft = f"{r['ttft_s']:.2f}s" if r["ttft_s"] is not None else "—"
            print(
                f"  {r['konfig']:<24}  {ttft:>7}  {r['total_s']:>6.1f}s  "
                f"{r['tokens']:>6}  {r['tps']:>6.1f}  ✅{medal}"
            )


def _print_samandrag(alle: list[dict]):
    """Beste konfig per modell + overordna vinnar."""
    print("\n" + "═" * 65)
    print("  Samandrag — beste konfig per modell")
    print("═" * 65)

    vinnarar = []
    for modell in {r["modell"] for r in alle}:
        vellykkede = [r for r in alle if r["modell"] == modell and not r["feil"]]
        if not vellykkede:
            print(f"  {modell:<20}  ingen vellykkede kjøyringar")
            continue
        best = min(vellykkede, key=lambda r: r["total_s"])
        vinnarar.append(best)
        print(
            f"  {modell:<20}  {best['konfig']:<24}  "
            f"{best['total_s']:.1f}s  {best['tps']:.1f} tok/s"
        )

    if vinnarar:
        overordna = min(vinnarar, key=lambda r: r["total_s"])
        print(f"\n  🏆  Raskast totalt: {overordna['modell']} med «{overordna['konfig']}» — {overordna['total_s']}s\n")


# ---------------------------------------------------------------------------
# Hovudprogram
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Optimaliserings-benchmark for møtereferat")
    parser.add_argument(
        "--modeller", nargs="+", default=STANDARD_MODELLER, metavar="MODELL",
    )
    parser.add_argument(
        "--ollama", default="http://localhost:11434", metavar="URL",
        help="Ollama base-URL (standard: http://localhost:11434)"
    )
    parser.add_argument(
        "--konfigurasjonar", nargs="+", metavar="NAMN",
        help="Køyr berre utvalde konfigurasjonar (t.d. ctx4096 ctx2048+max400)"
    )
    args = parser.parse_args()

    konfigurasjonar = (
        [k for k in KONFIGURASJONAR if k.namn in args.konfigurasjonar]
        if args.konfigurasjonar else KONFIGURASJONAR
    )

    total_kjoyringar = len(args.modeller) * len(konfigurasjonar)
    print(f"\nBenchmark: {len(args.modeller)} modell(ar) × {len(konfigurasjonar)} konfigurasjonar = {total_kjoyringar} kjøyringar")
    print(f"Ollama: {args.ollama}\n")

    alle_resultat: list[dict] = []

    for modell in args.modeller:
        print(f"─── {modell} {'─' * (50 - len(modell))}")
        modell_rader = []

        for konfig in konfigurasjonar:
            print(f"  {konfig.namn:<24} …", end="", flush=True)
            res = _kall_ollama(args.ollama, modell, konfig)

            if res["feil"]:
                print(f" ❌ {res['feil']}")
            else:
                print(f" {res['total_s']:.1f}s  (TTFT {res['ttft_s']:.2f}s, {res['tps']} tok/s)")

            rad = {"modell": modell, "konfig": konfig.namn, **res}
            modell_rader.append(rad)
            alle_resultat.append(rad)

        _print_per_modell(modell, modell_rader)

    _print_samandrag(alle_resultat)


if __name__ == "__main__":
    main()
