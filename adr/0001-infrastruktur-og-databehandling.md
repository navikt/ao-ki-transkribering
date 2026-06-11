# ADR-0001: Infrastruktur og databehandling for transkripsjon av §14a-møter

**Status:** Forslag  
**Dato:** 2026-06-11  
**Forfattere:** ao-ki-taskforce  
**Gjeldende løsning:** Alternativ 1 (lokal kjøring)

---

## Kontekst

NAV-veiledere gjennomfører arbeidsevnevurderingsmøter etter nav-loven §14a. Disse møtene
inneholder sensitive personopplysninger, herunder opplysninger om helse, økonomi og
arbeidssituasjon — i mange tilfeller særkategori-opplysninger etter GDPR art. 9.

Et §14a-møte er et **felles møte** mellom statlig og kommunal del av NAV-kontoret (delt
behandlingsansvar). Møtet kan dermed inneholde opplysninger som tilhører to ulike
behandlingsansvarlige:

- **Statlig NAV** (Arbeids- og velferdsdirektoratet): §14a-vedtak, arbeidsevnevurdering,
  dagpenger, AAP, uføretrygd, arbeidsrettet oppfølging.
- **Kommunal del av NAV-kontoret**: sosialhjelp, bostøtte, kommunale boliger
  (hjemlet i lov om sosiale tjenester, nav-loven §15).

Opplysninger som hører til den kommunale behandlingsansvarlige **kan ikke lagres i NAVs
statlige systemer** (Modia, Arena, aktivitetsplanen).

Verktøyet behandler:
1. **Lydfil** — opptak av samtalen (svært sensitiv, transient)
2. **Transkripsjon** — tekstlig gjengivelse av samtalen (sensitiv)
3. **Møtereferat** — veilederens strukturerte referat for aktivitetsplanen (statlig del)

---

## Rettslig grunnlag

| Behandling | Hjemmel |
|-----------|---------|
| Lyd- og transkripsjonsbehandling | GDPR art. 6 (1) e + nav-loven §14a (arbeidsevnevurdering) |
| Særkategori-opplysninger (helse m.m.) | GDPR art. 9 (2) b (arbeids- og sosialrettslig hjemmel) |
| Generering av referat i aktivitetsplanen | nav-loven §14a jf. arbeids- og velferdsforvaltningsloven §4 |
| Lydopptak av tredjepart (bruker) | Krever informasjon til bruker; samtykke er **ikke** behandlingsgrunnlag |

**Viktig:** Lydfilen er et hjelpemiddel for transkripsjon — ikke et arkivdokument.
Den skal **ikke** lagres og har ingen selvstendig rettslig verdi etter at transkripsjonen
er generert.

---

## Alternativer vurdert

### Alternativ 1 — Lokal kjøring på veilederens maskin *(gjeldende)*

```
[Mikrofon] → [Nettleser] → [Lokal server (Python/FastAPI)]
                                    ↓
                          [nb-whisper (lokalt)]
                                    ↓
                          [Ollama LLM (lokalt)]
                                    ↓
                          [Referat i nettleser]
                                    ↓
                          [Veileder kopierer til Modia]
```

**Teknisk:** Python FastAPI + nb-whisper + Ollama, kjøres på veilederens arbeidsmaskin
eller en dedikert lokal maskin på kontoret.

**Faktisk enhetsbruk (bekreftet):**

| Komponent | Teknologi | Apple Silicon | NVIDIA |
|-----------|-----------|---------------|--------|
| Batch-transkripsjon | transformers + PyTorch | MPS (GPU) ✅ | CUDA (GPU) ✅ |
| Sanntid-transkripsjon | faster-whisper / CTranslate2 | **CPU** ⚠️ | CUDA (GPU) ✅ |
| Møtereferat (Ollama) | qwen3:32b | Metal (GPU) ✅ | CUDA (GPU) ✅ |

CTranslate2 (brukt av faster-whisper) støtter ikke Apple Metal/MPS — `device="auto"`
faller tilbake til CPU på alle Mac-er. Sanntidstranskripsjon kjøres derfor på CPU
selv på Apple M-chips. Dette er akseptabelt i praksis: CTranslate2 er svært
CPU-effektivt (int8-kvantisert), og 10-sekunders lydsegmenter transkriberes på ~1–3 sek.

**Mulig forbedring — mlx-whisper:**
`mlx-whisper` bruker Apples MLX-rammeverk og utnytter Metal GPU fullt ut på Apple Silicon.
nb-whisper-modellene er ikke pre-konvertert til MLX-format i `mlx-community` per juni 2026,
men kan konverteres manuelt. Kvaliteten på norsk vil avhenge av om de norsk-finjusterte
vektene bevares gjennom konverteringen — dette er uavklart og bør testes før eventuell
innføring.

**Fordeler:**
- Ingen data forlater maskinen — ingen databehandleravtale nødvendig
- Ingen nettverkskrav under møtet
- Enklest mulig GDPR-profil: transient behandling, ingen ekstern databehandler
- §15-risikoen er minimal — referatet gjennomgås av veileder før bruk

**Ulemper:**
- Krever dedikert maskinvare per kontor (ikke nødvendigvis GPU — CPU fungerer for sanntid)
- Vanskelig å skalere til mange kontor
- Ingen sentral oppdatering av modeller
- Avhengig av Ollama installert lokalt

**Juridisk vurdering:**
Behandlingsgrunnlaget er uproblematisk — all behandling skjer på NAVs eget utstyr av
NAV-ansatt i forbindelse med §14a-møtet. Ingen databehandler (jf. GDPR art. 28) er
involvert. §15-håndteringen ivaretas ved at veileder gjennomgår referatet manuelt.

---

### Alternativ 2 — Sentral server i NAVs on-premises infrastruktur (FSS)

```
[Mikrofon] → [Nettleser] → [Internett/VPN] → [GPU-server i FSS]
                                                      ↓
                                            [nb-whisper + Ollama]
                                                      ↓
                                            [Transkripsjon/referat]
                                                      ↓
                                    [Returner til veilederens nettleser]
```

**Teknisk:** Dedikert GPU-server i NAVs Fagsystemsone (FSS) med NVIDIA A100/H100.
Tilgang via NAVs interne nett eller Citrix/VDI.

**Fordeler:**
- Data forlater ikke NAVs infrastruktur — ingen ekstern databehandler
- Kan deles av mange veiledere
- Sentral modell-oppdatering

**Ulemper:**
- Høy CAPEX (GPU-server koster 500 000–1 500 000 kr)
- Lang anskaffelsestid (6–18 måneder)
- Krever driftsorganisasjon og sikkerhetsgodkjenning for FSS
- Nettverkslatens kan påvirke sanntidskvalitet

**Juridisk vurdering — ⚠️ Vesentlige utfordringer:**

**Delt behandlingsansvar og kommunal taushetsplikt**

Et §14a-møte er et *felles møte* med delt behandlingsansvar mellom statlig NAV
(Arbeids- og velferdsdirektoratet) og den kommunale delen av NAV-kontoret. Lydopptaket
vil uunngåelig inneholde opplysninger som faller inn under den kommunale behandlingsansvarliges
ansvarsområde (sosialhjelp, bostøtte, kommunale tjenester etter lov om sosiale tjenester).

Dersom lydopptaket sendes til en **sentral statlig server i FSS**, oppstår følgende problemer:

1. **Kommunens samtykke/behandlingsgrunnlag:** Den kommunale behandlingsansvarlige
   (kommunen) har ikke gitt behandlingsgrunnlag for at statlig NAV sentralt skal
   prosessere lydopptak som inneholder kommunal taushetsbelagt informasjon. Dette kan
   kreve enten lovhjemmel, databehandleravtale, eller delt behandlingsansvaravtale (jf.
   GDPR art. 26) med hver enkelt kommune — potensielt 200+ avtaler.

2. **Kommunal taushetsplikt:** Opplysninger om sosialhjelp, bostøtte og kommunale
   boliger er undergitt kommunal taushetsplikt (forvaltningsloven § 13, lov om sosiale
   tjenester § 44). Disse opplysningene kan ikke behandles i statlige systemer uten
   eksplisitt hjemmel.

3. **Kontorsperrer mellom NAV-kontor:** NAVs statlige systemer har informasjonsskiller
   (kontorsperrer) mellom geografiske kontor — en veileder på Kontor A skal ikke ha
   tilgang til opplysninger fra Kontor B. En sentral FSS-server må håndheve disse
   skillene også for transkripsjoner og lydopptak, noe som krever et aksessregime som
   i praksis er like komplekst som et eget system per kontor.

4. **Innsynsrett og behandlingsregister:** Opplysningene vil måtte registreres i NAVs
   behandlingsregister med alle tilhørende rettigheter (innsyn, retting, sletting,
   jf. GDPR art. 12–22). En sentral server med data fra alle kontor øker omfanget
   vesentlig og krever robuste kassasjonsrutiner.

**Konklusjon:** Alternativ 2 kan i utgangspunktet fungere for den *statlige* delen av
behandlingen, men krever avklaring med hvert enkelt kommunalt NAV-kontor om behandling
av kommunalt taushetspliktig informasjon. Dette er en juridisk og organisatorisk barriere
som gjør alternativet vanskelig å realisere i praksis uten lovendring eller individuelle
kommuneavtaler.

**Sammenligning med Alternativ 1:** Den lokale løsningen (Alt 1) unngår disse utfordringene
fordi behandlingen skjer på maskinvare kontrollert av det lokale NAV-kontoret, der begge
behandlingsansvarlige (stat og kommune) er fysisk til stede og kontrollerer prosessen.

---

### Alternativ 3 — NAIS-plattformen (GKE, CPU-only, europe-north1)

```
[Mikrofon] → [Nettleser] → [ID-porten/Azure AD] → [NAIS-app (GKE)]
                                                          ↓
                                                [nb-whisper (CPU)]
                                                          ↓
                                                [Ollama/ekstern LLM]
                                                          ↓
                                                [Referat returneres]
```

**Teknisk:** Standard NAIS-applikasjon på GKE i `europe-north1` (Finland). Ingen GPU —
nb-whisper kjøres på CPU med CTranslate2 (faster-whisper).

**Viktig observasjon:** Den lokale løsningens sanntidstranskripsjon kjøres allerede på
CPU (CTranslate2 støtter ikke MPS/Metal). Ytelsen som er observert lokalt — ~1–3 sek
per 10-sekunders segment — er dermed representativ for hva en NAIS CPU-pod vil levere,
forutsatt sammenlignbar CPU-ytelse. NAIS-pod med f.eks. 4–8 vCPU vil ligge i samme
størrelsesorden.

**Antatt ytelse på NAIS (nb-whisper-medium, CTranslate2 int8, 4 vCPU):**

| Brukstilfelle | Estimert latens |
|--------------|----------------|
| Sanntid (10-sek segment) | 2–5 sek ✅ akseptabelt |
| Batch (45-min møte) | 8–15 min ⚠️ ikke i møtet |
| Batch (45-min møte, nb-whisper-small) | 3–6 min ⚠️ |

**Fordeler:**
- Fullt NAIS-kompatibelt — kan driftes av eksisterende plattformteam
- Skalerbart — mange samtidige veiledere
- Kjent sikkerhetsprofil (NAIS er godkjent for personopplysninger)
- Lav driftskostnad (ingen GPU-nodepool)
- **Sanntidsmodus er realistisk** basert på observert CPU-ytelse lokalt

**Ulemper:**
- Batch-transkripsjon av lange møter er treg (ikke egnet for post-møte-bruk)
- Ollama med store LLM-er (32b) krever mye RAM — må trolig bruke mindre modell (8b)
  eller ekstern LLM-tjeneste for møtereferat
- NAIS dokumenterer ikke GPU-støtte per juni 2026

**Juridisk vurdering:**
GCP (Google) er databehandler etter GDPR art. 28. NAV må ha en gyldig
databehandleravtale (DPA) med Google Cloud. NAV har rammeavtale med GCP via
Digitaliseringsrundskrivet. `europe-north1` (Finland) er innenfor EU/EØS — ingen
overføring til tredjeland (jf. GDPR kap. V). §15-håndteringen krever samme manuelle
gjennomgang som alternativ 1. Lydopptaket bør ikke lagres i GCP-lagring — kun
transienten behandling i minnet (RAM) under transkripsjon.

---

### Alternativ 4 — GKE med GPU-nodepool i europe-north1 *(anbefalt for sky)*

```
[Mikrofon] → [Nettleser] → [ID-porten/Azure AD] → [GKE Ingress]
                                                          ↓
                                            [Transkriberingstjeneste]
                                          [nb-whisper (NVIDIA L4 GPU)]
                                                          ↓
                                            [Referattjeneste (Ollama)]
                                                          ↓
                                     [Referat returneres kryptert over TLS]
                                                          ↓
                                          [Lydfil slettes fra minnet]
```

**Teknisk:** GKE Standard cluster i `europe-north1` med dedikert GPU-nodepool
(NVIDIA L4, 24 GB VRAM). To Kubernetes-tjenester:
- `transkribering`: nb-whisper-large på L4, autoskalering 0→N pods
- `referat`: Ollama (qwen3:32b eller tilsvarende), GPU-delt eller egen nodepool

**Ytelse (estimert, NVIDIA L4):**

| Møtelengde | Transkriberingstid | Referatgenerering |
|-----------|-------------------|-------------------|
| 30 min | ~40–60 sek | ~30–60 sek |
| 60 min | ~80–120 sek | ~60–90 sek |

**Fordeler:**
- Sanntidskvalitet mulig med L4
- Skalerbar — håndterer toppbelastning (mange samtidige møter)
- Sentral oppdatering av modeller og sikkerhetspatcher
- Integrert med NAVs eksisterende sky-infrastruktur

**Ulemper:**
- GCP er ekstern databehandler — krever aktiv DPA og etterlevelsesdokumentasjon
- Mer kompleks infrastruktur enn alternativ 1/2
- GPU-nodepooler i europe-north1 støtter kun L4/T4 (ikke A100/H100)
- Cloud Run med GPU er **ikke** tilgjengelig i europe-north1 (kun europe-west4)
- Krever samarbeid med NAIS-teamet om GPU-nodepool

**Juridisk vurdering:**

*Databehandleravtale:*
Google Cloud (GCP) behandler personopplysninger på vegne av NAV → GDPR art. 28 DPA
kreves. NAV har allerede inngått DPA med Google via Digitaliseringsrundskrivet.
Verifiser at DPA dekker lydbehandling og AI-prosessering.

*Geografisk plassering:*
`europe-north1` (Hamina, Finland) er innenfor EU/EØS. Ingen overføring til tredjeland.
Schrems II-problematikk er ikke aktuell så lenge data ikke forlater regionen.

*Transient lydbehandling:*
Lydfilen overføres til GKE-pod, transkriberes i minnet, og slettes. **Lydfilen lagres
aldri i persistent storage (Cloud Storage, database).** Dette er avgjørende for å
begrense risikoprofielen. Bør dokumenteres i Behandlingskatalogen.

*§15-håndtering:*
Uavhengig av infrastrukturalternativ: referatgenereringen skjer i statlig kontekst og
er begrenset av §15. Løsningen implementerer §15-filter i LLM-prompten. Veileder
gjennomgår alltid referatet før lagring i Modia. Kommunale opplysninger fjernes manuelt
eller markeres med ⚠️-advarsel.

*Delt behandlingsansvar:*
Statlig NAV og kommunal del av NAV-kontoret er separate behandlingsansvarlige.
Verktøyet befinner seg i statlig NAV sin behandlingskjede. Den **kommunale
behandlingsansvarlige har ikke gitt samtykke** til at lydopptak av sine møter
prosesseres i statlig infrastruktur. Dette er et åpent juridisk spørsmål som bør
avklares med NAVs personvernombud (PVO) og eventuelt KS (kommunesektorens organisasjon)
før produksjonssetting av sky-alternativet.

---

### Alternativ 5 — Ekstern AI-tjeneste (Azure OpenAI / OpenAI Whisper API)

```
[Mikrofon] → [Nettleser] → [Azure OpenAI Whisper API] → [Transkripsjon]
                                      ↓
                         [Azure OpenAI GPT-4o referat]
```

**Fordeler:**
- Ingen infrastruktur å drifte
- Svært høy kvalitet

**Ulemper:**
- Lydopptak av sensitive §14a-møter sendes til ekstern tjeneste
- Azure er databehandler — DPA kreves; data kan inngå i treningsdata (må konfigureres bort)
- Selv med Azure OpenAI i norsk/europeisk region: etisk problematisk å sende lydopptak
  av sårbare borgere til kommersiell AI-leverandør
- Regulatorisk risiko (AI-forordningen art. 6, høyrisiko AI-system)

**Juridisk vurdering:**
**Ikke anbefalt** for §14a-møter med særkategori-opplysninger. Selv med gyldig DPA og
europeisk datalagring er det betydelig etisk og regulatorisk risiko knyttet til å
behandle lydopptak av sårbare borgere i kommersielle AI-tjenester.

---

## Beslutning

**Gjeldende: Alternativ 1** (lokal kjøring) for pilot/utprøving.

For produksjonssetting anbefales en trinnvis tilnærming:

1. **Fase 1 (nå):** Lokal kjøring — ingen DPA-krav, enkel juridisk profil, rask
   iterasjon. Egnet for pilotering på enkeltkontor med dedikert maskinvare.

2. **Fase 2:** Sentral on-premises server (alternativ 2) eller GKE med GPU
   (alternativ 4) — avhengig av skaleringsbehov og NAVs infrastrukturstrategi.
   **Forutsetter** avklaring av delt behandlingsansvar (§15) med PVO og KS.

3. **Alternativ 3** (NAIS CPU) kan være et steg mot alternativ 4 dersom GPU-støtte
   på NAIS avklares.

**Alternativ 5 frarådes** for §14a-møter.

---

## Åpne spørsmål

| Spørsmål | Ansvarlig | Frist |
|----------|-----------|-------|
| Kan statlig NAV behandle lydopptak fra §14a-møter i GCP uten eksplisitt avklaring med kommunal behandlingsansvarlig? | PVO / juridisk | — |
| Har NAVs DPA med Google Cloud dekning for lydbehandling og AI-inferens? | Seksjonsleder / PVO | — |
| Støtter NAIS-plattformen GPU-nodepooler i GKE? | NAIS-teamet | — |
| Krever lydopptak av bruker særskilt informasjon utover ordinær personvernerklæring? | PVO | — |
| Skal verktøyet klassifiseres som høyrisiko AI-system etter EU AI-forordningen art. 6? | Juridisk / PVO | — |

---

## Konsekvenser

- Behandlingskatalogen må oppdateres med nytt B-nummer for transkriberingsbehandlingen
  (se [ny-behandling-katalog](../adr/README.md))
- PVK/DPIA må gjennomføres før produksjonssetting, uavhengig av infrastrukturalternativ
- Løsningen bør inngå i NAVs oversikt over AI-systemer (AI-forordningen art. 49)
