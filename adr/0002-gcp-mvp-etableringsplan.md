# ADR-0002: GCP MVP-etableringsplan — Hybrid NAIS + GPU-infrastruktur

**Status:** Godkjent  
**Dato:** 2026-08-05  
**Oppdatert:** 2026-08-25  
**Forfattere:** ao-ki-taskforce  
**Forutsetning:** ADR-0001 besluttet egenstyrt Kubernetes-cluster med GPU som fase 2.

> **2026-08-25:** KNADA-alternativet forkastet — går med eget GCP-prosjekt (`ao-ki-taskforce-prod-2472`).
> Region endret til `europe-west4-b` (L4 ikke tilgjengelig i `europe-north1`).
> Modell endret til **Borealis-12b** (én L4, 24 GB VRAM) for piloten — kan oppgraderes til 27b.
> GKE-cluster og Terraform-infrastruktur er etablert. Fase 1 og 2 fullført.

---

## Kontekst og funn

### Funn fra `navikt/copilot-infra` (2026-08-18)

NAIS-teamet har et eksperimentelt oppsett for egenstyrt LLM-infrastruktur i GCP
([`navikt/copilot-infra`](https://github.com/navikt/copilot-infra)) for agentisk koding.
Gjennomgang av dette repoet gir viktige lærdommer for vår arkitektur:

- **Inference-motor:** De bruker **vLLM** (ikke Ollama). vLLM er designet for
  produksjons-inference med PagedAttention og langt bedre GPU-utnyttelse. Ollama er
  primært et utviklerverktøy for lokal bruk.
- **Gateway:** De bruker **LiteLLM på Cloud Run** (`INGRESS_TRAFFIC_INTERNAL_ONLY`)
  som stabil URL foran GPU-noder. For oss kjøres LiteLLM i stedet som en Deployment
  i GKE-clusteret — det aligner med NAIS-porteringen og unngår Cloud Runs begrensninger
  (WS-timeout ~60 min, 32 MB request-grense). Se eget avsnitt under.
- **Modellvekter:** Lastes fra **GCS ved oppstart** — ikke bakt inn i image. 675 MiB/s
  nedlastningshastighet gjør dette praktisk selv for store modeller.
- **Nettverksgap (G11):** Det finnes ingen ferdig nettverkssti mellom NAIS-clusterne
  og et eget team-GCP-prosjekt. De løste dette midlertidig med en offentlig auth-shim
  (Go-tjeneste). For oss er VPC-peering et krav siden vi prosesserer lydopptak av møter —
  data skal ikke gå via offentlig endepunkt.
- **Beregningsmodell:** De bruker GCE Managed Instance Groups (ikke GKE). For vår del
  er GKE Standard fortsatt riktig valg, siden vi har kortere modeller (< 20 GB),
  on-demand GPU-tilgjengelighet, sanntids-WebSocket-krav og ønsker migreringsvei til NAIS.

### Funn fra NAIS-infrastruktur

NAIS-teamet ønsker ikke GPU-støtte for denne POC-en. Gjennomgang av NAIS-infrastrukturen
(kildekode i `nais/nais-terraform-modules`) avdekker følgende:

- **Ingen GPU-nodepooler i NAIS-clustrene** (`dev-gcp`, `prod-gcp`). Node Auto Provisioning
  er satt opp kun for CPU og minne — ingen GPU resource limits.
- **Ingen API-overflate for GPU** i NAIS Application-spek. Feltene `tolerations`,
  `nodeSelector` og `affinity` finnes ikke i `nais.yaml`.
- **Intern-only ingress** via `intern.nav.no` / `intern.dev.nav.no` er fullt støttet —
  disse domenene peker på GCP Internal Load Balancers med kun private RFC-1918-adresser,
  kun tilgjengelig via naisdevice VPN eller internt NAV-nettverk.
- **KNADA** (`knada-gcp`-prosjekt) er en data science-plattform (Airflow/Flyte/JupyterHub)
  VPC-peeret med begge NAIS-clustrene. Prod-clusteret (`knada-gke`, `europe-north1`) har
  ingen GPU-nodepooler. Et GPU-cluster (`knada-gpu`, NVIDIA T4) finnes kun i dev-miljøet
  i `europe-west1`. **KNADA er forkastet som plattform** — ingen GPU i prod, og dev-GPU-ene
  (T4, `europe-west1`) er for små og i feil region for §14a-data.

---

## Anbefalt arkitektur: Hybrid NAIS + GPU

```
┌──────────────────────────────────────────────────────────────────┐
│  NAV-ansatt (naisdevice VPN / internt nett)                      │
└────────────────────┬─────────────────────────────────────────────┘
                     │ HTTPS  intern.nav.no (privat LB 10.7.8.200)
                     ▼
┌──────────────────────────────────────────────────────────────────┐
│  NAIS prod-gcp cluster  (GCP-prosjekt: nais-prod-020f)           │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  ao-ki-transkribering  (nais.yaml)                         │  │
│  │  • Frontend + FastAPI                                      │  │
│  │  • Azure AD sidecar (autoLogin: true)                      │  │
│  │  • Prometheus/Loki/Tempo observability                     │  │
│  │  • Zero-trust nettverkspolicy                              │  │
│  └──────────────────────┬─────────────────────────────────────┘  │
│                         │ intern K8s DNS / HTTP                   │
│                  VPC peering (MÅ ETABLERES — se G11)              │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  GPU-plattform  (eget GKE cluster i team GCP-prosjekt)           │
│                                                                  │
│  LiteLLM gateway (GKE Deployment, ClusterIP/Internal LB)         │
│    ├── vLLM — nb-whisper (GKE GPU-pod)                           │
│    └── vLLM — Borealis   (GKE GPU-pod)                           │
│                                                                  │
│  Krever VPC peering fra NAIS-teamet                              │
└──────────────────────────────────────────────────────────────────┘
```

**Interntrafikk:** NAIS-appen kaller GPU-tjenestene via intern GCP-nettverkstrafikk
(private IP over VPC peering) — ingen data forlater NAVs GCP-infrastruktur.

---

## Inference-stack: valg av motor

**Alle modeller kjøres i vLLM** — én motor for både transkripsjon og LLM.

| Oppgave | Motor | API |
|---------|-------|-----|
| **Transkripsjon** (nb-whisper) | **vLLM** | `POST /v1/audio/transcriptions` (OpenAI-kompatibel) |
| **Sanntid-transkripsjon** | **vLLM** | `WS /v1/realtime` — streaming WebSocket med `transcription.delta`-events |
| **Referatgenerering** (Borealis-12b) | **vLLM** | `POST /v1/chat/completions` |

### Hvorfor vLLM for Whisper — og ikke faster-whisper?

Det opprinnelige valget var **faster-whisper** (CTranslate2) for transkripsjon og
**Ollama** for LLM. Etter gjennomgang av `navikt/copilot-infra` og vLLMs kildekode
er begge byttet ut med vLLM:

**faster-whisper → vLLM:**
- vLLM har hatt encoder-decoder-støtte siden v0.5+ og leverer egne Whisper-modulfiler
  (`whisper.py`, `whisper_causal.py`) i v0.27.1
- vLLM har innebygd `SpeechToTextConfig` med automatisk chunk-splitting for lang lyd
  — ingen egenutviklet chunking-logikk nødvendig
- vLLM eksponerer `WS /v1/realtime` med `transcription.delta`-events, nøyaktig det
  sanntids-WebSocket-funksjonaliteten vår trenger
- Én motor for alle modeller forenkler infrastrukturen: ett image-format, én
  deployment-mal, én gateway (LiteLLM) foran alt
- *Eneste kjente svakhet:* beam search er ineffektivt i vLLM for Whisper (under
  aktiv optimalisering). Greedy decoding er standard og tilstrekkelig for transkripsjon.

**Ollama → vLLM:**
- Ollama er primært et utviklerverktøy for lokal bruk — ikke designet for delt
  GPU-infrastruktur med PagedAttention og GPU-utnyttelse på produksjonsnivå
- vLLM brukes av `navikt/copilot-infra` og er verifisert på NAV-infrastruktur

### Valg av LLM-modell for referatgenerering: Borealis-12b (pilot)

[Borealis](https://ai.nb.no/borealis/) er Nasjonalbibliotekets åpne norske modellserie,
basert på Gemma 3. Den er fintunet på norsk (bokmål og nynorsk) og distribuert med
åpen lisens fra en norsk offentlig institusjon — godt egnet for norsk forvaltning.

**Tilgjengelige størrelser:** 270m, 1b, 4b, 12b, 27b (full-release).

**Kan vi kjøre Borealis-27b på én L4 (24 GB VRAM)?**

| Format | Størrelse | Én L4 (24 GB) |
|--------|-----------|---------------|
| BF16 (full presisjon) | ~54 GB | ❌ |
| Q8_0 | ~27 GB | ❌ (marginalt for lite) |
| Q4_K_M (GGUF) | ~14 GB | ✅ — men vLLM laster ikke GGUF nativt |
| BF16 + tensor parallelism | ~27 GB | ✅ **to L4-er** |

**Beslutning (2026-08-25): Borealis-12b for piloten** — kjører på én L4 i BF16
(~24 GB). vLLM støtter Gemma 3 nativt (`gemma3.py`), og Borealis er en direkte
finetune av `google/gemma-3-12b-it` — ingen tilpasning nødvendig.

*Dette er en hypotese som skal falsifiseres i piloten:* at 12b produserer
møtereferater av tilstrekkelig kvalitet. Piloten må derfor inneholde en
kvalitetsvurdering (f.eks. side-ved-side mot 27b eller menneskelig vurdering
av et utvalg referater) før beslutningen begrunnes som permanent.

**Oppgraderingsvei:** Borealis-27b kjører i BF16 på **to L4-er** med tensor
parallelism (`--tensor-parallel-size 2`) dersom kvalitetsvurderingen viser at
12b ikke er god nok. 27b er finetune av `google/gemma-3-27b-it`.

**Modellvekter** lastes fra **GCS ved pod-oppstart** — ikke bakt inn i image.
nb-whisper-large er ~3 GB; Borealis-12b i BF16 er ~24 GB.

**LiteLLM** kjøres som en **Deployment i GKE-clusteret** (ClusterIP eller Internal
LoadBalancer) — ikke på Cloud Run slik `navikt/copilot-infra` gjør. Begrunnelse:

- **NAIS-portering:** Hele GPU-plattformen blir rene K8s-manifester. Ved migrering
  til NAIS flyttes deploymenten 1:1 — Cloud Run-tjenester kan ikke flyttes som K8s-workloads.
- **WebSocket:** Sanntidsflyten vår er WS-basert. Cloud Run terminerer
  WS-tilkoblinger etter ~60 min (akkurat ved normal møtelengde) og har en 32 MB
  request-grense. I GKE går LiteLLM mot vLLM via ClusterIP uten slike begrensninger.
- **Kostnad:** Mister scale-to-zero for gatewayen, men den er en liten CPU-tjeneste —
  én `min 1` replica er ubetydelig sammenlignet med GPU-nodene.

Gatewayen gir fortsatt: stabil URL som overlever node-churn (GKE scale-to-zero),
retry-logikk, model aliasing, og `turn_off_message_logging: true` — lyddata og
transkripsjon logges ikke. Master key mountes som K8s Secret.
LiteLLM-konfigurasjonen i `navikt/copilot-infra`s `model-gateway`-modul
(generert fra deployments-map) brukes som mal.

---

## Frontend på NAIS — detaljer

Å kjøre frontend og API på NAIS er sterkt anbefalt. Man får gratis:

| Tjeneste | Konfigurasjon | Verdi |
|----------|--------------|-------|
| **Azure AD-autentisering** | `spec.azure.sidecar.enabled: true` + `autoLogin: true` | Slipper å implementere OIDC selv |
| **Intern-only tilgang** | `spec.ingresses: [https://ao-ki-transkribering.intern.nav.no]` | Kun tilgjengelig via naisdevice/intranet |
| **Metrics + Grafana** | Automatisk fra `/metrics` | Driftsovervåking uten oppsett |
| **Logging (Loki)** | Automatisk fra stdout | Alle applikasjonslogger samlet |
| **Zero-trust nett** | `spec.accessPolicy.outbound` for GPU-endepunkt | Eksplisitt hvitelisting av utgående trafikk |
| **Ingen secrets i kode** | `spec.envFrom.secret` | Hemmeligheter injiseres som env-vars |

Minimalt `nais.yaml`-eksempel:

```yaml
apiVersion: nais.io/v1alpha1
kind: Application
metadata:
  name: ao-ki-transkribering
  namespace: ao-ki-taskforce
spec:
  image: europe-north1-docker.pkg.dev/{{teamprosjekt}}/ao-ki-transkribering:{{tag}}
  port: 8765
  replicas:
    min: 1
    max: 2
  ingresses:
    - https://ao-ki-transkribering.intern.dev.nav.no   # dev
    # - https://ao-ki-transkribering.intern.nav.no     # prod
  azure:
    application:
      enabled: true
      sidecar:
        enabled: true
        autoLogin: true
  accessPolicy:
    outbound:
      external:
        - host: <gpu-tjeneste-intern-ip-eller-hostname>
  resources:
    requests:
      cpu: 200m
      memory: 512Mi
    limits:
      memory: 1Gi
  liveness:
    path: /isAlive
  readiness:
    path: /isReady
```

---

## GPU-plattform: Egenstyrt GKE-cluster i team GCP-prosjekt

KNADA er vurdert og forkastet:
- Ingen GPU-nodepooler i prod (`knada-gke`, `europe-north1`)
- Dev-GPU-ene (`knada-gpu`, NVIDIA T4, `europe-west1`) er for små og i feil region for §14a-data
- Uklar adgang til å deploye egne Kubernetes-workloads utenfor Flyte/Airflow

**Beslutning:** Eget GKE Standard-cluster i teamets GCP-prosjekt (som NAIS automatisk
oppretter for `ao-ki-taskforce`).

**Utfordring med region og GPU-tilgjengelighet (L4 vs RTX/B200/H100):**
NAIS-infrastrukturen kjører i `europe-north1` (Hamina, Finland). Sjekk av GPU-kvoter og
tilgjengelighet i denne regionen avdekker at **L4 (G2-instanser) ikke tilbys i `europe-north1`**.
De eneste tilgjengelige alternativene i Finland er massive datasenter-GPUer (B200, H100) eller RTX Pro 6000.

Dette gir oss to løyper (uavklart):
1. **Løype A (Kryss-region):** Beholde L4 (svært kostnadseffektivt for våre 3-20 GB modeller),
   men plassere GPU-clusteret i `europe-west4` (Nederland) eller `europe-west1` (Belgia).
   Trafikken rutes transparent via VPC-peering mellom NAIS i `north1` og GPU i `west4`.
2. **Løype B (Samme region):** Bytte til RTX Pro 6000 (G4-instanser, 24GB VRAM) for å beholde
   clusteret i `europe-north1`. Dette er dyrere per time og kan ha dårligere on-demand tilgjengelighet enn L4.

**Hvorfor GKE og ikke GCE MIG (som `navikt/copilot-infra`):**
- `navikt/copilot-infra` kjører 756 GB-modeller på B200 kun tilgjengelig som Spot —
  MIG-resiliens er kritisk der. Våre modeller (3–20 GB) er tilgjengelig on-demand.
- GKE gjenbruker Kubernetes-kompetansen teamet allerede har fra NAIS.
- Sanntids-WebSocket krever persistent tilkobling — Spot-preemption er uønsket.
- GKE gir ren migreringsvei til NAIS når GPU-støtte eventuelt kommer.

**Forutsetning:** NAIS-teamet må legge til VPC peering mellom teamets GCP-prosjekt
og NAIS-clustrenes VPC-er. Dette er en enkel konfigurasjon i
`nais/nais-terraform-modules/tenants/nav/peerings_prod.tf`:

```hcl
# Eksempel på ny peering som NAIS-teamet legger til
module "peer_ao_ki_transkribering" {
  source        = "../../modules/peering"
  local_network = data.google_compute_network.nais_vpc.self_link
  peer_network  = "projects/ao-ki-transkribering/global/networks/default"
  export_routes = false
  import_routes = false
}
```

Dette er en *langt* lavere terskel å be om enn GPU-nodepool-støtte.

#### Terraform: GKE cluster (alternativ B)

```hcl
# GPU-cluster i teamets GCP-prosjekt
resource "google_container_cluster" "gpu" {
  name     = "ao-ki-transkribering-gpu"
  location = "europe-north1"
  project  = var.team_project_id

  remove_default_node_pool = true
  initial_node_count       = 1

  network    = "default"
  subnetwork = "default"

  # Ingen offentlig endepunkt — kun privat API-server
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "10.7.8.0/23"   # NAIS prod-gcp node CIDR
      display_name = "nais-prod-nodes"
    }
    cidr_blocks {
      cidr_block   = "10.6.8.0/23"   # NAIS dev-gcp node CIDR
      display_name = "nais-dev-nodes"
    }
  }
}

# GPU-nodepool: NVIDIA L4, scale-to-zero
resource "google_container_node_pool" "gpu" {
  name     = "gpu-pool"
  cluster  = google_container_cluster.gpu.id
  location = "europe-north1"

  autoscaling {
    min_node_count = 0
    max_node_count = 2
  }

  node_config {
    machine_type = "g2-standard-8"   # 8 vCPU, 32 GB RAM, 1× NVIDIA L4
    disk_size_gb = 100

    guest_accelerator {
      type  = "nvidia-l4"
      count = 1
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    workload_metadata_config { mode = "GKE_METADATA" }
  }
}
```

#### Kapasitet og skalering av L4 GPUer

Å velge `europe-west4`/`west1` gir oss tilgang til kvoter på 32 L4 GPUer.
Selv for en fremtidig oppskalering til hele NAV, er dette tilstrekkelig med god margin:

- **1 times møte** tar ca. 3 minutter å transkribere (nb-whisper på vLLM, konservativ RTF 20x).
- Referatgenerering tar ca. 30 sekunder (Borealis-12b, context loading + generering).
- **Skaleringsscenario:** 3 000 en-times møter per dag krever 150 maskintimer til
  transkripsjon og 25 timer til LLM. Selv i en massiv "toppbelastning" hvor 40% av 
  møtene skjer i løpet av to timer, vil GKE-clusteret måtte autoskalere opp til
  omlag ~35 L4-instanser parallelt, noe som er tett på dagens standardkvote.
  For MVP og bred pilotering er kvoten lang over behovet.

---

## Nettverkssikkerhet

All trafikk holdes innenfor NAVs GCP-infrastruktur:

```
Bruker (naisdevice VPN)
    → intern.nav.no (privat LB, 10.7.8.200)
        → NAIS-pod (FastAPI)
            → LiteLLM gateway (GKE Deployment, intern VPC)
                → vLLM pod — nb-whisper  [POST /v1/audio/transcriptions — kun POC-test]
                → vLLM pod — nb-whisper  [WS   /v1/realtime  (sanntid)]
                → vLLM pod — Borealis    [POST /v1/chat/completions]
                ← Transkripsjon/referat returneres
            ← Returneres til NAIS-pod
        ← Returneres til nettleser
Ingen av stegene over passerer internett.
```

**GPU-tjenestene eksponeres kun som ClusterIP eller Internal LoadBalancer** (ikke ekstern).
NAIS-appen hvitelister GPU-endepunktets IP i `accessPolicy.outbound`.

---

## Datalivssyklus og personvern

**Beslutning (2026-08-21):** Løsningen lagrer ingen lyd og ingen transkripsjoner.

- **Lyd:** Streames som korte chunks over WebSocket og kastes fortløpende etter
  prosessering. Ingen lyddata skrives til disk noe sted i kjeden.
- **Batch-transkripsjon støttes ikke i produksjon** — kun som testverktøy i POC-fasen.
- **Verbatim-transkripsjon:** Holdes kun i minne i NAIS-poden og slettes når
  referatet er generert og godkjent av bruker. Se åpent spørsmål om robusthet.
- **Referat:** Forlater systemet manuelt — i POC-fasen kopierer/laster brukeren
  selv referatet inn i dertil egnet arkiveringssystem. Ingen persistens hos oss.

**Konsekvenser:**

| Konsekvens | Vurdering |
|------------|-----------|
| DPIA/ROS | Betydelig enklere — ingen lagring av personopplysninger i løsningen |
| Database | Ikke behov for Postgres i MVP |
| Cloud Run 32 MB-grense | Ikke relevant — LiteLLM kjøres i GKE, ikke Cloud Run |
| Rettskraftig sletting | Trivielt — ingenting å slette etter sesjonsslutt |

---

## Observability og kostnadskontroll på GPU-siden

NAIS gir Grafana/Loki gratis på app-siden, men GPU-plattformen (GKE)
ligger i teamets GCP-prosjekt og må utstyres bevisst. `navikt/copilot-infra` har
verifiserte mønstre som bør kopieres:

- **Kostnadsvakt:** Budsjettvarsler (GCP Budget Alerts) med varslingsadresser.
  Copilot-infra varsler når en node har stått over 12 t — tilsvarende terskel
  bør settes for vår GPU-nodepool.
- **Automatisk nedskalering:** Cloud Scheduler som setter GPU-nodepool til 0
  kveld/helg. Selv med `min_node_count = 0` i autoskaleringen er en aktiv
  scheduler en dobbeltsikring mot at noder glemtes stående.
- **Logging/metrikker:** Cloud Logging/Monitoring på GKE-siden. LiteLLM kjører
  med `turn_off_message_logging: true` — kun metadata logges, aldri innhold
  (lyd, transkripsjon, referat).

---

## Faseplan

### Fase 1 — Avklar GPU-plattform ✅ Fullført (2026-08-24)
- [x] ~~Ta kontakt med KNADA-teamet~~ — KNADA forkastet, går med eget GCP-prosjekt
- [x] GCP-prosjekt bekreftet: `ao-ki-taskforce-prod-2472` (NAIS-provisjonert)
- [x] Billing-konto bekreftet (koblet av NAIS)

### Fase 2 — Frontend på NAIS ✅ Fullført (2026-08-20)
- [x] `nais.yaml` opprettet og deployet til `dev-gcp`
- [x] Docker-image uten GPU-avhengigheter (`requirements/api.txt`)
- [x] Tilgjengelig på `https://ao-ki-transkribering.intern.dev.nav.no`
- [x] Azure AD-autentisering verifisert (Wonderwall + Texas)

### Fase 3 — GPU-infrastruktur (pågår)
- [x] GKE Standard-cluster opprettet med Terraform (`europe-west4-b`, `g2-standard-12`, 1× L4)
- [x] GCS-bucket for modellvekter opprettet (`ao-ki-taskforce-prod-2472-modeller`)
- [x] Workload Identity konfigurert
- [ ] Be NAIS-teamet om VPC peering (`#nais` Slack)
- [ ] Verifiser GPU-nodepool med `nvidia-smi`
- [ ] **Spike: verifiser sanntidskjeden ende-til-ende** — vLLM `WS /v1/realtime` med
  nb-whisper, proxied gjennom LiteLLM i GKE. Uverifisert territorium:
  copilot-infra bruker kun chat completions, og vi er avhengige av at LiteLLMs
  WS-passthrough tåler møter av normal lengde uten timeout. Fallback: NAIS-pod
  kobler direkte mot vLLM-tjenesten (ClusterIP) og dropper LiteLLM for
  sanntidsflyten.
- [ ] Mål round-trip-latenstid NAIS (`north1`) → GPU-cluster dersom kryss-region
  velges (`west4`/`west1`) — påvirker sanntidsopplevelsen

### Fase 4 — Container-images og deploy (uke 3–4)
- [ ] Last opp modellvekter til GCS (nb-whisper-large ~3 GB, Borealis-12b ~24 GB) —
  vekter lastes inn i podene fra GCS ved oppstart
- [ ] Bygg vLLM-image med `vllm[audio]` for nb-whisper
- [ ] Bygg vLLM-image for Borealis-12b
- [ ] Push images til Artifact Registry (`europe-west4-docker.pkg.dev/ao-ki-taskforce-prod-2472/`)
- [ ] Sett opp LiteLLM som Deployment i GKE (ClusterIP/Internal LB, master key
  som K8s Secret — bruk `navikt/copilot-infra`s `model-gateway`-modul som mal)
- [ ] Deploy K8s-manifester til GPU-cluster (`k8s/`)
- [ ] Koble NAIS-app mot LiteLLM-gateway (oppdater `TRANSKRIPSJON_SERVICE_URL` og `OLLAMA_URL` i secrets)
- [ ] Fjern faster-whisper fra `worker/` og nedjuster ressursprofil i `nais.yaml`
  (dagens 2–4 Gi memory-limit er dimensjonert for in-pod-modell) — avhengig av
  at sanntidsspiaken i Fase 3 er grønn
- [ ] Sett opp budsjettvarsler og Cloud Scheduler for nedskalering av GPU-nodepool

### Fase 5 — Testing og akseptansekriterier (uke 4)

| Test | Akseptabel grense |
|------|-------------------|
| Batch-transkripsjon, 45 min møte (kun POC-testverktøy) | < 3 min |
| Sanntid-segment (10 sek lyd) | < 3 sek |
| Referatgenerering, 1 000 ord | < 60 sek |
| Cold start GPU-pod (0 → klar) | < 5 min ⚠️ |
| Ingen lyddata på disk etter sesjon | ✅ verifisert |
| Kun tilgjengelig via naisdevice | ✅ verifisert |

⚠️ **Kaldstart:** Borealis-12b (~24 GB) tar ~36 sek å laste fra GCS. Med node-provisioning
kan total cold start overskride 5 min. Tiltak: `min 1` GPU-node i arbeidstiden via
Cloud Scheduler, eller pre-staged vekter på disk-snapshot.

---

## Kostnadsestimat

*Merk: Estimatet baserer seg på L4-GPUer (G2-instanser), som forutsetter at GPU-clusteret legges til `europe-west4` eller `europe-west1`, da L4 ikke finnes i `europe-north1` (se avsnitt om Region og GPU-tilgjengelighet).*

| Ressurs | Type | Pris/time | Estimert bruk/mnd | Kostnad/mnd |
|---------|------|-----------|-------------------|-------------|
| GKE Standard-kontrollplan | fast | — | — | ~$73 |
| GKE system-noder (CPU, alltid på) | e2-standard-2 × 1 | ~$0.07 | 730 t | ~$50 |
| GPU-node (nb-whisper) | g2-standard-8 + L4 | ~$1.20 | 40 t (pilot) | ~$48 |
| GPU-node (Borealis-12b) | g2-standard-12 + 1× L4 | ~$1.40 | 40 t (pilot) | ~$56 |
| LiteLLM gateway | CPU-pod i GKE (min 1 replica) | — | 730 t | ~$10 |
| Persistent disk | 50 GB SSD | — | — | ~$8 |
| **Totalt pilot** | | | | **~$245/mnd (~2 600 kr)** |

Oppgradering til Borealis-27b (2× L4, `g2-standard-24`) øker LLM-kostnaden til
~$96/mnd — totalt ~$285/mnd.

GPU-noder autoskalerer til 0 ved inaktivitet → faktisk pilot-kostnad trolig langt lavere.
API/frontend kjører på NAIS og er ikke medregnet her (intern NAIS-kostnad).

---

## Modelldrift (vekter, versjoner, rollback)

Modelvekter lastes fra GCS ved pod-oppstart. For å ikke drifte «latest»:

- **Staging-skript:** Eget skript (jf. `stage-weights.py` i `navikt/copilot-infra`)
  som laster vekter fra HuggingFace til teamets GCS-bucket, med størrelses-/
  hash-verifisering. Borealis distribueres signert (`SHA256SUMS.sig`) — verifiser.
- **Versjonering:** GCS-path inkluderer modellversjon (f.eks.
  `gs://<bucket>/models/nb-whisper-large/v1/`). Pod-manifest pinner stien.
- **vLLM-image pinnes på digest** (jf. copilot-infra-praksis). Whisper-støtten i
  vLLM er ung — nye versjoner testes i dev før prod.
- **Rollback:** Ny modellversjon rulles ut som ny Deployment ved siden av gammel;
  LiteLLM model-alias flippes etter verifisering. Gammel versjon beholdes på GCS
  til ny er akseptert.

## GPU-driver og node-image

`gpu_driver_installation_config` i Terraform-snutten forutsetter at GKE støtter
automatisk driver-installasjon for valgt GPU/region-kombinasjon. Copilot-infra
brukte mye av sin fase 1 på nøyaktig driver-problematikk (deres G19-gap:
fraksjonelle G4-instanser krevde egen GRID-driver, og standard DLVM-image manglet
container-runtime). For G2/L4 er GKE-støtten moden, men dette verifiseres
eksplisitt som første steg i Fase 3 (podium-kommando `nvidia-smi`).

---

## Åpne spørsmål

| Spørsmål | Ansvarlig | Status |
|----------|-----------|--------|
| Be NAIS-teamet om VPC peering fra `ao-ki-taskforce-prod-2472` | ao-ki-taskforce → NAIS | ⬜ |
| Robusthet: hva skjer om nettverket ryker midt i et møte? Klient-side bufring? | ao-ki-taskforce | ⬜ |
| Hva møter brukeren når GPU-clusteret er nede/kaldt (scale-to-zero, 5+ min kaldstart)? Er "møtet kan ikke startes før GPU er varm" akseptabelt i pilot, eller trengs graceful degradation? | Produkteier + ao-ki-taskforce | ⬜ |
| Borealis-12b kvalitet — er den god nok, eller trenger vi 27b (2× L4)? | ao-ki-taskforce | ⬜ |
| Finnes AWQ/GPTQ-kvantisering av Borealis-27b for å unngå 2× L4 (om 27b viser seg nødvendig)? | NbAiLab / ao-ki-taskforce | ⬜ |
| Kan vi dele LiteLLM-gatewayen med `navikt/copilot-infra`-teamet? | **Nei** — ulike datakrav, tilgangskontroll og modeller. Gjenbruk Terraform-modulen som mal. | ✅ Avklart |
| Region `europe-west4` vs `europe-north1` | **`europe-west4-b`** valgt — L4 ikke tilgjengelig i north1 | ✅ Avklart |
| GCP-prosjekt for GPU-infrastruktur | **`ao-ki-taskforce-prod-2472`** | ✅ Avklart |
| Modell: Borealis-27b (2× L4) eller Borealis-12b (1× L4)? | **Borealis-12b** for pilot | ✅ Avklart |
| KNADA som alternativ plattform | **Forkastet** — går med eget GCP-prosjekt | ✅ Avklart |
| Langsiktig: kan GPU-infrastrukturen bli en felles NAV-plattform for åpne modeller? | Teknologiavdelingen | ⬜ |

### Utsettes til pilot-fasen (ikke MVP-kritiske)

| Tema | Beskrivelse |
|------|-------------|
| **NetworkPolicy i GKE** | I dag sikres GPU-plattformen med privat cluster + Internal LB + master key. I pilot bør K8s NetworkPolicy eksplisitt begrense trafikk til LiteLLM fra NAIS-CIDR-ene, og vLLM-poder fra kun gatewayen. |
| **Master key-rotasjon** | LiteLLM master key i K8s Secret: hvem eier rotasjon, hvor ofte, og hvordan ruller vi uten nedetid? Enkelt å styre manuelt i pilot. |
| **Modelloppdateringsrutine** | Modelldrift-avsnittet definerer mønsteret, men rutine for å følge med på nye Borealis-/nb-whisper-releases (og vurdere oppgradering) etableres i pilot. |

---

## Migreringsvei til NAIS

Hybridarkitekturen er designet for enkel migrering:

```
I dag (hybrid):          NAIS-app → VPC peering → GPU-cluster (eget/KNADA)
Når NAIS støtter GPU:    NAIS-app → NAIS GPU-nodepool
```

Kun GPU-tjenestene flyttes. Frontend og API på NAIS rører man ikke.
LiteLLM-gatewayens URL i `nais.yaml` forblir den samme — kun backendet bak
den endres til en NAIS-intern service.
