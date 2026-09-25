# Operations

Operational commands for the GKE GPU model stack.

## Prerequisites

Authenticate and select the project:

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project ao-ki-taskforce-prod-2472
```

Configure `kubectl`:

```bash
gcloud container clusters get-credentials ao-ki-gpu \
  --region=europe-west4 \
  --project=ao-ki-taskforce-prod-2472
```

## Deploy

Deploy infrastructure, Kubernetes resources, and working-hours scaling:

```bash
./scripts/deploy-gpu-stack.sh
```

Apply only Kubernetes resources after Terraform is already applied:

```bash
./scripts/apply-k8s.sh
```

## Status

Check pods, services, CronJobs, and GPU nodes:

```bash
kubectl get pods -n vllm -o wide
kubectl get svc -n vllm
kubectl get cronjob -n vllm
kubectl get nodes -l cloud.google.com/gke-accelerator=nvidia-l4 -o wide
```

Check the LiteLLM model gateway:

```bash
./scripts/test-model-api.sh
```

Run a heavier concurrency test:

```bash
python3 scripts/stress-test-model-api.py
```

## Start and Stop Models

Whisper and Borealis are warmed automatically on weekdays at 06:45 Europe/Oslo
with explicit GPU zone fallback and scaled down at 17:00.

Start Whisper manually:

```bash
./scripts/start-gpu-deployment-with-fallback.sh vllm-whisper
kubectl -n vllm scale deployment/vllm-whisper --replicas=0
```

Borealis:

```bash
./scripts/start-gpu-deployment-with-fallback.sh vllm-borealis
kubectl -n vllm scale deployment/vllm-borealis --replicas=0
```

Stop both model pods:

```bash
kubectl -n vllm scale deployment/vllm-whisper deployment/vllm-borealis --replicas=0
```

## GPU Stockout

If a pod stays `Pending`, inspect events:

```bash
kubectl describe pod -n vllm -l app=vllm-borealis
kubectl get events -n vllm --sort-by=.lastTimestamp
```

Typical capacity messages:

- `GCE out of resources`
- `ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS`
- `Pod didn't trigger scale-up`
- `node(s) didn't match Pod's node affinity/selector`

Retry the fallback script. It tries the per-zone pools `gpu-l4-a`, `gpu-l4-b`,
and `gpu-l4-c`:

```bash
KEEP_PENDING=true ./scripts/start-gpu-deployment-with-fallback.sh vllm-whisper
KEEP_PENDING=true ./scripts/start-gpu-deployment-with-fallback.sh vllm-borealis
```

If no zone has L4 capacity, the deployment can be left pending so GKE keeps the
GPU request visible to the cluster autoscaler. Scale down manually when you no
longer want to wait for capacity:

```bash
kubectl -n vllm scale deployment/vllm-whisper deployment/vllm-borealis --replicas=0
```

For manual escalation, Terraform defines zero-min A100 pools in the zones where
`nvidia-tesla-a100` is available. They are not used by the working-hours CronJob.
Only run this when L4 has stayed pending and the higher A100 cost is acceptable:

```bash
./scripts/start-gpu-deployment-on-a100.sh --yes vllm-borealis
```

## Image and Model Caching

The vLLM container images are stored in Artifact Registry, and model artifacts
are stored in the GCS model bucket. GPU node pools scale to zero outside active
use, so a newly created GPU node has no local container or model cache.

GKE Image Streaming is enabled on GPU node pools with `gcfs_config` to reduce
container image startup latency. Model files still need to be copied from GCS
and loaded into GPU memory during pod startup.

Check Image Streaming/GCFS on a node pool:

```bash
gcloud container node-pools describe gpu-l4 \
  --cluster=ao-ki-gpu \
  --region=europe-west4 \
  --project=ao-ki-taskforce-prod-2472 \
  --format='yaml(config.gcfsConfig)'
```

## Logs

Whisper:

```bash
kubectl logs -n vllm deployment/vllm-whisper --tail=100
```

Borealis:

```bash
kubectl logs -n vllm deployment/vllm-borealis --tail=100
```

Init container logs for model copy:

```bash
kubectl logs -n vllm deployment/vllm-borealis -c last-ned-modell --tail=100
kubectl logs -n vllm deployment/vllm-whisper -c last-ned-modell --tail=100
```
