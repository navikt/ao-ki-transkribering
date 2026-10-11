# ao-ki-transkribering

Lokalt transkriberings- og referatverktøy for NAV §14a-brukermøter.

Bruker [nb-whisper](https://huggingface.co/NbAiLab/nb-whisper-medium) for norsk tale-til-tekst,
høyttalerdiarisering (identifiserer hvem som sier hva), og
[Ollama](https://ollama.com) med [qwen3:32b](https://ollama.com/library/qwen3.6) for automatisk
møtereferat og sammendrag etter NAVs §14a-mal.

**Ingen data forlater maskinen.** Alt kjøres lokalt.

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

Dette laster ned `nb-whisper-medium` (~2,8 GB) til lokal Hugging Face-cache.
Kjøres kun én gang – alt fungerer offline etterpå.

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

### Last ned qwen3:32b

```bash
ollama pull qwen3:32b
```

Modellen er ~23 GB. Nedlasting tar tid avhengig av internettforbindelsen.

> **Mindre maskin?** Bruk en mindre modell og sett miljøvariabelen:
> ```bash
> export OLLAMA_MODELL=qwen3:8b    # ~5 GB, raskere men mer nynorsk
> ollama pull qwen3:8b
> ```
> Se [ollama.com/library](https://ollama.com/library) for tilgjengelige modeller.

---

## 5. Start applikasjonen

```bash
source .venv/bin/activate   # hvis ikke allerede aktivert
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
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
| `AI_PROXY_URL` | `http://127.0.0.1:9000` | OpenAI-kompatibelt LLM-endepunkt ved `LLM_BACKEND=openai` |
| `AI_PROXY_API_KEY` | tom | API-nøkkel for OpenAI-kompatibelt LLM-endepunkt |
| `OLLAMA_URL` | `http://localhost:11434`     | Ollama-endepunkt |
| `OLLAMA_MODELL` | `qwen3:8b`                   | Eldre alias for `LLM_MODELL` i Ollama-modus |
| `OLLAMA_NUM_CTX` | `32768`                      | Kontekstvindauge for LLM (tokens) |
| `ARBEIDSMAPPE` | midlertidig mappe | Mappe for lyd- og jobbstatusfiler |
| `START_LOKAL_WORKER` | `true` | Starter lokal transkripsjonsarbeider sammen med API-et |
| `TRANSKRIPSJON_BACKEND` | `local` | `local` eller `remote` modellbackend for batch-transkripsjon |
| `TRANSKRIPSJON_SERVICE_URL` | `http://127.0.0.1:9000` | URL til ekstern modellarbeider ved `TRANSKRIPSJON_BACKEND=remote` |
| `TRANSKRIPSJON_MODELL` | `nb-whisper-large` ved remote | Modellnavn sendt til OpenAI-kompatibel transkripsjons-API |

### Container

Bygg image:

```bash
docker build -t ao-ki-transkribering .
```

Kjør API-et:

```bash
docker run --rm -p 8765:8765 ao-ki-transkribering
```

Kjør samme image som HTTP-basert modellarbeider:

```bash
docker run --rm -p 9000:9000 ao-ki-transkribering \
  python -m uvicorn apps.model_worker.app:app --host 0.0.0.0 --port 9000
```

Image-et inkluderer ikke lokale modellfiler fra `modeller/` eller testdata. De bør
monteres eller håndteres som eget modellvolum når API og modellarbeider splittes i
separate containere.

---

## Bruk

### Batch-transkripsjon
Last opp en lydfil (wav, mp3, m4a, webm). Applikasjonen transkriberer og identifiserer talere. Klikk **Skriv møtereferat** for å generere referat etter §14a-malen.

### Sanntidsmodus
Klikk **Start opptak** for å transkribere direkte fra mikrofon. Referatutkastet oppdateres automatisk underveis (~hvert 150. nye ord) slik at det er ferdig når møtet avsluttes.

Velg **Lydkilde** for lydfil eller mikrofon (sanntid). Bekreftelsen gjelder opptak og databehandling;
§14 a/§15-bekreftelsen vises som en separat avkryssing og brukes når den er relevant.
Velg **Tekstmal** og **Generer** i utkastkolonnen for sammendrag eller ulike referater fra samme transkripsjon.
**Egendefinert** tar egne instruksjoner om struktur og vektlegging (maks 4000 tegn);
systemreglene om faktabasert innhold beholdes. Valgt tekstmal gjelder også live-utkast.
**Bearbeidet referat** er et separat tekstfelt og overskrives ikke ved ny generering.

Lyden sendes til transkripsjon etter 1,5 sekunder stillhet eller senest etter
10 sekunder. Ved tvungne kutt beholdes 1 sekund overlapp som kontekst;
matchende ord ved grensene fjernes fra visningen. Modellens prosesseringstid
kommer i tillegg til buffertiden.

### Rollemerking
Etter transkripsjon kan du klikke **Veileder / Bruker / Tolk**-knappene for å merke hvem som er hvem. LLM-en bruker dette for mer presise referater.

---

## Testlyd

`testdata/`-mappen inneholder to NRK-opptak for testing:
- `king.mp3` – én stemme
- `tre_stemmer_test.wav` – podcast med tre stemmer (tester diarisering)

---

## Etterlevelse og personvern

- **Ingen data forlater maskinen.** Alle modeller kjøres lokalt.
- Lydfilen slettes automatisk etter transkripsjon.
- Transkripsjonen eksisterer kun i nettleserøkten – ingenting lagres på server.
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
server.py                    # Bakoverkompatibel ASGI-entrypoint (server:app)
app_api.py                   # Bakoverkompatibel API-entrypoint (app_api:app)
app_factory.py               # Bakoverkompatibel import for create_app
settings.py                  # Bakoverkompatibel import for core.settings
runtime.py                   # Bakoverkompatibel import for core.runtime
worker_transkripsjon.py      # Eksplisitt entrypoint for transkripsjonsarbeider
model_worker_app.py          # Bakoverkompatibel modellarbeider-entrypoint
apps/
  api/app.py                 # API-app: statiske filer, lifespan og router-registrering
  model_worker/app.py        # HTTP-basert modellarbeider
api/                         # HTTP/WebSocket-endepunkter
core/
  settings.py                # Miljøvariabler og runtime-konfigurasjon
  runtime.py                 # Arbeiderprosess, kø og delt JobStore
contracts/
  transcription.py           # Delte HTTP-kontrakter mellom API og modellarbeider
services/
  jobs.py                    # Filbasert jobbtilstand og atomiske statusoppdateringer
  transkripsjon_backend.py   # Kontrakt for transkripsjonsbackends
workers/
  transkripsjon.py           # Køarbeider for transkripsjonsjobber og jobbstatus
static/
  index.html                 # Frontend-markup
  styles.css                 # Frontend-stiler
  app.js                     # Frontend-logikk
  audio-processor.js         # AudioWorklet for sanntids-PCM-prosessering
transkribering/
  batch.py                   # Lokal batchmodell: nb-whisper + diarisering
  sanntid.py                 # Sanntidstranskripsjon med faster-whisper
  diarisering.py             # Høyttalerdiarisering (pyannote)
  hallusinasjon.py           # Filtrering av hallusinerte segmenter
  konstanter.py              # Felles konstanter
ollama/
  klient.py                  # Ollama HTTP-klient
prompts/
  motereferat.py             # §14a-møtereferat-prompt
  normalisering.py           # Normaliseringsprompt
  estimat.py                 # Tidsestimat-prompt
benchmarks/
  sammendrag.py              # Benchmark av /sammendrag/stream mot server
  optimalisering.py          # Matrise-benchmark av LLM-konfigurasjonar mot Ollama
last_ned_modeller.py         # Nedlasting av nb-whisper-modeller
konverter_modeller.py        # Konvertering til CTranslate2-format (sanntidsmodus)
møtereferat_prompt.md        # Dokumentasjon av LLM-prompts
benchmarks.md                # Benchmark-resultater
testdata/                    # Testlydfiler (NRK, offentlig)
```

### API og transkripsjonsarbeider

Som standard starter API-et fortsatt en lokal transkripsjonsarbeider i egen prosess.
Dette holder lokal utvikling enkel:

```bash
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765 --reload
```

For å kjøre API og transkripsjonsarbeider som separate prosesser:

```bash
START_LOKAL_WORKER=false python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
python -m worker_transkripsjon
```

Dette er forberedelse til å flytte modellene til en egen prosess eller container.

Alternativt kan API-et bruke en HTTP-basert modellarbeider uten delt filsystem:

```bash
python -m uvicorn apps.model_worker.app:app --host 127.0.0.1 --port 9000
TRANSKRIPSJON_BACKEND=remote TRANSKRIPSJON_SERVICE_URL=http://127.0.0.1:9000 \
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
references. It does not use `testdata/transcriptions`. The manifest records the
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
The normal app command still defaults to Whisper; cloud manifests are unchanged.

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
or summaries. Its development Transformers runtime is not a production rollout.
