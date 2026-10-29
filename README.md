# Transkriberit

Transkriberings- og referatverktøy for møter og veiledersamtaler.

NAIS-oppsettet bruker NB-Whisper Large for transkripsjon, Nemotron Diarization
for estimert talerskille og Borealis-12b Q8 via llama.cpp for referater. Modellene
kjører i et separat GKE-cluster bak en autentisert Cloud Run-gateway.
Lokal modus bruker som standard NB-Whisper Medium, ECAPA-talerskille og kan
kobles til [Ollama](https://ollama.com) for referatgenerering.

I lokal modus kjøres modellene på maskinen. NAIS/remote-modus sender lyd og tekst
til modell-API-et i GCloud. Se [sikkerhetsoppsett og overgang](docs/model-api-security.md)
før behandling av sensitivt møteinnhold.

## Deployede adresser

- Prod, ansatte på kompatible enheter: https://ao-ki-transkribering.ansatt.nav.no
- Prod, internt nett: https://ao-ki-transkribering.intern.nav.no
- Dev, internt nett: https://ao-ki-transkribering.intern.dev.nav.no

Dev krever en konto i NAV Preprod, ikke den vanlige produksjonskontoen.
`intern`-adressene krever intern nettverkstilgang; `ansatt`-adressen beholder
innlogging og plattformens krav til enhet. Personlige telefoner er ikke garantert
tilgang. GitHub Actions bygger appen og deployer til dev før prod. Modellclusteret
oppdateres separat; se [drift](docs/operations.md).
Operasjonelle logger, målinger, dashboard og alarmer er beskrevet i
[monitorering](docs/monitoring.md), uten opptak, transkripsjoner eller referatinnhold.

---

## Ordliste (enkelt forklart)

**API:** En "dør" mellom to systemer. API-et bestemmer hvordan de kan sende informasjon til hverandre på en trygg og strukturert måte.

**Infrastruktur:** Grunnmuren som får løsningen til å fungere i praksis, for eksempel servere, nettverk, lagring og oppsett rundt applikasjonen.

**IP (IP-adresse):** Et nummer som identifiserer en enhet i et nettverk, omtrent som en adresse slik at data finner riktig mottaker.

---

## Krav til maskinvare

| | Minimum | Anbefalt |
|---|---|---|
| RAM | 16 GB | 32 GB+ |
| GPU-minne | — | 8 GB+ (NVIDIA/Apple Silicon) |
| Disk | 10 GB ledig | 30 GB (for flere modeller) |
| OS | macOS 13+, Ubuntu 22.04+ | — |

> **Apple Silicon:** Modellene bruker MPS automatisk.  
> **NVIDIA:** Krever CUDA 12+ og `torch` med CUDA-støtte (se [pytorch.org](https://pytorch.org/get-started/locally/)).  
> **CPU-only:** Fungerer, men transkripsjon av et 1-timesmøte tar ~30–60 min.

---

## 1. Forutsetninger

### Python

Krever Python 3.11 eller 3.12.

```bash
python3 --version   # skal vise 3.11.x eller 3.12.x
```

### ffmpeg

Brukes til lydkonvertering.

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt install ffmpeg
```

---

## 2. Klon og installer Python-avhengigheter

```bash
git clone https://github.com/navikt/ao-ki-transkribering.git
cd ao-ki-transkribering

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

> **NVIDIA GPU:** Installer torch med CUDA-støtte *før* `requirements.txt`:
> ```bash
> pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
> pip install -r requirements.txt
> ```

---

## 3. Last ned nb-whisper-modeller

```bash
python last_ned_modeller.py
```

Skriptet laster ned alle fem størrelsene nedenfor til lokal Hugging Face-cache.
Det hopper over modeller som allerede finnes. Rediger `MODELLER`-listen først
hvis du bare trenger medium, som er standard for lokal kjøring. Talerskille kan
også hente ECAPA-modellen første gang; cache denne før eventuell offline-bruk.

Vil du laste ned andre størrelser:

| Modell | Størrelse | Hastighet | Nøyaktighet |
|--------|-----------|-----------|-------------|
| tiny | 148 MB | svært rask | lav |
| base | 295 MB | rask | middels |
| small | 926 MB | middels | god |
| **medium** | **2,8 GB** | **anbefalt** | **veldig god** |
| large | 5,8 GB | treg | best |

Rediger `MODELLER`-listen i `last_ned_modeller.py` for å velge hvilke som lastes ned.

### Konverter for sanntidsmodus

Sanntidstranskribering bruker `faster-whisper` (CTranslate2-format, ~4× raskere).
Konverter `nb-whisper-medium` til dette formatet:

```bash
python konverter_modeller.py
```

Konverterte modeller lagres i `./modeller/` (~1,3 GB for medium).

---

## 4. Installer og konfigurer Ollama

Ollama brukes for å generere møtereferat og sammendrag via lokal LLM.

### Installer Ollama

**macOS:**
```bash
brew install ollama
```

Eller last ned fra [ollama.com/download](https://ollama.com/download).

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Start Ollama-tjenesten

```bash
ollama serve
```

Kjør dette i et eget terminalvindu, eller som bakgrunnsprosess. Ollama lytter på `http://localhost:11434`.

### Last ned lokal LLM

```bash
ollama pull qwen3:8b
```

`qwen3:8b` er standard når `LLM_BACKEND=ollama`. Velg en annen installert modell
med `LLM_MODELL`, for eksempel `borealis:12b`. Hastighet og minnebehov avhenger av
modell, kvantisering og kontekstvindu.

> **En annen modell?** Last den ned og angi navnet ved oppstart:
> ```bash
> ollama pull qwen3:32b
> export LLM_MODELL=qwen3:32b
> ```
> Se [ollama.com/library](https://ollama.com/library) for tilgjengelige modeller.

---

## 5. Start applikasjonen

```bash
source .venv/bin/activate   # hvis ikke allerede aktivert
LLM_BACKEND=ollama python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
```

Åpne nettleser på [http://127.0.0.1:8765](http://127.0.0.1:8765).

### Velg LLM ved oppstart

Som standard bruker API-et en OpenAI-kompatibel modellproxy for referat og sammendrag:

```bash
LLM_BACKEND=openai \
AI_PROXY_URL=https://... \
AI_PROXY_API_KEY=... \
LLM_MODELL=borealis-12b \
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
```

For lokal LLM kan samme API startes mot Ollama:

```bash
LLM_BACKEND=ollama \
OLLAMA_URL=http://localhost:11434 \
LLM_MODELL=qwen3:8b \
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
```

`LLM_MODELL` er foretrukket modellvariabel. `OLLAMA_MODELL` støttes fortsatt som bakoverkompatibelt alias i Ollama-modus.

### Valgfrie miljøvariabler

| Variabel | Standard                     | Beskrivelse |
|----------|------------------------------|-------------|
| `WHISPER_MODELL` | `NbAiLab/nb-whisper-medium`  | Modell for batch-transkripsjon |
| `LOKAL_ASR_BACKEND` | `whisper` | Lokal ASR: `whisper` eller eksperimentell `nemotron` |
| `NEMOTRON_MODELL` | `nvidia/nemotron-3.5-asr-streaming-0.6b` | Lokal Nemotron-modell |
| `NEMOTRON_ENHET` | CUDA hvis tilgjengelig, ellers CPU | `cpu` eller `cuda`; MPS er ikke validert |
| `NEMOTRON_CPU_THREADS` | `4` | CPU-traader for Nemotron |
| `WHISPER_SANNTID_MODELL` | `modeller/nb-whisper-medium` | Modell for sanntidsmodus |
| `LLM_BACKEND` | `openai` | `openai` for OpenAI-kompatibel proxy, eller `ollama` for lokal Ollama |
| `LLM_MODELL` | `borealis-12b` (`qwen3:8b` ved `LLM_BACKEND=ollama`) | LLM for møtereferat og sammendrag |
| `LLM_MAX_TOKENS` | `256` (`1024` i NAIS) | Standard svarbudsjett for proxy-baserte LLM-kall. Utfyllende sammendrag bruker et eget budsjett på 3072 tokener. Prompt og svar deler modellens kontekstvindu. |
| `LLM_CONTEXT_TOKENS` | `8192` (`32768` ved lokal Ollama, eller `OLLAMA_NUM_CTX`) | Kontekstvindu per forespørsel, inkludert instruksjoner og svar. Må samsvare med modellserveren; øker ikke serverens grense. `LLM_NUM_CTX` støttes som eldre alias. |
| `AI_PROXY_URL` | `http://127.0.0.1:9000` | OpenAI-kompatibelt LLM-endepunkt ved `LLM_BACKEND=openai` |
| `AI_PROXY_API_KEY` | tom | API-nøkkel for OpenAI-kompatibelt LLM-endepunkt |
| `OLLAMA_URL` | `http://localhost:11434`     | Ollama-endepunkt |
| `OLLAMA_MODELL` | `qwen3:8b`                   | Eldre alias for `LLM_MODELL` i Ollama-modus |
| `OLLAMA_NUM_CTX` | `32768`                      | Kontekstvindauge for LLM (tokens) |
| `ARBEIDSMAPPE` | midlertidig mappe | Mappe for lyd- og jobbstatusfiler |
| `START_LOKAL_WORKER` | `true` | Starter lokal transkripsjonsarbeider sammen med API-et |
| `TRANSKRIPSJON_BACKEND` | `local` | `local` eller `remote` modellbackend for batch-transkripsjon |
| `TRANSKRIPSJON_SERVICE_URL` | `http://127.0.0.1:9000` | URL til ekstern modellarbeider ved `TRANSKRIPSJON_BACKEND=remote` |
| `TRANSKRIPSJON_MODELL` | `nb-whisper-large` i NAIS | Modellnavn sendt til OpenAI-kompatibel transkripsjons-API |

Referater og sammendrag bruker originaltranskripsjonen direkte når den passer i
kontekstvinduet. Borealis kan telle hele den ferdig formaterte chat-prompten med
sin egen tokenizer, inkludert spesialtokener. Instruksjoner og svar har reserverte
budsjetter. Hvis tokentelleren ikke er tilgjengelig, brukes et konservativt estimat.
For store kilder hentes strukturerte kildepunkter automatisk, uten å kutte bort
første eller siste del av møtet. Kildenumre peker på uendrede originalutdrag;
sitater hentes av kode, ikke skrives eller oversettes av modellen.
Når originalen passer, brukes normalt to modellkall: ett for referatet og ett for
en kompakt kontroll av påstander, ansvar og manglende temaer. Den tidligere
setnings- og utdragsvise kontrollen brukes ikke som standard. Korreksjoner
kontrolleres på nytt før publisering. Ukjent ansvar
forblir uavklart. Ved vedvarende kontrollfeil vises en feil, ikke et ukontrollert utkast.
Målet er rundt to minutter for vanlige møter som passer i kontekstvinduet, ikke
en garantert svartid. Lange svar, kø, store kilder og rettinger kan ta lengre tid.
Kontrollen er mindre uttømmende enn tidligere. Kontrollene bruker fortsatt
Borealis og er ikke en garanti mot faktafeil; referatet må gjennomgås av en person.
Se [kildekontroll og innføring](docs/summary-grounding.md).
Nettleseren starter en bakgrunnsjobb og henter fremdrift med korte statuskall.
Ved nettverksbrudd prøves samme jobb igjen, uten å generere referatet på nytt.
Logger viser jobb-ID, start, fremdrift, ferdigstilling og feiltype, uten møteinnhold.
Resultatet er bare tilgjengelig for brukeren som startet jobben og slettes etter
samme oppbevaringstid som transkripsjonsresultater (standard seks timer).
Jobbene kjører i én API-replika, med inntil to aktive genereringer og 16 jobber
totalt i kø og under behandling. Omstart av API-et avbryter pågående generering;
en varig kø og delt lagring kreves før flere API-replikaer kan brukes.
De eldre strømmeendepunktene er fortsatt tilgjengelige for API-klienter.

Borealis-oppsettet reserverer 32 768 konteksttokener per forespørsel, fordelt på
to samtidige modellkall (65 536 totalt i llama.cpp). Kompakt kontroll har inntil
768 svartokener. Kildeuttrekk og de eldre, uttømmende kontrollene har inntil
4 096 svartokener. Andre teksthandlingers mellomnotater har
inntil 2 048 svartokener i stedet for 512. Dette gir mindre
aggressiv komprimering, men er ingen garanti mot faktafeil. Lengre notater kan
øke genereringstiden; færre modellkall kan redusere samlet ventetid.
Ved innføring må modellserveren oppdateres og minnebruk/lange forespørsler
kontrolleres før NAIS-konfigurasjonen økes. Kontekstgrensen i API-et alene
øker ikke modellserverens kapasitet. Eksisterende målinger med fire 8K-spor
gjelder ikke automatisk for dette oppsettet.
En [kapasitetstest på GCloud](docs/benchmarks/2026-10-20/borealis-context-capacity.json)
bekreftet to samtidige forespørsler med 24 845 inn-tokener hver. Svarene var bare
åtte tokener; testen dokumenterer kapasitet, ikke referatkvalitet eller ytelse for
lange svar.

«Utfyllende sammendrag» gir et tematisk møtereferat med kort oppsummering,
nummererte hovedtemaer, oppgaver, åpne spørsmål og konklusjon. Temaene følger
møtet; malen skal ikke gjette talernavn eller gjøre uklare datoer, kostnader og
juridiske vurderinger til bekreftede fakta. Lange, innholdsrike møter kan gi rundt
800-1200 ord, mens korte møter skal få kortere referater. Svarbudsjettet reserveres
før teksten eventuelt deles. Når modellens tokenteller er utilgjengelig, brukes
et estimat på to UTF-8-byte per token. Delstørrelsen reduseres automatisk dersom
modellserveren avviser kontekststørrelsen.

Ved filopplasting til modellgatewayen konverterer API-et lyden til 16 kHz mono og
sender 15-sekunders WAV-deler med ett sekund overlapp. Det gir omtrent 480 kB per
modellkall i stedet for hele originalfilen. Talerskille deler samme opptakstilstand,
tidsstempler flyttes til filens tidslinje, og overlappende tekst håndteres som ved
mikrofonopptak. Stille partier og siste lyddel beholdes. Fremdrift følger behandlet
lyd; en mislykket del gir feil på jobben fremfor et delvis resultat. Midlertidig
konvertert lyd ryddes ved avslutning. Gjeldende varighetsgrense er to timer.

### Container

Bygg et API-image uten lokale modellavhengigheter:

```bash
docker build --build-arg REQUIREMENTS=requirements/api.txt \
  --target runtime -t transkriberit-api .
```

Kjør med en egen, ikke versjonert miljøfil for modelltilkoblingen:

```bash
docker run --rm -p 127.0.0.1:8765:8765 \
  --env-file /path/to/runtime.env transkriberit-api
```

Miljøfilen må angi `TRANSKRIPSJON_BACKEND=remote`, modellarbeiderens URL og
LLM-backend med nødvendige tilganger. Adresser på vertsmaskinen er ikke
containerens `localhost`. Se miljøvariablene over og sikkerhetsoppsettet for GCP.

Bygg og kjør lokal CPU-modellarbeider som et separat image:

```bash
docker build --build-arg REQUIREMENTS=requirements/worker-cpu.txt \
  --target model-worker -t transkriberit-worker .
docker run --rm -p 127.0.0.1:9000:9000 \
  -v "$HOME/.cache/huggingface:/app/.cache/huggingface:ro" transkriberit-worker
```

Whisper-modellen må være lastet ned i den monterte Hugging Face-cachen først.
ECAPA bruker en separat `ECAPA_CACHE`, som bør cachelagres ved behov for offline
talerskille. Image-ene inkluderer ikke lokale modellfiler, opptak, rapporter eller
testdata. GCP bruker egne speech- og llama-adapterimages, ikke CPU-arbeideren.

---

## Bruk

### Batch-transkripsjon
Velg **Lydfil** under **Lydkilde** og last opp wav, mp3, m4a eller webm.
Gjennomgå transkripsjon og estimerte talere. Velg **Tekstmal** og **Generer**
for å lage et utkast fra transkripsjonen.

### Sanntidsmodus
Klikk **Start opptak** for å transkribere direkte fra mikrofon. Referatutkastet oppdateres automatisk underveis (~hvert 150. nye ord) slik at det er ferdig når møtet avsluttes.

Velg **Lydkilde** for lydfil eller mikrofon (sanntid). Bekreftelsen gjelder opptak og databehandling;
**Begrenset §14a-referat** er et separat, valgfritt formatvalg for Veiledermøte,
ikke en samtykke- eller tilgangstillatelse. Møteformål kan være Kartlegging eller
Oppfølging (standard innenfor denne malen).
Velg **Tekstmal** og **Generer** i utkastkolonnen for sammendrag eller ulike referater fra samme transkripsjon.
**Egendefinert** tar egne instruksjoner om struktur og vektlegging (maks 4000 tegn);
systemreglene om faktabasert innhold beholdes. Valgt tekstmal gjelder også live-utkast.
**Bearbeidet referat** er et separat tekstfelt og overskrives ikke ved ny generering.

Nettleseren sender 15-sekunders lyddeler over HTTP, med ett sekund overlapp.
Stille lyd beholdes, og mislykkede forespørsler prøves igjen uten å stoppe
mikrofonen. **Stopp** avslutter opptaket, mens ventende deler fortsatt behandles.
Modellens prosesseringstid og eventuell kø kommer i tillegg til buffertiden.
Det eldre WebSocket-endepunktet bruker pausegrenser etter 1,5 sekunder stillhet,
med maksimalt 15 sekunders buffer. Se [opptak og gjenoppretting](docs/operations.md#recoverable-browser-recordings).

### Rollemerking
Etter transkripsjon kan du klikke **Veileder / Bruker / Tolk**-knappene for å merke hvem som er hvem. LLM-en bruker dette for mer presise referater.

---

## Testlyd

Se [testdatakatalogen](testdata/README.md) for lydfiler, bruksområder og manglende
proveniensavklaringer. De tidligere unøyaktige referansetranskripsjonene er
fjernet fra arbeidstreet og brukes ikke som fasit. Lokale opptak og modellcacher
er ikke slettet. Sletting av en fil her fjerner den ikke fra Git-historikken.

---

## Etterlevelse og personvern

- **Databehandling følger backend-valget.** Lokal kjøring kan holde inferens på
  maskinen; NAIS/remote sender lyd og tekst til den konfigurerte modellgatewayen.
- Opplastede lydfiler lagres midlertidig og slettes etter behandlingsforsøket.
  Transkripsjons- og referatjobber lagres på API-serveren, med standard
  oppbevaring på seks timer (`JOB_RETENTION_SECONDS`) og periodisk opprydding.
- Mikrofonopptak lagres midlertidig i nettleserens IndexedDB for gjenoppretting.
  Bruk **Slett lokal lyd** når opptaket ikke skal beholdes. Opptak utløper etter
  åtte timer; når nettleseren er lukket, skjer sletting først ved et senere besøk.
  Nedlastede filer er utenfor appens sletterutiner.
- Møtereferater skal gjennomgås av veileder før bruk, jf. §15-vurdering (kommunale vs. statlige opplysninger).
- Se [møtereferat_prompt.md](møtereferat_prompt.md) for LLM-prompt-dokumentasjon.

Løsningen eies av [ao-ki-taskforce](https://github.com/orgs/navikt/teams/ao-ki-taskforce) under NAV IT.

---

## Utvikling

```bash
# Kjør med auto-reload under utvikling
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765 --reload
```

Kodestruktur:

```
frontend/
  app.py                     # FastAPI-entrypoint, statiske filer og lifespan
  routes/                    # HTTP/WebSocket-endepunkter og opptaksflyt
  static/                    # HTML, CSS, JavaScript og AudioWorklet
  compact_summary.py         # Standard kildekontroll av referater
  grounded_summary.py        # Delte kildeverktøy og uttømmende evalueringsflyt
  summary_jobs.py            # Bakgrunnsjobber for referater
worker/
  app.py                     # HTTP-basert lokal modellarbeider
  nemotron_api.py            # Cloud speech-API: Whisper og Nemotron-talerskille
  llama_api.py               # Autentisert adapter foran llama.cpp
  workers/                   # Lokal køarbeider, startet av API-et
  transkribering/            # ASR, lyddeler, talerskille og modellkø
  ollama/                    # Lokal LLM-klient
  prompts/                   # Tekstmaler, møteformål og normalisering
shared/
  core/                      # Miljøvariabler og prosess-runtime
  contracts/                 # Delte transkripsjonskontrakter
  services/                  # JobStore, modellklienter og modellautentisering
  diagnostics.py             # Strukturert logging
scripts/                     # Deploy, modellstaging, smoke- og belastningstester
images/                      # Speech-, llama-adapter- og gatewayfiler
k8s/                         # Modellmanifester, sikkerhet og administrasjon
terraform/                   # GKE, gateway, IAM og modellbucket
.nais/                       # Dev/prod-manifester for frontend/API
tests/                       # Python- og JavaScript-tester
benchmarks/                  # Lokale modell- og datasettbenchmarker
docs/                        # Drift, sikkerhet, kildekontroll og måleresultater
adr/                         # Arkitekturvalg og kostnadsgrunnlag
testdata/                    # Testlyd og samtalefiksturer; se egen README
last_ned_modeller.py          # Nedlasting av nb-whisper-modeller
konverter_modeller.py        # Konvertering til CTranslate2-format
```

### API og transkripsjonsarbeider

Som standard starter API-et fortsatt en lokal transkripsjonsarbeider i egen prosess.
Dette holder lokal utvikling enkel:

```bash
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765 --reload
```

For å kjøre API og modellarbeider som separate tjenester uten delt filsystem,
start disse kommandoene i hvert sitt terminalvindu:

```bash
python -m uvicorn worker.app:app --host 127.0.0.1 --port 9000
TRANSKRIPSJON_BACKEND=remote TRANSKRIPSJON_SERVICE_URL=http://127.0.0.1:9000 \
  LLM_BACKEND=ollama \
  python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
```

I denne modusen laster API-et opp lydfilen til modellarbeideren over HTTP og lagrer
resultatet lokalt i `JobStore`.

### Benchmarking

Mål ytelse på `/sammendrag/stream`-endepunktet (krever at serveren kjører):

```bash
python -m benchmarks.sammendrag
python -m benchmarks.sammendrag --modell qwen3.6:35b --runder 3 --vis-sammendrag
```

Test LLM-konfigurasjonar direkte mot Ollama (uten server):

```bash
python -m benchmarks.optimalisering
python -m benchmarks.optimalisering --modeller qwen3:8b qwen3.5:9b
```

### Smoke-test uten GPU

Sjekker appens helse, aktiv konfigurasjon og referat-scenarier uten å kjøre transkripsjon
eller LLM-generering:

```bash
APP_URL=http://127.0.0.1:8765 ./scripts/smoke-test-app.sh
```

Mot deployet miljø:

```bash
APP_URL=https://ao-ki-transkribering.intern.nav.no ./scripts/smoke-test-app.sh
```

Sett `RUN_LLM_STATUS=true` hvis modellgatewayen også skal sjekkes.

### Testing

Unit-tester (krever ikke Ollama eller server):

```bash
pytest
```

Integrasjonstester (krever Ollama / faster-whisper-modell):

```bash
pytest -m integration                                                    # alle
python tests/integration/test_referat.py                                 # møtereferat
python tests/integration/test_referat.py --fil testdata/conversation_nb.md
python tests/integration/test_referat.py --debug                         # vis råe chunks
python tests/integration/test_sanntid.py                                 # sanntidsmodus
python tests/integration/test_sanntid.py --fil testdata/king.mp3
```

## Speech-model comparison (experimental)

The standalone benchmark does not change the app or Kubernetes deployments:

```bash
.venv/bin/python -m pip install -r requirements/benchmark-speech.txt
ECAPA_CACHE="$PWD/.cache/speechbrain" .venv/bin/python -m benchmarks.speech \
  --device cpu --seconds 60 --output .cache/benchmarks/speech-cpu.json
```

It compares raw NB-Whisper Large and NVIDIA Nemotron 3.5 ASR on identical clips
from `testdata/audio`, and ECAPA with Nemotron 3 Diarization on
`testdata/tre_stemmer_test.wav`. FFmpeg and the worker dependencies are required.
The initial model downloads are cached by Hugging Face. Optional benchmark
dependencies are not installed in production images.

Nemotron diarization currently needs an unreleased Transformers implementation.
The tested development revision can be installed separately, without replacing
the app's packages:

```bash
.venv/bin/python -m pip install --no-deps --target .cache/speech-runtime \
  -r requirements/benchmark-speech-runtime.txt
PYTHONPATH="$PWD/.cache/speech-runtime" .venv/bin/python -m benchmarks.speech \
  --device cpu --seconds 60 --output .cache/benchmarks/speech-cpu-validated.json
```

The JSON report contains generated transcripts, speaker timelines, inference/load
times, devices, and model revisions. Existing reference transcripts are not read
or used, including for clip boundaries. Transcription accuracy and diarization
error rate are deliberately unscored for these existing files. Quality must be checked against the audio,
not by treating either model's output as ground truth. Timings are offline
measurements, not live-streaming latency, and should only be compared on the same
hardware.

Use `--seconds 0` for complete recordings, `--samples test002` for one ASR sample,
or `--backends nemotron nemotron-diar` to run only the NVIDIA candidates.
`--device cuda` supports a separate CUDA benchmark environment; do not run a
second GPU workload against the active serving node without planning memory and
request contention. ECAPA remains CPU-only to match the app.

The public ASR model explicitly receives `nb-NO`. Its Nynorsk support requires
adaptation. NbAiLab's Norwegian Nemotron beta needs separate access and rights
review; it is not selected automatically. Model/runtime integration and streaming
benchmarks remain separate from this evaluation.

The earlier reference-based WER and deletion rates have been withdrawn because
the supplied transcripts are not trusted. They do not establish which ASR model
is more accurate. On the full 90-second three-speaker recording, ECAPA and
Nemotron diarization both found three speakers, in 3.08s and 0.48s respectively
(excluding loading). Speaker-boundary correctness has not been scored.

Sources: [Nemotron ASR model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b),
[Nemotron diarization model card](https://huggingface.co/nvidia/Nemotron-3-Diarization).

### Reviewed Norwegian podcast test data

Prepare 30 non-overlapping podcast clips from the official NB Samtale test split
(24 Bokmal, 6 Nynorsk). Downloads total about 230 MB, are checksum-verified, and
stay under the ignored `.cache/` directory. Only selected audio is extracted;
subsequent preparation reuses the archives.

```bash
.venv/bin/python -m benchmarks.nb_samtale
PYTHONPATH="$PWD/.cache/speech-runtime" .venv/bin/python -m benchmarks.speech \
  --manifest .cache/nb-samtale/podcast-test.json --backends whisper nemotron \
  --seconds 0 --device cpu --threads 4 \
  --output .cache/benchmarks/nb-samtale-cpu.json
```

This explicitly selected corpus has human-corrected, second-person-reviewed
references. It does not use the removed user-supplied reference transcripts.
The manifest records the
dataset revision, source metadata, transcript variant and audio checksums.
The report scores orthographic references with pooled word-error and deletion
rates, separately for Bokmal and Nynorsk. Normalization ignores case and
punctuation, but does not equate numbers with spoken number words or dialect
forms with standardized words. References have not been independently checked
against the audio here, and training-data overlap is unverified: treat this as
a diagnostic comparison, not a definitive model ranking. Nynorsk is a written-norm
challenge set: Whisper keeps `language=no` and NVIDIA uses `nb-NO`, not Nynorsk-targeted
decoding. Those scores are not a supported-language accuracy claim for NVIDIA.
Short isolated clips do not test streaming latency or continuous diarisation.

See [dataset research and limitations](docs/norwegian-speech-testdata.md) and the
[official NB Samtale dataset card](https://huggingface.co/datasets/Sprakbanken/nb_samtale).

### Local Nemotron

Experimental Nemotron ASR can replace local Whisper for both file uploads and
recording. ECAPA speaker diarisation and the selected LLM are unchanged.
The normal local app command still defaults to Whisper. GCP also uses
NB-Whisper; the Nemotron runtime remains an experimental alternative.

Use the isolated optional runtime described above. On first setup, cache the ASR
weights before starting the offline app:

```bash
.venv/bin/python -m pip install --no-deps --target .cache/speech-runtime \
  -r requirements/benchmark-speech-runtime.txt
PYTHONPATH="$PWD/.cache/speech-runtime" .venv/bin/python -c \
  'from huggingface_hub import snapshot_download; snapshot_download("nvidia/nemotron-3.5-asr-streaming-0.6b", allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"])'
```

After setup, one command starts the local app with Nemotron and local Ollama
(`borealis:12b`, which must be installed in Ollama):

```bash
./scripts/run-local-nemotron.sh
# Or another port when the normal app is already running:
PORT=8766 ./scripts/run-local-nemotron.sh
```

Set `LLM_BACKEND`, `LLM_MODELL` and the corresponding endpoint variables before
the command to retain another LLM setup. The launcher does not read `.env`.
For rollback, stop this instance and use the normal app command with
`LOKAL_ASR_BACKEND=whisper`.

This is buffered recording transcription, not continuous cache-aware streaming.
Long uploads use bounded windows with audio context; short speaker turns are
preserved. RNNT emission timestamps and the resulting speaker alignment are
approximate and have not been independently validated. The app returns a warning
with upload results. This stock model performed substantially worse than
NB-Whisper on our podcast subset, so review its output before using it for minutes
or summaries. The Transformers runtime remains experimental.

### GCP-modeller

The speech GPU serves `nb-whisper-large` with CTranslate2 FP16 and word timestamps,
alongside `nvidia/Nemotron-3-Diarization` for speaker labels. Transformers remains
an alternative ASR backend. Borealis Q8_0 uses llama.cpp and an authenticated API
adapter on the other GPU. Both retain their TLS endpoints and allocated L4s.
The NAIS model setting, gateway routing and gateway model allowlist must agree.

Nemotron ASR was rolled back on 2026-10-13 after Norwegian audio produced
Swedish-like output despite the `nb-NO` language prompt. The same synthetic
Norwegian clip passed through the authenticated NAIS-to-GCP path after restoring
Whisper. This is a smoke test, not an accuracy evaluation of real meetings.

Nemotron ASR remains experimental and is not used for cloud transcription.
The speech image uses `SPEECH_ASR_BACKEND=whisper` and downloads both Whisper and
the separate Nemotron diarization checkpoint from GCS. Build it with
`IMAGE=europe-west4-docker.pkg.dev/ao-ki-taskforce-prod-2472/vllm/whisper-diarization:20261013-v2 bash scripts/build-nemotron-image.sh`.

Recordings use at most 15-second windows, with one second of overlap on forced
cuts. Quiet audio is retained; energy detection chooses pause boundaries only.
Speaker state is isolated per recording UUID, expires after five idle minutes,
and is bounded to eight active recordings. It is not persisted across restarts.
Repeated delivery of the latest chunk is idempotent. Speaker labels are estimates,
not identities; users must review them, especially around overlapping speech.

Model switches restart the speech service and NAIS model-setting changes restart
the API. Finish recordings before a cutover; active WebSockets can disconnect.
