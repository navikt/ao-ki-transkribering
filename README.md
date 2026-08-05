# ao-ki-transkribering

Lokalt transkriberings- og referatverktøy for NAV §14a-brukermøter.

Bruker [nb-whisper](https://huggingface.co/NbAiLab/nb-whisper-medium) for norsk tale-til-tekst,
høyttalerdiarisering (identifiserer hvem som sier hva), og
[Ollama](https://ollama.com) med [qwen3:32b](https://ollama.com/library/qwen3.6) for automatisk
møtereferat og sammendrag etter NAVs §14a-mal.

**Ingen data forlater maskinen.** Alt kjøres lokalt.

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
python -m uvicorn apps.api.app:app --host 127.0.0.1 --port 8765
```

Åpne nettleser på [http://127.0.0.1:8765](http://127.0.0.1:8765).

### Valgfrie miljøvariabler

| Variabel | Standard                     | Beskrivelse |
|----------|------------------------------|-------------|
| `WHISPER_MODELL` | `NbAiLab/nb-whisper-medium`  | Modell for batch-transkripsjon |
| `WHISPER_SANNTID_MODELL` | `modeller/nb-whisper-medium` | Modell for sanntidsmodus (lokal) |
| `OLLAMA_URL` | `http://localhost:11434`     | Ollama-endepunkt |
| `OLLAMA_MODELL` | `qwen3:8b`                   | LLM for møtereferat |
| `OLLAMA_NUM_CTX` | `32768`                      | Kontekstvindauge for LLM (tokens) |
| `STT_BACKEND` | `lokal`                      | `lokal` (nb-whisper) eller `soniox` (sky-STT) |
| `SONIOX_API_KEY` | —                            | API-nøkkel for Soniox (kun ved `STT_BACKEND=soniox`) |
| `ARBEIDSMAPPE` | midlertidig mappe | Mappe for lyd- og jobbstatusfiler |
| `START_LOKAL_WORKER` | `true` | Starter lokal transkripsjonsarbeider sammen med API-et |
| `TRANSKRIPSJON_BACKEND` | `local` | `local` eller `remote` modellbackend for batch-transkripsjon |
| `TRANSKRIPSJON_SERVICE_URL` | `http://127.0.0.1:9000` | URL til ekstern modellarbeider ved `TRANSKRIPSJON_BACKEND=remote` |

Eksempel med Soniox:
```bash
STT_BACKEND=soniox SONIOX_API_KEY=<din-nøkkel> python -m uvicorn apps.api.app:app --host 127.0.0.1 --port 8765
```

---

## Bruk

### Batch-transkripsjon
Last opp en lydfil (wav, mp3, m4a, webm). Applikasjonen transkriberer og identifiserer talere. Klikk **Skriv møtereferat** for å generere referat etter §14a-malen.

### Sanntidsmodus
Klikk **Start opptak** for å transkribere direkte fra mikrofon. Referatutkastet oppdateres automatisk underveis (~hvert 150. nye ord) slik at det er ferdig når møtet avsluttes.

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
python -m uvicorn apps.api.app:app --host 127.0.0.1 --port 8765 --reload
```

Kodestruktur:

```
server.py                    # Bakoverkompatibel ASGI-entrypoint (server:app)
app_api.py                   # Bakoverkompatibel API-entrypoint (app_api:app)
app_factory.py               # Bakoverkompatibel import for create_app
settings.py                  # Miljøvariabler og runtime-konfigurasjon
runtime.py                   # Arbeiderprosess, kø og delt JobStore
worker_transkripsjon.py      # Eksplisitt entrypoint for transkripsjonsarbeider
model_worker_app.py          # Bakoverkompatibel modellarbeider-entrypoint
apps/
  api/app.py                 # API-app: statiske filer, lifespan og router-registrering
  model_worker/app.py        # HTTP-basert modellarbeider
api/                         # HTTP/WebSocket-endepunkter
kontrakter/
  transkripsjon.py           # Delte HTTP-kontrakter mellom API og modellarbeider
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
python -m uvicorn apps.api.app:app --host 127.0.0.1 --port 8765 --reload
```

For å kjøre API og transkripsjonsarbeider som separate prosesser:

```bash
START_LOKAL_WORKER=false python -m uvicorn apps.api.app:app --host 127.0.0.1 --port 8765
python -m worker_transkripsjon
```

Dette er forberedelse til å flytte modellene til en egen prosess eller container.

Alternativt kan API-et bruke en HTTP-basert modellarbeider uten delt filsystem:

```bash
python -m uvicorn apps.model_worker.app:app --host 127.0.0.1 --port 9000
TRANSKRIPSJON_BACKEND=remote TRANSKRIPSJON_SERVICE_URL=http://127.0.0.1:9000 \
  python -m uvicorn apps.api.app:app --host 127.0.0.1 --port 8765
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
