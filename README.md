# ao-ki-transkribering

Lokalt transkriberings- og referatverktøy for NAV §14a-brukermøter.

Bruker [nb-whisper](https://huggingface.co/NbAiLab/nb-whisper-medium) for norsk tale-til-tekst,
høyttalerdiarisering (identifiserer hvem som sier hva), og
[Ollama](https://ollama.com) med [qwen3:32b](https://ollama.com/library/qwen3) for automatisk
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

Modellen er ~20 GB. Nedlasting tar tid avhengig av internettforbindelsen.

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
uvicorn server:app --host 127.0.0.1 --port 8765
```

Åpne nettleser på [http://127.0.0.1:8765](http://127.0.0.1:8765).

### Valgfrie miljøvariabler

| Variabel | Standard                     | Beskrivelse |
|----------|------------------------------|-------------|
| `WHISPER_MODELL` | `NbAiLab/nb-whisper-medium`  | Modell for batch-transkripsjon |
| `WHISPER_SANNTID_MODELL` | `modeller/nb-whisper-medium` | Modell for sanntidsmodus |
| `OLLAMA_URL` | `http://localhost:11434`     | Ollama-endepunkt |
| `OLLAMA_MODELL` | `qwen3:32b`                  | LLM for møtereferat |

Eksempel:
```bash
OLLAMA_MODELL=qwen3:8b uvicorn server:app --host 127.0.0.1 --port 8765
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
uvicorn server:app --host 127.0.0.1 --port 8765 --reload
```

Kodestruktur:

```
server.py              # FastAPI-backend: transkripsjon, diarisering, Ollama-integrasjon
static/
  index.html           # Enkeltside-frontend (HTML/CSS/JS)
  audio-processor.js   # AudioWorklet for sanntids-PCM-prosessering
last_ned_modeller.py   # Nedlasting av nb-whisper-modeller
konverter_modeller.py  # Konvertering til CTranslate2-format (sanntidsmodus)
møtereferat_prompt.md  # Dokumentasjon av LLM-prompts
testdata/              # Testlydfiler (NRK, offentlig)
```
