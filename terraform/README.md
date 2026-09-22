# GKE GPU cluster — ao-ki-taskforce

Regional GKE Standard cluster in `europe-west4` with NVIDIA L4 GPUs for running
nb-whisper (transcription) and Borealis-12b (meeting summaries) via vLLM.

## Prerequisites

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project ao-ki-taskforce-prod-2472
```

## First-time setup

Create the Terraform state bucket (once only):

```bash
gcloud storage buckets create gs://ao-ki-taskforce-prod-2472-tfstate \
  --project=ao-ki-taskforce-prod-2472 \
  --location=europe-west4 \
  --uniform-bucket-level-access
```

## Deploy

One-step deploy from the repository root:

```bash
./scripts/deploy-gpu-stack.sh
```

Or run the steps manually:

```bash
cd terraform/gke
tofu init
tofu plan
tofu apply
```

After apply, configure kubectl:

```bash
gcloud container clusters get-credentials ao-ki-gpu \
  --region=europe-west4 \
  --project=ao-ki-taskforce-prod-2472
```

Verify GPU nodes scale up:

```bash
kubectl get nodes -l cloud.google.com/gke-accelerator=nvidia-l4 -w
```

Deploy the Kubernetes vLLM resources and working-hours scaler:

```bash
./scripts/apply-k8s.sh
```

Day-to-day operation commands live in [`../docs/operations.md`](../docs/operations.md).

## Upload model weights

```bash
MODEL_BUCKET=$(tofu output -raw model_bucket)

# nb-whisper-large (~3 GB, ~5 sec download at GKE startup)
gsutil -m cp -r /path/to/NbAiLab/nb-whisper-large gs://$MODEL_BUCKET/whisper/

# Borealis-12b (~24 GB BF16, ~36 sec download at GKE startup)
# Download from HuggingFace first: huggingface-cli download NbAiLab/borealis-12b
gsutil -m cp -r ~/.cache/huggingface/hub/models--NbAiLab--borealis-12b \
  gs://$MODEL_BUCKET/borealis-12b/
```

## Cost control

GPU nodes autoscale to 0 when no GPU pods are scheduled. The vLLM deployments
start with `replicas: 0`; `k8s/working-hours-scaler.yaml` installs Kubernetes
CronJobs that scale Whisper up and both deployments down on weekdays:

- `06:45 Europe/Oslo`: `vllm-whisper=1`
- `17:00 Europe/Oslo`: `vllm-whisper=0`, `vllm-borealis=0`

Manual start/stop:

```bash
kubectl -n vllm scale deployment/vllm-whisper --replicas=1
./scripts/start-borealis-with-gpu-fallback.sh
kubectl -n vllm scale deployment/vllm-whisper deployment/vllm-borealis --replicas=0
```

Cold starts still need a fresh GPU node when the pools are scaled to zero. The
custom vLLM images are stored in Artifact Registry, and model artifacts are
stored in the GCS model bucket; new nodes still need to fetch or stream those
remote artifacts because their local container cache disappears with the node.
GPU node pools enable GKE Image Streaming (`gcfs_config`) to reduce container
image pull latency for Artifact Registry images. The weekday 06:45 warm-up is
kept so Whisper is normally ready before office hours.

Borealis is kept manual until its startup time and memory profile are verified.
The default regional `gpu-l4` pool is still used for normal GPU workloads.
Additional zero-min fallback pools (`gpu-l4-a`, `gpu-l4-b`, `gpu-l4-c`) allow
the Borealis starter script to try one zone at a time when L4 capacity is scarce.
They do not create GPU nodes unless Borealis is explicitly started through the
fallback script.

Estimated cost with autoscaling: **~$150–200/month** for a pilot
(GPU nodes active ~40 h/week, system pool always on).

The cluster is regional and may place default GPU nodes in `europe-west4-a`,
`europe-west4-b`, or `europe-west4-c`. The per-zone fallback pools make the
zone retry order explicit for Borealis.

## VPC peering

Request from NAIS team in `#nais` on Slack:
> Vi trenger VPC peering fra `ao-ki-taskforce-prod-2472` til dev-gcp og prod-gcp.
> Team project VPC: `default` i `europe-west4`.

NAIS will add a peering rule to `nais-terraform-modules`. Once done, the NAIS app
can reach the vLLM services via internal IP.

## Architecture

```
NAIS prod-gcp (nais-prod-020f)
  └── ao-ki-transkribering pod
        └── VPC peering → ao-ki-taskforce-prod-2472
                            └── GKE ao-ki-gpu (europe-west4, zones a/b/c)
                                  ├── vLLM: nb-whisper-large  (gpu-l4 nodepool)
                                  └── vLLM: Borealis-12b      (gpu-l4-* fallback nodepools)
                            └── LiteLLM gateway (Cloud Run, internal only)
                            └── GCS: ao-ki-taskforce-prod-2472-modeller
```
