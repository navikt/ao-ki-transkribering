"""
Transkriberings-webapp – FastAPI backend
Kjør: uvicorn server:app

Arkitektur:
  - Batch-modus:    Transformers pipeline i separat prosess (MPS/CUDA, spawn).
  - Sanntidsmodus:  faster-whisper (CTranslate2) lastet direkte, kjøres via
                    asyncio.to_thread – CTranslate2 slipper GIL under inferens
                    og blokkerer ikke event loop.
"""

import os
os.environ["HF_HUB_OFFLINE"] = "1"

import asyncio
import json
import logging
import multiprocessing
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import numpy as np

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Konfigurasjon
# ---------------------------------------------------------------------------

MODELL_ID      = os.getenv("WHISPER_MODELL",         "NbAiLab/nb-whisper-medium")
CT2_MODELL_STI = os.getenv("WHISPER_SANNTID_MODELL", "modeller/nb-whisper-medium")
ARBEIDSMAPPE   = Path(tempfile.mkdtemp(prefix="transkribering_"))

# Ollama-konfigurasjon
OLLAMA_URL            = os.getenv("OLLAMA_URL",            "http://localhost:11434")
OLLAMA_MODELL         = os.getenv("OLLAMA_MODELL",         "qwen3.6:35b")
# Begrenser KV-cache-allokering. Modeller med stort standardvindauge (f.eks. qwen3.5: 128k)
# bruker ellers sekunder på allokering alene. 8192 er nok for alle §14a-referater.
OLLAMA_NUM_CTX        = int(os.getenv("OLLAMA_NUM_CTX",    "8192"))

# ---------------------------------------------------------------------------
# LLM-prompts for møtereferat (§14a) – basert på NAVs retningslinjer fra Navet
# ---------------------------------------------------------------------------

_SYSTEM_REFERAT = """\
SPRÅK: Skriv ALLTID på bokmål. Aldri bruk nynorsk eller dialektformer, uansett hva transkripsjonene inneholder.
Vanlige nynorsk-ord skal alltid skrives som bokmål: tilskot→tilskudd, handla→handlet, møtest→møtes, rettleiar→veileder, ønskje→ønske, søkje→søke, kva→hva, brukar→bruker, ikkje→ikke.

Du er en assistent som hjelper NAV-veiledere med å skrive samtalereferater etter § 14a-møter.
Referatet skrives inn i Aktivitetsplanen i Modia og deles direkte med brukeren.

VIKTIGSTE REGEL – INGEN HALLUSINASJONER:
Skriv BARE informasjon som faktisk finnes i transkripsjonene.
Dersom en seksjon ikke har relevant innhold fra samtalen, skriv «—» for den seksjonen.
IKKE dikte opp avtaler, mål, jobbønsker eller møtetidspunkter som ikke ble nevnt.
Generiske fraser som «du skal jobbe aktivt mot dine mål» eller «vi avtaler neste møte»
skal ALDRI brukes med mindre dette faktisk ble sagt i samtalen.

KAN SKRIVES:
- Brukerens jobbmål og hva som ble avtalt for å nå det
- Statlige ytelser (dagpenger, AAP, uføretrygd, sykepenger, arbeidsavklaringspenger)
- Arbeidsrettede aktiviteter og tiltak
- Bistandsbehov etter § 14a
- Konkrete avtaler om neste steg, frister og ansvarsfordeling
- At det ble gitt generell informasjon om sosialhjelp (men ikke detaljer)
- Navn på deltakere i samarbeidsmøter (men ikke deres kommunale rolle)

KAN IKKE SKRIVES (§15-grensen):
- Vedtak, utbetalinger eller detaljer fra sosialtjenesten
- At personen har kontakt med sosialtjenesten (NAV-kontorets kommunale del)
- Helsediagnoser eller sykdomshistorikk
- Subjektive vurderinger av brukerens personlighet eller atferd
- Opplysninger om brukerens familie som ikke er saklig nødvendig

STIL:
- Skriv ALLTID på bokmål, uavhengig av språket i transkripsjonene
- Skriv i vi/du-form («Vi avtalte at du …»)
- Klart og enkelt språk – brukeren skal forstå uten fagkunnskap
- Kortfattet og faktabasert
- Svar BARE med selve referatteksten, ingen innledende kommentarer"""

_BRUKER_REFERAT = """\
Lag et samtalereferat basert på følgende transkripsjon.

Bruk denne strukturen. BARE ta med innhold som faktisk finnes i transkripsjonene.
Utelat seksjoner som ikke har relevant innhold, eller skriv «—».

**Bakgrunn for møtet**
[Hva var formålet med møtet, basert på hva som ble sagt]

**Hva vi snakket om**
[Kun arbeidsrettet innhold som faktisk ble diskutert: mål, muligheter, utfordringer, ytelser, tiltak]

**Avtaler**
[Kun konkrete avtaler som ble gjort i samtalen. Hvis ingen avtaler ble gjort, skriv «—»]

**Neste møte**
[Kun hvis dato/tidspunkt ble avtalt i samtalen. Hvis ikke, skriv «—»]

Opplysninger om sosialtjenesten, kommunale ytelser eller helsediagnoser skal IKKE inkluderes.
Marker i stedet med: ⚠️ [Veileder: sjekk om dette skal inkluderes]

Skriv svaret på bokmål.

TRANSKRIPSJON:
{transkripsjon}"""

_SYSTEM_SAMMENDRAG = """\
SPRÅK: Skriv ALLTID på bokmål. Aldri bruk nynorsk eller dialektformer, uansett hva transkripsjonene inneholder.
Vanlige nynorsk-ord skal alltid skrives som bokmål: tilskot→tilskudd, handla→handlet, møtest→møtes, rettleiar→veileder, ønskje→ønske, søkje→søke, kva→hva, brukar→bruker, ikkje→ikke.

Du er en assistent som hjelper NAV-veiledere å holde oversikt under § 14a-møter.
Gi et kort løpende sammendrag av hva som er snakket om hittil.
Fokuser på arbeidsrettet innhold. Ta IKKE med opplysninger om sosialtjenesten, kommunale ytelser eller helsediagnoser.
Skriv ALLTID på bokmål, uavhengig av språket i transkripsjonene.
Svar BARE med selve sammendragsteksten, ingen innledning."""

_BRUKER_SAMMENDRAG = """\
Gi et kort sammendrag (maks 5 kulepunkter) av hva som er snakket om hittil i dette §14a-møtet.

Fokuser på:
- Brukerens situasjon og jobbmål
- Utfordringer og muligheter som er nevnt
- Eventuelle ytelser eller tiltak som er diskutert

TRANSKRIPSJON SÅ LANGT:
{transkripsjon}

Skriv svaret på bokmål."""

# Prompt for rullerende utkast under pågående møte (kortere/raskere enn fullversjon)
_SYSTEM_RULLERENDE = """\
SPRÅK: Skriv ALLTID på bokmål. Aldri bruk nynorsk eller dialektformer.

Du er en assistent som lager et løpende utkast til samtalereferat under et pågående §14a-møte.
Utkastet oppdateres fortløpende ettersom møtet skrider frem.

VIKTIGSTE REGEL – INGEN HALLUSINASJONER:
Skriv BARE informasjon som faktisk finnes i transkripsjonene hittil.
Dersom en seksjon ikke har innhold ennå, skriv «—».
IKKE dikte opp avtaler, jobbmål eller møtetidspunkter.

KAN IKKE SKRIVES: Vedtak/ytelser fra sosialtjenesten, helsediagnoser, subjektive personvurderinger.
Marker slike temaer med: ⚠️ [Sjekk §15]

Svar BARE med selve utkastteksten, ingen innledende kommentarer."""

_BRUKER_RULLERENDE = """\
Lag et oppdatert utkast til samtalereferat basert på transkripsjonen hittil.
Dette er et pågående møte – utkastet er ikke ferdig.

Bruk alltid disse overskriftene:

**Bakgrunn for møtet**
[Formål og kontekst fra det som er sagt]

**Hva vi har snakket om**
[Arbeidsrettet innhold diskutert hittil: mål, muligheter, ytelser, tiltak]

**Foreløpige avtaler**
[Konkrete avtaler nevnt hittil. Hvis ingen ennå: «—»]

TRANSKRIPSJON SÅ LANGT:
{transkripsjon}

Skriv svaret på bokmål."""


# LLM-en kan speile nynorsk/dialekt fra transkripsjonene. Denne funksjonen
# erstatter kjente nynorsk-former deterministisk, som et siste sikkerhetslag.

_NYNORSK_BOKMAL = [
    # Pronomen og determinativer
    (r"\bikkje\b",          "ikke"),
    (r"\bein\b",            "en"),
    (r"\beine\b",           "ene"),
    (r"\beit\b",            "et"),
    (r"\bho\b",             "hun"),
    (r"\bdei\b",            "de"),
    (r"\bdeira\b",          "deres"),
    (r"\bme\b",             "vi"),
    (r"\bkva\b",            "hva"),
    (r"\bnoko\b",           "noe"),
    (r"\bnokon\b",          "noen"),
    (r"\binga\b",           "ingen"),
    (r"\bnokre\b",          "noen"),
    # Preposisjoner og adverb
    (r"\bfrå\b",            "fra"),
    (r"\bhjå\b",            "hos"),
    (r"\bpå grunn av\b",    "på grunn av"),  # same, no change needed
    (r"\bnår\b",            "når"),          # same in both
    # Verb – infinitiv
    (r"\bvere\b",           "være"),
    (r"\bgjere\b",          "gjøre"),
    (r"\bseie\b",           "si"),
    (r"\bseia\b",           "si"),
    (r"\bsjå\b",            "se"),
    (r"\bkome\b",           "komme"),
    (r"\bgje\b",            "gi"),
    (r"\bta\b",             "ta"),           # same
    (r"\bsøkje\b",          "søke"),
    (r"\bønskje\b",         "ønske"),
    (r"\btrengje\b",        "trenge"),
    # Verb – presens
    (r"\bseier\b",          "sier"),
    (r"\bgjer\b",           "gjør"),
    (r"\bkjem\b",           "kommer"),
    (r"\bveit\b",           "vet"),
    (r"\bsegjer\b",         "sier"),
    (r"\btenkjer\b",        "tenker"),
    (r"\bsøkjer\b",         "søker"),
    (r"\bønskjer\b",        "ønsker"),
    (r"\btrengst\b",        "trengs"),
    (r"\btreng\b",          "trenger"),
    (r"\bmøtest\b",         "møtes"),
    # Verb – preteritum / perfektum
    (r"\bsnakka\b",         "snakket"),
    (r"\bjobba\b",          "jobbet"),
    (r"\barbeida\b",        "arbeidet"),
    (r"\bhandla\b",         "handlet"),
    (r"\bavtala\b",         "avtalt"),
    (r"\bopna\b",           "åpnet"),
    (r"\bbrukte\b",         "brukte"),       # same
    # Verb – passiv / infinitiv m/a
    (r"\btrappast\b",       "trappes"),
    (r"\bbehøvast\b",       "behøves"),
    # Substantiv – bestemte former med -a ending
    (r"\bbehandlinga\b",    "behandlingen"),
    (r"\butgreiinga\b",     "utredningen"),
    (r"\bforskinga\b",      "forskningen"),
    (r"\bvurderinga\b",     "vurderingen"),
    (r"\bavtalinga\b",      "avtalen"),
    (r"\boldinga\b",        "holdingen"),
    (r"\bsamtala\b",        "samtalen"),
    (r"\btida\b",           "tiden"),
    (r"\brapporten\b",      "rapporten"),    # same
    # Substantiv og adjektiv
    (r"\bbrukar\b",         "bruker"),
    (r"\bbrukarar\b",       "brukere"),
    (r"\bbrukaren\b",       "brukeren"),
    (r"\brettleiar\b",      "veileder"),
    (r"\brettleiarar\b",    "veiledere"),
    (r"\brettleiaren\b",    "veilederen"),
    (r"\btilskot\b",        "tilskudd"),
    (r"\bhøgare\b",         "høyere"),
    (r"\btilbodet\b",       "tilbudet"),
    (r"\bnoko å seie\b",    "noe å si"),
    (r"\bnoko\b",           "noe"),
]


def _normaliser_til_bokmal(tekst: str) -> str:
    """Erstatter kjente nynorsk-former med bokmål i LLM-output."""
    for mønster, erstatning in _NYNORSK_BOKMAL:
        def _bytt(m: re.Match, repl: str = erstatning) -> str:
            s = m.group(0)
            return repl[0].upper() + repl[1:] if s[0].isupper() else repl
        tekst = re.sub(mønster, _bytt, tekst, flags=re.IGNORECASE)
    return tekst


# Estimerte sekundar for LLM-generering per modell (kalibrert på Apple M-seriens MPS).
# Skaler med transkripsjonsleneden i _beregn_llm_estimat().
_LLM_ESTIMAT_SEK: dict[str, float] = {
    "qwen3:32b":            45.0,
    "qwen3.5:latest":       10.0,
    "qwen3.5-128k:latest":  10.0,
    "gemma4:26b":           35.0,
    "glm-4.7-flash:latest": 20.0,
}


def _beregn_llm_estimat(modell: str | None, transkripsjon: str) -> int:
    """Returner estimert genereringstid i sekunder for valgt modell og transkripsjonslengde."""
    m = modell or OLLAMA_MODELL
    base = next((v for k, v in _LLM_ESTIMAT_SEK.items() if k in m), 35.0)
    ord_antall = len(transkripsjon.split())
    # +10% per 500 ord over 500 (lengre kontekst = tregere prefill)
    if ord_antall > 500:
        base *= 1.0 + (ord_antall - 500) / 5000
    return max(5, round(base))


async def _stream_ollama_tokens(system: str, bruker: str, modell: str | None = None):
    """Async generator som gir (token, er_ferdig, full_normalisert_tekst).

    Brukes av streaming-endepunktene. Siste yield har er_ferdig=True og full tekst.
    """
    valgt_modell = modell or OLLAMA_MODELL
    deler: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": valgt_modell,
                "system": system,
                "prompt": bruker,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.25, "num_ctx": OLLAMA_NUM_CTX},
            },
        ) as resp:
            resp.raise_for_status()
            async for linje in resp.aiter_lines():
                if not linje:
                    continue
                try:
                    chunk = json.loads(linje)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("response", "")
                if token:
                    deler.append(token)
                    yield token, False, ""
                if chunk.get("done"):
                    full = _normaliser_til_bokmal("".join(deler).strip())
                    yield "", True, full
                    return


# Estimert prosesseringstid som andel av lydens varighet (kalibrert for MPS).
# Faktor: sekunder prosessering per sekund lyd, kalibrert for MPS (Apple Silicon).
_MODELL_FAKTOR = {"tiny": 0.08, "base": 0.12, "small": 0.20, "medium": 0.33, "large": 0.60}
# Multiplikator per hardware relativt til MPS-baseline.
_ENHET_MULTIPLIKATOR = {"cuda": 0.25, "mps": 1.0, "cpu": 3.5}
_DIARISER_OVERHEAD_S = 8  # fast diariserings-overhead uavhengig av lydens lengde

def _estimert_total_s(modell_id: str, lyd_s: float, enhet: str = "mps") -> float:
    faktor  = next((v for k, v in _MODELL_FAKTOR.items() if k in modell_id.lower()), 0.33)
    hw_mult = _ENHET_MULTIPLIKATOR.get(enhet, 1.0)
    return max(lyd_s * faktor * hw_mult + _DIARISER_OVERHEAD_S, 5.0)

_mp_ctx = multiprocessing.get_context("spawn")
_jobbkø: multiprocessing.Queue = _mp_ctx.Queue()

# ---------------------------------------------------------------------------
# Hjelpefunksjoner
# ---------------------------------------------------------------------------

def _er_hallusinasjon(tekst: str, maks_repetisjoner: int = 3) -> bool:
    """Oppdager Whisper-hallusinasjoner (gjentakende fraser/ord)."""
    ord_liste = tekst.split()
    if len(ord_liste) < 4:
        return False
    for n in range(1, len(ord_liste) // maks_repetisjoner + 1):
        for start in range(n):
            fraser = [" ".join(ord_liste[i:i+n]) for i in range(start, len(ord_liste) - n + 1, n)]
            if len(fraser) >= maks_repetisjoner:
                unik = set(f.lower() for f in fraser)
                if len(unik) == 1:
                    return True
    return False


def _trim_null_ord(ord_liste: list) -> list:
    """
    Fjerner hallusinerte ord der Whisper har stapet dem på nøyaktig samme
    tidsstempel med varighet=0. Dette skjer typisk når lyden kuttes midt i
    en setning og modellen «fyller ut» resten uten lyd å støtte seg på.

    Algoritme: finn siste ord med varighet >= 0.01s; kapp alt etter det.
    """
    siste_reelle = -1
    for i, c in enumerate(ord_liste):
        ts0, ts1 = c["timestamp"]
        if ts1 is not None and (ts1 - ts0) >= 0.01:
            siste_reelle = i
    if siste_reelle == -1:
        return ord_liste
    kappet = len(ord_liste) - siste_reelle - 1
    if kappet > 0:
        print(f"[trim-null] Kappet {kappet} null-varighet hallusinerte ord "
              f"(fra {ord_liste[siste_reelle + 1]['timestamp'][0]:.2f}s)", flush=True)
    return ord_liste[:siste_reelle + 1]


def _trim_etter_stille(
    ord_liste: list,
    pcm: np.ndarray,
    sample_rate: int = 16000,
    energi_terskel: float = 0.01,
    margin_s: float = 0.4,
) -> list:
    """
    Fjerner ord hvis tidsstempler starter etter at lyden faktisk er slutt.
    Whisper hallusinerer tekst ved stillhet på slutten av opptaket.

    Algoritme: finn siste 50ms-vindu med RMS >= energi_terskel,
    legg til margin_s, og kapp alle ord etter det tidspunktet.
    """
    if not ord_liste or len(pcm) == 0:
        return ord_liste

    VINDU_N = int(0.05 * sample_rate)
    siste_tale = 0.0
    for i in range(0, len(pcm) - VINDU_N, VINDU_N):
        if np.sqrt(np.mean(pcm[i:i + VINDU_N] ** 2)) >= energi_terskel:
            siste_tale = (i + VINDU_N) / sample_rate

    tale_grense = siste_tale + margin_s
    print(f"[trim] Siste tale: {siste_tale:.1f}s  grense: {tale_grense:.1f}s  "
          f"(lydfil: {len(pcm)/sample_rate:.1f}s)", flush=True)

    return [c for c in ord_liste if c["timestamp"][0] < tale_grense]


def _fjern_hallusinasjon(tekst: str, maks_repetisjoner: int = 3) -> str:
    """Trunkerer tekst ved første repetitive sekvens (Whisper-hallusinasjon)."""
    setninger = [s.strip() for s in tekst.replace("?", ".").replace("!", ".").split(".") if s.strip()]
    if len(setninger) < maks_repetisjoner + 1:
        return tekst
    for i in range(len(setninger) - maks_repetisjoner):
        vindu = setninger[i:i + maks_repetisjoner]
        if len(set(s.lower() for s in vindu)) == 1:
            # Kutt her – returner alt frem til repetisjon
            rein = ". ".join(setninger[:i]).strip()
            return (rein + ".") if rein else ""
    return tekst


# ---------------------------------------------------------------------------
# Arbeiderprosess – kjøres i egen process, laster modell én gang
# ---------------------------------------------------------------------------

def _arbeider(jobbkø: multiprocessing.Queue, modell_id: str):
    """
    Kjøres som en separat prosess. Laster Whisper-modellen én gang,
    deretter behandler jobber fra køen løpende.
    """
    os.environ["HF_HUB_OFFLINE"] = "1"
    import logging
    logging.getLogger("transformers").setLevel(logging.ERROR)

    from transformers import pipeline
    import torch

    # Alle modellstørrelser fungerer på MPS med spawn-kontekst
    # (fork-kontekst krasjer Metal-kompilatoren for medium/large)
    def velg_enhet(mod_id: str) -> str:
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _lag_pipeline(mod_id: str, dev: str):
        return pipeline("automatic-speech-recognition", model=mod_id, device=dev, ignore_warning=True)

    enhet = velg_enhet(modell_id)
    print(f"[arbeider] Laster modell: {modell_id}  (enhet: {enhet}) …", flush=True)
    asr = _lag_pipeline(modell_id, enhet)
    print("[arbeider] Modell klar.", flush=True)

    while True:
        melding = jobbkø.get()
        if melding is None:
            break

        jobb_id, lydfil_str, resultat_fil_str, n_talere = melding
        lydfil = Path(lydfil_str)
        resultat_fil = Path(resultat_fil_str)

        # Marker som pågående med starttid, modell og hardware-info
        resultat_fil.write_text(json.dumps({
            "status": "transkriberer",
            "fase": "konverterer",
            "start_tid": time.time(),
            "modell_id": modell_id,
            "enhet": enhet,
            "lyd_varighet_s": None,
        }))

        wav_sti = None
        try:
            wav_sti = Path(tempfile.mktemp(suffix=".wav"))
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(lydfil),
                 "-ar", "16000", "-ac", "1", str(wav_sti)],
                check=True, capture_output=True,
            )

            # Hent PCM én gang — brukes til trimming og diarisering
            pcm = np.frombuffer(
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(wav_sti),
                     "-ar", "16000", "-ac", "1", "-f", "f32le", "pipe:1"],
                    check=True, capture_output=True,
                ).stdout,
                dtype="<f4",
            ).copy()
            lyd_varighet_s = len(pcm) / 16000

            # Oppdater status med lydvarighet nå som vi kjenner den
            start_data = json.loads(resultat_fil.read_text())
            start_data.update({"fase": "transkriberer", "lyd_varighet_s": lyd_varighet_s})
            resultat_fil.write_text(json.dumps(start_data))

            # Ord-nivå tidsstempler for presis taler-splitting
            # num_beams=1: beam search hjelper ikke for tidsstempel-justering
            resultat = asr(
                str(wav_sti),
                chunk_length_s=28,
                return_timestamps="word",
                generate_kwargs={"num_beams": 1, "task": "transcribe", "language": "no"},
            )

            tekst = resultat["text"].strip()
            ord_liste = resultat.get("chunks", [])

            # Fiks None-tidsstempler
            siste_slutt = next(
                (c["timestamp"][1] for c in reversed(ord_liste) if c["timestamp"][1] is not None),
                0.0,
            )
            for c in ord_liste:
                ts0 = c["timestamp"][0] or 0.0
                ts1 = c["timestamp"][1] if c["timestamp"][1] is not None else siste_slutt
                c["timestamp"] = (ts0, ts1)

            # Trim null-varighet ord stapet på slutten (Whisper-hallusinasjon ved abrupt kutt)
            ord_liste = _trim_null_ord(ord_liste)
            # Trim ord som starter etter at faktisk tale er slutt (blokkerer hallusinering)
            ord_liste = _trim_etter_stille(ord_liste, pcm)
            if not ord_liste:
                tekst = ""

            # --- Speaker diarisering ---
            start_data.update({"fase": "diariserer"})
            resultat_fil.write_text(json.dumps(start_data))
            try:
                diari_segs, _ = _diariser(pcm, n_talere=n_talere)
            except Exception as diar_exc:
                print(f"[arbeider] Diarisering feilet: {diar_exc}", flush=True)
                diari_segs = []

            # Bygg segmenter: hvert ord får en taler, grupper påfølgende same-taler-ord
            segmenter = []
            if diari_segs:
                gjeldende_taler = "SPEAKER_00"
                gjeldende_ord: list[str] = []
                gjeldende_start = 0.0
                gjeldende_slutt = 0.0

                for c in ord_liste:
                    ts0, ts1 = c["timestamp"]
                    ord_tekst = c["text"]
                    taler = _tilordne_taler(ts0, ts1, diari_segs, gjeldende_taler)

                    if taler != gjeldende_taler and gjeldende_ord:
                        # Talerbytte – tøm gjeldende gruppe
                        t = _fjern_hallusinasjon("".join(gjeldende_ord).strip())
                        if t:
                            segmenter.append({
                                "start": round(gjeldende_start, 1),
                                "slutt": round(ts0, 1),
                                "tekst": t,
                                "taler": gjeldende_taler,
                            })
                        gjeldende_ord = []
                        gjeldende_start = ts0

                    if not gjeldende_ord:
                        gjeldende_start = ts0
                    gjeldende_taler = taler
                    gjeldende_ord.append(ord_tekst)
                    gjeldende_slutt = ts1

                # Siste gruppe
                if gjeldende_ord:
                    t = _fjern_hallusinasjon("".join(gjeldende_ord).strip())
                    if t:
                        segmenter.append({
                            "start": round(gjeldende_start, 1),
                            "slutt": round(gjeldende_slutt, 1),
                            "tekst": t,
                            "taler": gjeldende_taler,
                        })

                # Slå sammen svært korte stubb-segmenter (< 3 ord) med naboer
                MIN_ORD = 3
                i = 0
                while i < len(segmenter):
                    antall_ord = len(segmenter[i]["tekst"].split())
                    if antall_ord < MIN_ORD:
                        gjeldende = segmenter[i]
                        har_forrige = i > 0
                        har_neste   = i + 1 < len(segmenter)
                        # Foretrekk nabo med samme taler
                        same_forrige = har_forrige and segmenter[i-1]["taler"] == gjeldende["taler"]
                        same_neste   = har_neste   and segmenter[i+1]["taler"] == gjeldende["taler"]
                        if same_forrige and not same_neste:
                            slå_inn_forrige = True
                        elif same_neste and not same_forrige:
                            slå_inn_forrige = False
                        elif har_forrige and har_neste:
                            # Begge eller ingen har samme taler → velg nærmeste
                            tid_til_forrige = gjeldende["start"] - segmenter[i-1]["slutt"]
                            tid_til_neste   = segmenter[i+1]["start"] - gjeldende["slutt"]
                            slå_inn_forrige = tid_til_forrige <= tid_til_neste
                        else:
                            slå_inn_forrige = har_forrige
                        if slå_inn_forrige:
                            prev = segmenter[i - 1]
                            prev["tekst"] = prev["tekst"].rstrip() + " " + gjeldende["tekst"]
                            prev["slutt"] = gjeldende["slutt"]
                            segmenter.pop(i)
                        elif har_neste:
                            nxt = segmenter[i + 1]
                            nxt["tekst"] = gjeldende["tekst"] + " " + nxt["tekst"]
                            nxt["start"] = gjeldende["start"]
                            segmenter.pop(i)
                        else:
                            i += 1
                    else:
                        i += 1

                # Slå sammen påfølgende segmenter med samme taler
                i = 0
                while i + 1 < len(segmenter):
                    if segmenter[i]["taler"] == segmenter[i+1]["taler"]:
                        segmenter[i]["tekst"] = segmenter[i]["tekst"].rstrip() + " " + segmenter[i+1]["tekst"]
                        segmenter[i]["slutt"] = segmenter[i+1]["slutt"]
                        segmenter.pop(i + 1)
                    else:
                        i += 1
            else:
                # Fallback uten diarisering: én gruppe per Whisper-segment
                gjeldende_ord_grp: list[str] = []
                grp_start = 0.0
                for c in ord_liste:
                    if not gjeldende_ord_grp:
                        grp_start = c["timestamp"][0]
                    gjeldende_ord_grp.append(c["text"])
                if gjeldende_ord_grp:
                    t = _fjern_hallusinasjon(" ".join(gjeldende_ord_grp).strip())
                    if t:
                        segmenter.append({
                            "start": round(grp_start, 1),
                            "slutt": round(siste_slutt, 1),
                            "tekst": t,
                            "taler": "SPEAKER_00",
                        })

            resultat_fil.write_text(
                json.dumps({"status": "ferdig", "tekst": tekst, "segmenter": segmenter},
                           ensure_ascii=False)
            )
        except Exception as exc:
            import traceback
            print(f"[arbeider] FEIL i jobb {jobb_id}: {exc}", flush=True)
            traceback.print_exc()
            resultat_fil.write_text(
                json.dumps({"status": "feil", "feilmelding": str(exc)})
            )
        finally:
            lydfil.unlink(missing_ok=True)
            if wav_sti:
                wav_sti.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# FastAPI-app med lifespan (starter/stopper worker-prosessen)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    prosess = _mp_ctx.Process(target=_arbeider, args=(_jobbkø, MODELL_ID), daemon=True)
    prosess.start()
    yield
    _jobbkø.put(None)  # Signal til worker om å avslutte
    prosess.join(timeout=5)


app = FastAPI(title="NB-Whisper transkribering", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/", include_in_schema=False)
def rot():
    return FileResponse("static/index.html")


@app.get("/isAlive", include_in_schema=False)
def is_alive():
    return {"status": "ok"}


@app.get("/isReady", include_in_schema=False)
def is_ready():
    """Klar når arbeiderprosessen er startet og modellen er lastet."""
    if not _arbeider_klar.is_set():
        from fastapi import Response
        return Response(content='{"status":"laster modell"}', status_code=503,
                        media_type="application/json")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Batch-endepunkter
# ---------------------------------------------------------------------------

@app.post("/transkriber")
async def start_transkribering(
    lydfil: UploadFile = File(...),
    n_talere: int = Form(0),
):
    """Mottar lydfil, sender til arbeiderprosess, returnerer jobb-ID.

    n_talere: 0 = auto-deteksjon, 2/3/4 = eksakt antall talere.
    """
    suffix = Path(lydfil.filename or "opptak.webm").suffix or ".webm"
    jobb_id = str(uuid.uuid4())

    lydfil_sti = ARBEIDSMAPPE / f"{jobb_id}{suffix}"
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"

    with lydfil_sti.open("wb") as f:
        shutil.copyfileobj(lydfil.file, f)

    resultat_sti.write_text(json.dumps({"status": "venter"}))
    _jobbkø.put((jobb_id, str(lydfil_sti), str(resultat_sti), n_talere))

    return {"jobb_id": jobb_id}


@app.get("/status/{jobb_id}")
async def sjekk_status(jobb_id: str):
    """Returnerer status, fremdrift og elapsed tid for en transkriberingsjobb."""
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"
    if not resultat_sti.exists():
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = json.loads(resultat_sti.read_text())

    svar: dict = {"jobb_id": jobb_id, "status": data["status"]}

    if data["status"] == "transkriberer":
        start_tid = data.get("start_tid")
        lyd_s     = data.get("lyd_varighet_s")
        modell_id = data.get("modell_id", MODELL_ID)
        enhet     = data.get("enhet", "cpu")
        fase      = data.get("fase", "transkriberer")

        if start_tid:
            elapsed = time.time() - start_tid
            svar["elapsed_s"] = round(elapsed, 1)
            svar["fase"] = fase

            if lyd_s:
                estimert = _estimert_total_s(modell_id, lyd_s, enhet)
                svar["estimert_total_s"] = round(estimert, 1)
                svar["lyd_varighet_s"]   = round(lyd_s, 1)
                # Diarisering er siste 15 % av estimert tid
                if fase == "diariserer":
                    fremdrift = 0.85 + 0.10 * min(elapsed / estimert, 1.0)
                else:
                    fremdrift = min(elapsed / estimert * 0.85, 0.84)
                svar["fremdrift"] = round(fremdrift, 3)

    return svar


@app.get("/resultat/{jobb_id}")
async def hent_resultat(jobb_id: str):
    """Returnerer ferdig transkripsjon."""
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"
    if not resultat_sti.exists():
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = json.loads(resultat_sti.read_text())
    if data["status"] == "feil":
        raise HTTPException(status_code=500, detail=data.get("feilmelding", "Ukjent feil"))
    if data["status"] != "ferdig":
        raise HTTPException(status_code=409, detail=f"Jobb ikke ferdig (status: {data['status']})")
    return {"jobb_id": jobb_id, "tekst": data["tekst"], "segmenter": data["segmenter"]}


# ---------------------------------------------------------------------------
# Møtereferat og sammendrag – Ollama-integrasjon
# ---------------------------------------------------------------------------

class _OllamaForesporsel(BaseModel):
    transkripsjon: str
    modell: str | None = None


async def _kall_ollama(system: str, bruker: str, modell: str | None = None) -> str:
    """Kaller Ollama /api/generate med streaming og returnerer svarteksten.

    Bruker streaming for å unngå timeout ved lange resonnementer (qwen3-tenking).
    Tenking (`think`) deaktiveres eksplisitt for raskere og mer forutsigbar respons.
    """
    valgt_modell = modell or OLLAMA_MODELL
    deler: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": valgt_modell,
                "system": system,
                "prompt": bruker,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.25, "num_ctx": OLLAMA_NUM_CTX},
            },
        ) as resp:
            resp.raise_for_status()
            async for linje in resp.aiter_lines():
                if not linje:
                    continue
                try:
                    chunk = json.loads(linje)
                except json.JSONDecodeError:
                    continue
                deler.append(chunk.get("response", ""))
                if chunk.get("done"):
                    break
    return "".join(deler).strip()


@app.post("/sammendrag")
async def lag_sammendrag(foresporsel: _OllamaForesporsel):
    """Genererer et løpende sammendrag av transkripsjon hittil (Prompt B)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    try:
        # Normaliser transkripsjonens nynorsk-ord FØR sending – reduserer speiling
        transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
        bruker_prompt = _BRUKER_SAMMENDRAG.format(transkripsjon=transkripsjon_normalisert)
        tekst = await _kall_ollama(_SYSTEM_SAMMENDRAG, bruker_prompt, foresporsel.modell)
        tekst = _normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå Ollama – er tjenesten startet?")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama svarte med feil: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feil ved generering av sammendrag: {e}")
    return {"tekst": tekst, "modell": foresporsel.modell or OLLAMA_MODELL}


@app.post("/referat")
async def lag_referat(foresporsel: _OllamaForesporsel):
    """Genererer et fullt møtereferat fra transkripsjon (Prompt A)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    try:
        # Normaliser transkripsjonens nynorsk-ord FØR sending – reduserer speiling
        transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
        bruker_prompt = _BRUKER_REFERAT.format(transkripsjon=transkripsjon_normalisert)
        tekst = await _kall_ollama(_SYSTEM_REFERAT, bruker_prompt, foresporsel.modell)
        tekst = _normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå Ollama – er tjenesten startet?")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama svarte med feil: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feil ved generering av referat: {e}")
    return {"tekst": tekst, "modell": foresporsel.modell or OLLAMA_MODELL}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/referat/stream")
async def lag_referat_stream(foresporsel: _OllamaForesporsel):
    """Streaming SSE-versjon av /referat.

    Sender:
      {"type":"start",  "estimert_sek": N, "modell": "..."}
      {"type":"token",  "tekst": "..."}          (én per token)
      {"type":"ferdig", "tekst": "..."}          (normalisert sluttekst)
    eller:
      {"type":"feil",   "melding": "..."}
    """
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = _beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = _BRUKER_REFERAT.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield _sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in _stream_ollama_tokens(
                _SYSTEM_REFERAT, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield _sse({"type": "ferdig", "tekst": full_tekst, "modell": valgt_modell})
                elif token:
                    yield _sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield _sse({"type": "feil", "melding": "Kan ikke nå Ollama – er tjenesten startet?"})
        except Exception as e:
            yield _sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/sammendrag/stream")
async def lag_sammendrag_stream(foresporsel: _OllamaForesporsel):
    """Streaming SSE-versjon av /sammendrag."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = _beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = _BRUKER_SAMMENDRAG.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield _sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in _stream_ollama_tokens(
                _SYSTEM_SAMMENDRAG, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield _sse({"type": "ferdig", "tekst": full_tekst, "modell": valgt_modell})
                elif token:
                    yield _sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield _sse({"type": "feil", "melding": "Kan ikke nå Ollama – er tjenesten startet?"})
        except Exception as e:
            yield _sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/referat/rullerende/stream")
async def lag_rullerende_referat_stream(foresporsel: _OllamaForesporsel):
    """Rullerende utkast-referat for pågående møte (sanntid-modus).

    Kalt automatisk fra frontend hvert ~150. nye ord. Bruker en kortere prompt
    enn fullversjonen slik at svaret er raskere.

    Sender:
      {"type":"start",  "estimert_sek": N, "modell": "..."}
      {"type":"token",  "tekst": "..."}
      {"type":"ferdig", "tekst": "..."}
    eller:
      {"type":"feil",   "melding": "..."}
    """
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = _beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = _BRUKER_RULLERENDE.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield _sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in _stream_ollama_tokens(
                _SYSTEM_RULLERENDE, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield _sse({"type": "ferdig", "tekst": _normaliser_til_bokmal(full_tekst), "modell": valgt_modell})
                elif token:
                    yield _sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield _sse({"type": "feil", "melding": "Kan ikke nå Ollama – er tjenesten startet?"})
        except Exception as e:
            yield _sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


#
# Protokoll (ny):
#   Browser → Server: binære meldinger = raw float32 LE PCM, 16 kHz, mono
#   Browser → Server: JSON {"type": "stopp"} for å avslutte
#   Server → Client:  JSON {"type": "segment", "tekst": "...", "segmenter": [...]}
#
# Server-side VAD-logikk:
#   - Buffer innkommende PCM-frames
#   - Regn ut RMS-energi per 160-sample frame (10 ms ved 16 kHz)
#   - Detekter tale / stillhet med terskel
#   - Flush buffer til Whisper ved:
#       a) Stillhetsvarighet ≥ STILLHET_TERSKEL_S etter tale (naturlig pause)
#       b) Total bufferlengde ≥ MAKS_BUFFER_S (sikkerhetsnett)
#   - Sendt buffer inneholder kun talesegmenter (stille frames fjernes ikke,
#     men flush skjer ved naturlige pauser)
# ---------------------------------------------------------------------------

import numpy as np

SAMPLE_RATE        = 16000
FRAME_SAMPLES      = 160            # 10 ms per frame
ENERGI_TERSKEL     = 0.01           # RMS-terskel for tale (0–1 float32)
STILLHET_TERSKEL_S = 0.7            # sekunder stillhet → flush
MAKS_BUFFER_S      = 25.0           # sekunder maks buffer
MIN_TALE_S         = 0.3            # minimum tale for å i det hele tatt sende

# ---------------------------------------------------------------------------
# Speaker diarisering – resemblyzer (GE2E-embeddings) + scikit-learn
# ---------------------------------------------------------------------------

_voice_encoder = None
_voice_encoder_lock = threading.Lock()


def _hent_voice_encoder():
    global _voice_encoder
    if _voice_encoder is None:
        with _voice_encoder_lock:
            if _voice_encoder is None:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    from resemblyzer import VoiceEncoder
                _voice_encoder = VoiceEncoder("cpu")
    return _voice_encoder


def _auto_n_talere(embeds: np.ndarray, maks: int = 4) -> int:
    """Finner optimalt antall talere via silhouette score (2..maks)."""
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    beste_n = 2
    beste_score = -1.0
    for n in range(2, maks + 1):
        if len(embeds) < n * 2:
            break
        labels = AgglomerativeClustering(n_clusters=n).fit_predict(embeds)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(embeds, labels, metric="cosine")
        print(f"[diarisering] n={n} silhouette={score:.3f}", flush=True)
        if score > beste_score:
            beste_score = score
            beste_n = n
    print(f"[diarisering] Auto-valgt n_talere={beste_n}", flush=True)
    return beste_n


def _diariser(
    wav: np.ndarray,
    n_talere: int = 0,
    prototyper: "np.ndarray | None" = None,
) -> "tuple[list[dict], np.ndarray | None]":
    """
    Kjører speaker diarization på float32 PCM (16 kHz, mono).

    Args:
        wav:        float32 numpy-array, 16 kHz
        n_talere:   antall forventede talere. 0 = auto-deteksjon.
        prototyper: shape (n_talere, 256) – kjente talere fra tidligere chunks.
                    None betyr første chunk → kluster fra scratch.

    Returns:
        (diari_segs, prototyper)
        diari_segs: [{start, slutt, taler}] – slåtte intervaller
        prototyper: oppdaterte prototyper for neste kall
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics.pairwise import cosine_similarity

    if len(wav) < SAMPLE_RATE * 1.0:
        return [], prototyper

    encoder = _hent_voice_encoder()
    try:
        _, partial_embeds, partial_slices = encoder.embed_utterance(
            wav, return_partials=True, rate=1.5
        )
    except Exception as exc:
        print(f"[diarisering] Embed feil: {exc}", flush=True)
        return [], prototyper

    partial_embeds = np.array(partial_embeds)
    if len(partial_embeds) == 0:
        return [], prototyper

    # Auto-deteksjon av antall talere hvis n_talere=0 og ingen prototyper
    if n_talere == 0:
        if prototyper is not None:
            n_talere = len(prototyper)
        else:
            n_talere = _auto_n_talere(partial_embeds)

    # Bestem speaker-labels
    if len(partial_embeds) < n_talere:
        labels = np.zeros(len(partial_embeds), dtype=int)
    elif prototyper is None:
        labels = AgglomerativeClustering(n_clusters=n_talere).fit_predict(partial_embeds)
    else:
        sims = cosine_similarity(partial_embeds, prototyper)
        labels = np.argmax(sims, axis=1)

    # Oppdater (eller initialiser) prototyper
    ny_prototyper = np.zeros((n_talere, partial_embeds.shape[1]), dtype=np.float32)
    for i in range(n_talere):
        maske = labels == i
        if maske.any():
            ny_snitt = partial_embeds[maske].mean(axis=0)
            if prototyper is not None:
                ny_prototyper[i] = 0.85 * prototyper[i] + 0.15 * ny_snitt
            else:
                ny_prototyper[i] = ny_snitt
        elif prototyper is not None:
            ny_prototyper[i] = prototyper[i]

    # Bygg tidsintervaller, slå sammen påfølgende like talere
    diari_segs: list[dict] = []
    for sl, label in zip(partial_slices, labels):
        start = sl.start / SAMPLE_RATE
        slutt = sl.stop  / SAMPLE_RATE
        taler = f"SPEAKER_{int(label):02d}"
        if diari_segs and diari_segs[-1]["taler"] == taler:
            diari_segs[-1]["slutt"] = round(slutt, 2)
        else:
            diari_segs.append({"start": round(start, 2), "slutt": round(slutt, 2), "taler": taler})

    return diari_segs, ny_prototyper


def _tilordne_taler(
    seg_start: float,
    seg_slutt: float,
    diari_segs: list[dict],
    forrige_taler: str = "SPEAKER_00",
) -> str:
    """
    Finn dominerende taler for et whisper-tidsvindu basert på
    overlapp med diariseringssegmentene.
    """
    stemmer: dict[str, float] = {}
    for d in diari_segs:
        overlapp = min(seg_slutt, d["slutt"]) - max(seg_start, d["start"])
        if overlapp > 0:
            stemmer[d["taler"]] = stemmer.get(d["taler"], 0) + overlapp
    return max(stemmer, key=stemmer.get) if stemmer else forrige_taler

_fw_modell = None
_fw_lock = threading.Lock()


def _hent_fw_modell():
    """Laster faster-whisper-modellen én gang (thread-safe lazy init)."""
    global _fw_modell
    if _fw_modell is None:
        with _fw_lock:
            if _fw_modell is None:
                from faster_whisper import WhisperModel
                sti = CT2_MODELL_STI
                if not Path(sti).exists():
                    raise FileNotFoundError(
                        f"Sanntidsmodellen '{sti}' finnes ikke. "
                        "Kjør konverter_modeller.py for å opprette CTranslate2-modellen, "
                        "eller sett WHISPER_SANNTID_MODELL til en gyldig sti."
                    )
                print(f"[sanntid] Laster {sti} …", flush=True)
                _fw_modell = WhisperModel(sti, device="auto", compute_type="default")
                print("[sanntid] Klar.", flush=True)
    return _fw_modell


def _transkriber_pcm(
    pcm: np.ndarray,
    prototyper: "np.ndarray | None" = None,
) -> "tuple[dict | None, np.ndarray | None]":
    """
    Transkriberer en float32 numpy-array (16 kHz, mono).
    Kjøres via asyncio.to_thread – CTranslate2 slipper GIL.
    Returnerer (resultat, oppdaterte_prototyper).
    """
    if len(pcm) < SAMPLE_RATE * MIN_TALE_S:
        return None, prototyper
    modell = _hent_fw_modell()
    wav_sti = Path(tempfile.mktemp(suffix=".wav"))
    try:
        import soundfile as sf
        sf.write(str(wav_sti), pcm, SAMPLE_RATE, subtype="FLOAT")

        tekst_deler = []
        segmenter_liste = []
        segments, _ = modell.transcribe(
            str(wav_sti),
            language="no",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
        )
        for seg in segments:
            t = seg.text.strip()
            if t:
                tekst_deler.append(t)
                segmenter_liste.append(
                    {"start": round(seg.start, 1), "slutt": round(seg.end, 1), "tekst": t}
                )

        if not tekst_deler:
            return None, prototyper

        # Diarisering på dette chunk-et
        try:
            diari_segs, ny_prototyper = _diariser(pcm, prototyper=prototyper)
            forrige = "SPEAKER_00"
            for seg in segmenter_liste:
                taler = _tilordne_taler(seg["start"], seg["slutt"], diari_segs, forrige)
                seg["taler"] = taler
                forrige = taler
        except Exception:
            for seg in segmenter_liste:
                seg["taler"] = "SPEAKER_00"
            ny_prototyper = prototyper

        tekst = " ".join(tekst_deler)
        return {"tekst": tekst, "segmenter": segmenter_liste}, ny_prototyper

    except Exception as exc:
        print(f"[sanntid] Feil: {exc}", flush=True)
        return None, prototyper
    finally:
        wav_sti.unlink(missing_ok=True)


class _VadBuffer:
    """
    Energibasert VAD-buffer. Samler PCM-frames og avgjør når det er trygt
    å sende til Whisper (ved naturlig pause eller maks bufferlengde).
    """
    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._total_samples: int = 0
        self._tale_samples: int = 0
        self._stille_samples: int = 0
        self._harTale: bool = False

    def legg_til(self, samples: np.ndarray) -> np.ndarray | None:
        """
        Legg til samples (float32). Returner buffer (numpy-array) for
        transkribering hvis VAD utløser flush, ellers None.
        """
        for start in range(0, len(samples), FRAME_SAMPLES):
            frame = samples[start:start + FRAME_SAMPLES]
            if len(frame) == 0:
                continue

            rms = float(np.sqrt(np.mean(frame ** 2)))
            er_tale = rms > ENERGI_TERSKEL

            self._frames.append(frame)
            self._total_samples += len(frame)

            if er_tale:
                self._harTale = True
                self._tale_samples += len(frame)
                self._stille_samples = 0
            else:
                self._stille_samples += len(frame)

            # Flush ved naturlig pause etter tale
            stille_s = self._stille_samples / SAMPLE_RATE
            total_s  = self._total_samples  / SAMPLE_RATE

            if self._harTale and (
                stille_s >= STILLHET_TERSKEL_S or total_s >= MAKS_BUFFER_S
            ):
                return self._flush()

        return None

    def flush_alt(self) -> np.ndarray | None:
        """Tøm buffer ved stopp-kommando."""
        if self._harTale and self._tale_samples > 0:
            return self._flush()
        self._reset()
        return None

    def _flush(self) -> np.ndarray:
        data = np.concatenate(self._frames)
        self._reset()
        return data

    def _reset(self):
        self._frames = []
        self._total_samples = 0
        self._tale_samples = 0
        self._stille_samples = 0
        self._harTale = False


@app.websocket("/ws/sanntid")
async def sanntid_ws(websocket: WebSocket):
    """
    WebSocket-endpoint for sanntidstranskribering med server-side VAD og diarisering.

    Protokoll:
      Client → Server: binær melding = raw float32 LE PCM, 16 kHz, mono
      Client → Server: JSON {"type": "stopp"}  (avslutt og flush)
      Server → Client: JSON {"type": "segment", "tekst": "...", "segmenter": [{..., "taler": "SPEAKER_XX"}]}
    """
    await websocket.accept()

    # Sjekk at sanntidsmodellen finnes – send feilmelding og lukk om ikke
    try:
        _hent_fw_modell()
    except FileNotFoundError as e:
        await websocket.send_json({"type": "feil", "melding": str(e)})
        await websocket.close()
        return

    buf = _VadBuffer()
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
    # Prototype-state deles mellom worker-kall for å holde konsistent taler-ID
    prototyper_state: list[np.ndarray | None] = [None]

    async def transkriber_worker():
        """Konsumerer PCM-bufre fra kø, kjører Whisper + diarisering sekvensielt."""
        while True:
            pcm = await transkriber_kø.get()
            if pcm is None:
                break
            resultat, ny_proto = await asyncio.to_thread(
                _transkriber_pcm, pcm, prototyper_state[0]
            )
            prototyper_state[0] = ny_proto
            if resultat and resultat.get("tekst"):
                try:
                    await websocket.send_json({
                        "type": "segment",
                        "tekst": resultat["tekst"],
                        "segmenter": resultat["segmenter"],
                    })
                except Exception:
                    pass
            transkriber_kø.task_done()

    worker_task = asyncio.create_task(transkriber_worker())

    async def send_til_whisper(pcm: np.ndarray):
        try:
            await transkriber_kø.put(pcm)
        except asyncio.QueueFull:
            print("[sanntid] Kø full – dropper segment", flush=True)

    try:
        while True:
            melding = await websocket.receive()

            if "text" in melding:
                data = json.loads(melding["text"])
                if data.get("type") == "stopp":
                    rest = buf.flush_alt()
                    if rest is not None:
                        await send_til_whisper(rest)
                    break

            elif "bytes" in melding:
                raw = melding["bytes"]
                if not raw:
                    continue

                n_samples = len(raw) // 4
                if n_samples == 0:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()

                pcm_klar = buf.legg_til(samples)
                if pcm_klar is not None:
                    await send_til_whisper(pcm_klar)

    except WebSocketDisconnect:
        pass
    finally:
        await transkriber_kø.put(None)
        await worker_task

