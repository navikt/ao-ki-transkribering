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

Whisper is warmed automatically on weekdays at 06:45 Europe/Oslo and scaled down
at 17:00. Start or stop it manually:

```bash
kubectl -n vllm scale deployment/vllm-whisper --replicas=1
kubectl -n vllm scale deployment/vllm-whisper --replicas=0
```

Borealis is manual. Start it with explicit per-zone GPU fallback:

```bash
./scripts/start-borealis-with-gpu-fallback.sh
```

Stop Borealis:

```bash
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

For Borealis, retry the fallback script. It tries the per-zone pools
`gpu-l4-a`, `gpu-l4-b`, and `gpu-l4-c`:

```bash
./scripts/start-borealis-with-gpu-fallback.sh
```

If no zone has L4 capacity, leave the deployment scaled to 0 to avoid waiting
pods and retry later:

```bash
kubectl -n vllm scale deployment/vllm-borealis --replicas=0
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
