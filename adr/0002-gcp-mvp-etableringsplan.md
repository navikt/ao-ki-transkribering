# ADR-0002: GCP MVP-etableringsplan — Kubernetes med GPU

**Status:** Forslag  
**Dato:** 2026-08-05  
**Forfattere:** ao-ki-taskforce  
**Forutsetning:** ADR-0001 besluttet egenstyrt GKE-cluster utenfor NAIS som fase 2.

---

## Kontekst

NAIS-teamet ønsker ikke å tilby GPU-støtte for denne POC-en. Vi etablerer
et egenstyrt GKE Standard-cluster i `europe-north1` (Finland) med NVIDIA L4
GPU-nodepool. Målet er en fungerende MVP som kan kjøre nb-whisper-large og
Ollama (qwen3:32b eller tilsvarende) i NAVs GCP-organisasjon.

Planen dekker både teknisk oppsett og utprøvingsfasene frem mot en MVP.

---

## Forutsetninger

| Forutsetning | Status | Kommentar |
|---|---|---|
| GCP-prosjekt under navikt-org | ⬜ Må opprettes | Navn: `ao-ki-transkribering` |
| GCP-fakturering og budsjett | ⬜ Avklares | Estimat: ~5 000–15 000 kr/mnd for POC |
| IAM-tilgang til GCP for teamet | ⬜ Bestilles | Rollen `roles/container.admin` + `roles/artifactregistry.admin` |
| Azure AD app-registrering | ⬜ Bestilles | For autentisering av interne brukere |
| Terraform Cloud / lokal state | ⬜ Velges | Anbefales: GCS-bucket som Terraform remote state |
| `gcloud` og `kubectl` installert | ⬜ Lokalt | `brew install --cask google-cloud-sdk` |

---

## Faseplan

### Fase 1 — GCP-prosjekt og grunninfrastruktur (dag 1–2)

#### 1a. Opprett GCP-prosjekt

```bash
gcloud projects create ao-ki-transkribering \
  --organization=NAVIKT_ORG_ID \
  --name="AO KI Transkribering"

gcloud billing projects link ao-ki-transkribering \
  --billing-account=BILLING_ACCOUNT_ID
```

Aktiver nødvendige API-er:
```bash
gcloud services enable \
  container.googleapis.com \
  artifactregistry.googleapis.com \
  compute.googleapis.com \
  iam.googleapis.com \
  --project=ao-ki-transkribering
```

#### 1b. Terraform-oppsett

Anbefalt mappestruktur i repoet:

```
infra/
  terraform/
    main.tf          # GKE-cluster, nodepooler
    variables.tf
    outputs.tf
    versions.tf
  k8s/
    namespace.yaml
    transkribering/
      deployment.yaml
      service.yaml
      hpa.yaml
    referat/
      deployment.yaml
      service.yaml
    ingress.yaml
    cert.yaml
  docker/
    transkribering/
      Dockerfile
    referat/
      Dockerfile
```

Remote state-bucket:
```bash
gsutil mb -p ao-ki-transkribering -l europe-north1 \
  gs://ao-ki-transkribering-tfstate
```

---

### Fase 2 — GKE Standard-cluster med GPU-nodepool (dag 2–4)

#### 2a. Terraform: Cluster og nodepooler

```hcl
# infra/terraform/main.tf

resource "google_container_cluster" "transkribering" {
  name     = "ao-ki-transkribering"
  location = "europe-north1"
  project  = var.project_id

  # Fjern default nodepool – vi definerer egne
  remove_default_node_pool = true
  initial_node_count       = 1

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  network    = "default"
  subnetwork = "default"
}

# CPU-nodepool for API og ingress
resource "google_container_node_pool" "cpu" {
  name     = "cpu-pool"
  cluster  = google_container_cluster.transkribering.id
  location = "europe-north1"

  autoscaling {
    min_node_count = 1
    max_node_count = 3
  }

  node_config {
    machine_type = "n2-standard-4"
    disk_size_gb = 50
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    workload_metadata_config { mode = "GKE_METADATA" }
  }
}

# GPU-nodepool med NVIDIA L4
resource "google_container_node_pool" "gpu" {
  name     = "gpu-pool"
  cluster  = google_container_cluster.transkribering.id
  location = "europe-north1"

  autoscaling {
    min_node_count = 0   # Skaler til 0 ved inaktivitet
    max_node_count = 2
  }

  node_config {
    machine_type = "g2-standard-8"  # 8 vCPU, 32 GB RAM, 1x L4 GPU
    disk_size_gb = 100

    guest_accelerator {
      type  = "nvidia-l4"
      count = 1
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
    }

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    workload_metadata_config { mode = "GKE_METADATA" }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }
  }
}
```

#### 2b. Koble til cluster

```bash
gcloud container clusters get-credentials ao-ki-transkribering \
  --region europe-north1 \
  --project ao-ki-transkribering
```

Verifiser GPU-driver er installert på noden:
```bash
kubectl get nodes -l cloud.google.com/gke-accelerator=nvidia-l4
kubectl describe node <gpu-node> | grep -A5 "nvidia.com/gpu"
```

---

### Fase 3 — Container-images (dag 3–5)

#### 3a. Artifact Registry

```bash
gcloud artifacts repositories create transkribering \
  --repository-format=docker \
  --location=europe-north1 \
  --project=ao-ki-transkribering

gcloud auth configure-docker europe-north1-docker.pkg.dev
```

#### 3b. Dockerfile: Transkriberingstjeneste

```dockerfile
# infra/docker/transkribering/Dockerfile
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir \
    fastapi uvicorn httpx \
    faster-whisper \
    pyannote.audio resemblyzer \
    numpy

# Forhåndslast nb-whisper-modell ved bygg (bakes inn i image)
# Alternativt: last fra GCS ved oppstart for mindre image
COPY scripts/last_modell.py .
RUN python3 last_modell.py  # Kjøres kun ved docker build

COPY server.py static/ ./
EXPOSE 8765
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765"]
```

**Viktig:** Vurder om nb-whisper-modellen (1.5 GB) bakes inn i image eller
lastes fra GCS bucket ved pod-oppstart. Innbakt gir raskere cold start;
ekstern GCS gir mindre image og enklere modell-oppdatering.

#### 3c. Dockerfile: Referattjeneste (Ollama)

```dockerfile
# infra/docker/referat/Dockerfile
FROM ollama/ollama:latest

# Forhåndslast modell inn i image (gir ~22 GB image)
RUN ollama serve & sleep 5 && ollama pull qwen3:32b && kill %1

EXPOSE 11434
CMD ["serve"]
```

> ⚠️ Alternativt: bruk standard `ollama/ollama`-image og last modell fra
> GCS bucket til persistent volume ved første oppstart. Gir mindre image
> men krever PersistentVolumeClaim.

#### 3d. Bygg og push

```bash
IMAGE=europe-north1-docker.pkg.dev/ao-ki-transkribering/transkribering

docker build -t $IMAGE/transkribering:latest infra/docker/transkribering/
docker build -t $IMAGE/referat:latest infra/docker/referat/
docker push $IMAGE/transkribering:latest
docker push $IMAGE/referat:latest
```

---

### Fase 4 — Kubernetes-manifester (dag 5–7)

#### 4a. Namespace og RBAC

```yaml
# infra/k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: transkribering
```

#### 4b. Deployment: Transkriberingstjeneste

```yaml
# infra/k8s/transkribering/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: transkribering
  namespace: transkribering
spec:
  replicas: 1
  selector:
    matchLabels:
      app: transkribering
  template:
    metadata:
      labels:
        app: transkribering
    spec:
      tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
      containers:
      - name: transkribering
        image: europe-north1-docker.pkg.dev/ao-ki-transkribering/transkribering/transkribering:latest
        ports:
        - containerPort: 8765
        env:
        - name: OLLAMA_URL
          value: "http://referat-svc:11434"
        - name: OLLAMA_MODELL
          value: "qwen3:32b"
        resources:
          limits:
            nvidia.com/gpu: "1"
            memory: "8Gi"
          requests:
            nvidia.com/gpu: "1"
            memory: "4Gi"
        livenessProbe:
          httpGet:
            path: /isAlive
            port: 8765
          initialDelaySeconds: 30
        readinessProbe:
          httpGet:
            path: /isReady
            port: 8765
          initialDelaySeconds: 60
```

#### 4c. Deployment: Referattjeneste (Ollama)

```yaml
# infra/k8s/referat/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: referat
  namespace: transkribering
spec:
  replicas: 1
  selector:
    matchLabels:
      app: referat
  template:
    spec:
      tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
      containers:
      - name: ollama
        image: europe-north1-docker.pkg.dev/ao-ki-transkribering/transkribering/referat:latest
        ports:
        - containerPort: 11434
        resources:
          limits:
            nvidia.com/gpu: "1"
            memory: "28Gi"
          requests:
            nvidia.com/gpu: "1"
            memory: "24Gi"
        volumeMounts:
        - name: ollama-data
          mountPath: /root/.ollama
      volumes:
      - name: ollama-data
        persistentVolumeClaim:
          claimName: ollama-pvc
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ollama-pvc
  namespace: transkribering
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: standard-rwo
  resources:
    requests:
      storage: 50Gi
```

#### 4d. Ingress med TLS

```yaml
# infra/k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: transkribering-ingress
  namespace: transkribering
  annotations:
    kubernetes.io/ingress.class: "gce"
    networking.gke.io/managed-certificates: "transkribering-cert"
    kubernetes.io/ingress.global-static-ip-name: "transkribering-ip"
spec:
  rules:
  - host: transkribering.intern.nav.no  # DNS-oppføring må opprettes
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: transkribering-svc
            port:
              number: 8765
---
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: transkribering-cert
  namespace: transkribering
spec:
  domains:
  - transkribering.intern.nav.no
```

---

### Fase 5 — Autentisering (dag 7–9)

For intern NAV-bruk anbefales **Azure AD** via en enkel reverse-proxy eller
sidecar (f.eks. `oauth2-proxy`), siden verktøyet er for NAV-ansatte.

```yaml
# Legg til oauth2-proxy som sidecar i transkribering-deployment
- name: auth-proxy
  image: quay.io/oauth2-proxy/oauth2-proxy:latest
  args:
  - --provider=azure
  - --azure-tenant=NAV_TENANT_ID
  - --client-id=APP_CLIENT_ID
  - --cookie-secret=$(COOKIE_SECRET)
  - --upstream=http://localhost:8765
  - --http-address=0.0.0.0:4180
  ports:
  - containerPort: 4180
```

Ingress peker da på port 4180 (auth-proxy) i stedet for 8765 direkte.

---

### Fase 6 — Kostnadsestimat

| Ressurs | Type | Pris/time | Estimert bruk/mnd | Kostnad/mnd |
|---------|------|-----------|-------------------|-------------|
| GPU-node (transkripsjon) | g2-standard-8 + L4 | ~$1.20 | 40 timer (pilot) | ~$48 |
| GPU-node (Ollama) | g2-standard-8 + L4 | ~$1.20 | 40 timer (pilot) | ~$48 |
| CPU-node (API/ingress) | n2-standard-4 | ~$0.19 | 730 timer | ~$140 |
| Persistent disk (Ollama) | 50 GB SSD | ~$0.17/GB | — | ~$8 |
| Egress/nett | — | ~$0.08/GB | 10 GB | ~$1 |
| **Totalt (pilot, lav bruk)** | | | | **~$245/mnd (~2 600 kr)** |

> GPU-nodene autoskalerer til 0 ved inaktivitet (scale-to-zero via KEDA eller
> manuell nedskalering mellom testsessioner). I pilotperioden vil faktisk
> kostnad være langt lavere enn estimatet over.

**Potensielt langsiktig kostnad (produksjon, 5–10 samtidige veiledere):**
~$1 500–3 000/mnd (~15 000–32 000 kr).

---

### Fase 7 — Utprøving og MVP-kriterier

#### Steg 1: Infrastrukturtesting (dag 9–10)
- [ ] GPU tilgjengelig i pod (`nvidia-smi` fra container)
- [ ] nb-whisper-large laster og transkriberer testfil
- [ ] Ollama svarer på `/api/generate` mot qwen3:32b
- [ ] Tjenestene kommuniserer internt via K8s DNS

#### Steg 2: Funksjonell testing (dag 10–12)
- [ ] Batch-transkripsjon av king.mp3 og tre_stemmer_test.wav
- [ ] Sanntidstranskripsjon via WebSocket
- [ ] Sammendrag og møtereferat genereres uten timeout
- [ ] Responstider innenfor akseptable grenser (se under)

**Akseptansekriterier for MVP:**

| Test | Akseptabel grense | Målt |
|------|-------------------|------|
| Batch-transkripsjon, 45 min møte | < 3 min | — |
| Sanntid-segment (10 sek lyd) | < 3 sek | — |
| Referatgenerering (1 000 ord transkripsjon) | < 60 sek | — |
| Cold start GPU-pod (fra 0) | < 5 min | — |
| TLS-tilkobling via ingress | ✅ | — |
| Ingen data lagret etter sesjon | ✅ (verifiser) | — |

#### Steg 3: Sikkerhet og etterlevelse (dag 12–14)
- [ ] Bekreft at lyddata ikke skrives til disk (kun RAM)
- [ ] Verifiser at Ollama-tjenesten kun er tilgjengelig internt (ingen ekstern eksponering)
- [ ] Azure AD-autentisering fungerer for NAV-ansatte
- [ ] Oppdater Behandlingskatalogen med nytt B-nummer for sky-behandlingen
- [ ] Gjennomgå åpne spørsmål fra ADR-0001

---

## Åpne spørsmål

| Spørsmål | Ansvarlig |
|----------|-----------|
| Hvilken GCP-organisasjon og billing account brukes? | ao-ki-taskforce / økonomi |
| Opprettes eget GCP-prosjekt eller under eksisterende org? | Plattform / PO |
| Hvilken Azure AD-tenant og app-registrering for autentisering? | IT-sikkerhet |
| Skal DNS-oppføring under `intern.nav.no` bestilles? | IT-drift |
| Bakes Ollama-modellen inn i Docker-image eller lastes fra GCS? | ao-ki-taskforce |
| Skal infrastrukturen også brukes for andre åpen-vektede modeller i NAV? | Teknologiavdelingen |
