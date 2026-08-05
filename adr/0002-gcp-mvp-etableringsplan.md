# ADR-0002: GCP MVP-etableringsplan — Hybrid NAIS + GPU-infrastruktur

**Status:** Forslag  
**Dato:** 2026-08-05  
**Forfattere:** ao-ki-taskforce  
**Forutsetning:** ADR-0001 besluttet egenstyrt Kubernetes-cluster med GPU som fase 2.

---

## Kontekst og funn

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
  i `europe-west1`. Egnet for produksjon med GPU er uavklart — krever dialog med KNADA-teamet.

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
│                  VPC peering (allerede etablert)                  │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  GPU-plattform  (se Alternativ A og B under)                     │
│                                                                  │
│  Alternativ A: KNADA  (knada-gcp, allerede VPC-peeret)           │
│    nb-whisper + Ollama som Kubernetes-tjenester                  │
│                                                                  │
│  Alternativ B: Eget GKE cluster i team GCP-prosjekt              │
│    Krever ny VPC peering via NAIS-teamet                         │
└──────────────────────────────────────────────────────────────────┘
```

**Interntrafikk:** NAIS-appen kaller GPU-tjenestene via intern GCP-nettverkstrafikk
(private IP over VPC peering) — ingen data forlater NAVs GCP-infrastruktur.

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

## GPU-plattform: To alternativer

### Alternativ A — KNADA (verdt å undersøke)

KNADA (`knada-gcp`) er en data science-plattform (Airflow, Flyte, JupyterHub) med
eget GKE-cluster. Bekreftet fra kildekoden i `nais/knada-gcp`:

- **`knada-gke`** (prod, `europe-north1`): GKE-cluster med CPU-nodepooler
  (`resource_intensive_pool`). **Ingen GPU-nodepooler i prod.** ⚠️
- **`knada-gpu`** (dev-miljø, `europe-west1`): Separat GPU-cluster med
  **NVIDIA Tesla T4** — kun i dev, ikke prod.
- Begge er i GCP-prosjekter VPC-peeret med NAIS-clustrene ✅
- Plattformen er bygget rundt **Flyte** og **Airflow** (batch-workflows/notebooks)

**Fordeler:**
- VPC-peering med NAIS allerede på plass — ingen ny infrastruktur mellom lagene

**Usikkerheter (må avklares med KNADA-teamet):**
- Kan ao-ki-taskforce deploye egne Kubernetes-workloads, eller styres alt via Flyte/Airflow?
- Er det planer om GPU i prod-clusteret (`europe-north1`)?
- `europe-west1` (dev GPU) er akseptabelt for testing, men ikke for produksjon med §14a-data

**Konklusjon:** KNADA har ingen GPU i prod i dag. Alternativ B (eget cluster) er
trolig nødvendig med mindre KNADA-teamet har planer om GPU i prod.

---

### Alternativ B — Egenstyrt GKE-cluster i team GCP-prosjekt

Dersom KNADA ikke egner seg, opprettes et eget GKE Standard-cluster i teamets
GCP-prosjekt (som NAIS automatisk oppretter for `ao-ki-taskforce`).

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

---

## Nettverkssikkerhet

All trafikk holdes innenfor NAVs GCP-infrastruktur:

```
Bruker (naisdevice VPN)
    → intern.nav.no (privat LB, 10.7.8.200)
        → NAIS-pod (FastAPI)
            → GPU-pod (nb-whisper / Ollama)  [intern VPC, RFC-1918]
                ← Transkripsjon/referat returneres
            ← Returneres til NAIS-pod
        ← Returneres til nettleser
Ingen av stegene over passerer internett.
```

**GPU-tjenestene eksponeres kun som ClusterIP eller Internal LoadBalancer** (ikke ekstern).
NAIS-appen hvitelister GPU-endepunktets IP i `accessPolicy.outbound`.

---

## Faseplan

### Fase 1 — Avklar GPU-plattform (uke 1)
- [ ] Ta kontakt med KNADA-teamet (`#knada` Slack): støttes persistent GPU-tjenester?
- [ ] Hvis nei: klargjør team GCP-prosjekt (NAIS oppretter automatisk for `ao-ki-taskforce`)
- [ ] Hent GCP-prosjekt-ID og bekreft billing-konto

### Fase 2 — Frontend på NAIS (uke 1–2)
- [ ] Opprett `nais.yaml` (basert på eksempel over) i repoet
- [ ] Bygg Docker-image uten GPU-avhengigheter (kun frontend + FastAPI uten whisper-last)
- [ ] Deploy til `dev-gcp` med `intern.dev.nav.no`
- [ ] Verifiser Azure AD-autentisering og intern-only tilgang

### Fase 3 — GPU-infrastruktur (uke 2–3)
**KNADA-løype:**
- [ ] Sett opp nb-whisper + Ollama som K8s Deployments i KNADA
- [ ] Verifiser at NAIS-pod kan nå GPU-tjenestene via intern IP

**Eget cluster-løype (alternativ B):**
- [ ] Opprett GKE Standard-cluster med Terraform (se over)
- [ ] Be NAIS-teamet om VPC peering (enkelt PR til `nais-terraform-modules`)
- [ ] Verifiser GPU-nodepool med `nvidia-smi`

### Fase 4 — Container-images og deploy (uke 3–4)
- [ ] Bygg `transkribering`-image med CUDA/faster-whisper + nb-whisper-large
- [ ] Bygg `referat`-image med Ollama + qwen3:32b (bakt inn eller fra PVC)
- [ ] Push til Artifact Registry
- [ ] Deploy K8s-manifester til GPU-cluster
- [ ] Koble NAIS-app mot GPU-tjenester (OLLAMA_URL / intern host)

### Fase 5 — Testing og akseptansekriterier (uke 4)

| Test | Akseptabel grense |
|------|-------------------|
| Batch-transkripsjon, 45 min møte | < 3 min |
| Sanntid-segment (10 sek lyd) | < 3 sek |
| Referatgenerering, 1 000 ord | < 60 sek |
| Cold start GPU-pod (0 → klar) | < 5 min |
| Ingen lyddata på disk etter sesjon | ✅ verifisert |
| Kun tilgjengelig via naisdevice | ✅ verifisert |

---

## Kostnadsestimat

### Alternativ A (KNADA): Avklares med KNADA-teamet — trolig intern fordeling.

### Alternativ B (eget cluster):

| Ressurs | Type | Pris/time | Estimert bruk/mnd | Kostnad/mnd |
|---------|------|-----------|-------------------|-------------|
| GPU-node (whisper) | g2-standard-8 + L4 | ~$1.20 | 40 t (pilot) | ~$48 |
| GPU-node (Ollama) | g2-standard-8 + L4 | ~$1.20 | 40 t (pilot) | ~$48 |
| CPU-node (API) | n2-standard-4 | ~$0.19 | 730 t | ~$140 |
| Persistent disk | 50 GB SSD | — | — | ~$8 |
| **Totalt pilot** | | | | **~$245/mnd (~2 600 kr)** |

GPU-noder autoskalerer til 0 ved inaktivitet → faktisk pilot-kostnad trolig langt lavere.

---

## Åpne spørsmål

| Spørsmål | Ansvarlig | Status |
|----------|-----------|--------|
| Støtter KNADA persistent GPU-tjenester (ikke bare notebooks)? | ao-ki-taskforce → `#knada` | ⬜ |
| Har NAIS-teamet kapasitet til å legge til VPC peering for teamprosjektet? | ao-ki-taskforce → NAIS | ⬜ |
| Er `ao-ki-taskforce` et NAIS-team med eget GCP-prosjekt? | ao-ki-taskforce | ⬜ |
| Hvilken billing account brukes for GPU-infrastrukturen? | PO / økonomi | ⬜ |
| Skal Ollama-modellen bakes inn i Docker-image (~22 GB) eller lastes fra GCS PVC? | ao-ki-taskforce | ⬜ |
| Langsiktig: kan GPU-infrastrukturen bli en felles NAV-plattform for åpne modeller? | Teknologiavdelingen | ⬜ |

---

## Migreringsvei til NAIS

Hybridarkitekturen er designet for enkel migrering:

```
I dag (hybrid):          NAIS-app → VPC peering → GPU-cluster (eget/KNADA)
Når NAIS støtter GPU:    NAIS-app → NAIS GPU-nodepool
```

Kun GPU-tjenestene flyttes. Frontend og API på NAIS rører man ikke.
`OLLAMA_URL`-env-variabelen i `nais.yaml` peker da på NAIS-intern service i stedet.
