# GKE GPU cluster — ao-ki-taskforce

GKE Standard cluster in `europe-west4-b` with NVIDIA L4 GPUs for running
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

```bash
cd terraform/gke
terraform init
terraform plan
terraform apply
```

After apply, configure kubectl:

```bash
gcloud container clusters get-credentials ao-ki-gpu \
  --zone=europe-west4-b \
  --project=ao-ki-taskforce-prod-2472
```

Verify GPU nodes scale up:

```bash
kubectl get nodes -l cloud.google.com/gke-accelerator=nvidia-l4 -w
```

## Upload model weights

```bash
MODEL_BUCKET=$(terraform output -raw model_bucket)

# nb-whisper-large (~3 GB, ~5 sec download at GKE startup)
gsutil -m cp -r /path/to/NbAiLab/nb-whisper-large gs://$MODEL_BUCKET/whisper/

# Borealis-12b (~24 GB BF16, ~36 sec download at GKE startup)
# Download from HuggingFace first: huggingface-cli download NbAiLab/borealis-12b
gsutil -m cp -r ~/.cache/huggingface/hub/models--NbAiLab--borealis-12b \
  gs://$MODEL_BUCKET/borealis-12b/
```

## Cost control

GPU nodes autoscale to 0 when idle. To ensure scale-to-zero outside working hours,
create a Cloud Scheduler job (or use `terraform/scheduler/` — coming in Phase 4):

```bash
# Scale down at 18:00 CET weekdays
gcloud scheduler jobs create http gpu-scale-down \
  --schedule="0 16 * * 1-5" \
  --uri="https://container.googleapis.com/v1/projects/ao-ki-taskforce-prod-2472/zones/europe-west4-b/clusters/ao-ki-gpu/nodePools/gpu-l4/setSize" \
  --message-body='{"nodeCount": 0}' \
  --oauth-service-account-email=<scheduler-sa>@ao-ki-taskforce-prod-2472.iam.gserviceaccount.com \
  --location=europe-west4
```

Estimated cost with autoscaling: **~$150–200/month** for a pilot
(GPU nodes active ~40 h/week, system pool always on).

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
                            └── GKE ao-ki-gpu (europe-west4-b)
                                  ├── vLLM: nb-whisper-large  (gpu-l4 nodepool)
                                  └── vLLM: Borealis-12b      (gpu-l4 nodepool)
                            └── LiteLLM gateway (Cloud Run, internal only)
                            └── GCS: ao-ki-taskforce-prod-2472-modeller
```
