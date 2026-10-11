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

Deploy the Kubernetes vLLM resources and GPU retry scaler:

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

Both vLLM deployments start with `replicas: 1`, requesting one GPU per model.
`k8s/working-hours-scaler.yaml` retries L4 zones hourly, 24 hours a day, including
weekends. Pending pods remain active between retries so the cluster autoscaler
can keep trying. Pods already assigned to a node are preserved, including those
still loading. The `vllm-scale-down` CronJob is suspended: there is no scheduled
17:00 shutdown.

Once both GPUs are assigned, hourly checks preserve them rather than requesting
additional GPUs. Allocation is not guaranteed, and a scheduled pod does not
necessarily mean its model API is ready. GPU nodes can still disappear due to
maintenance or failures.

Manual start/stop:

```bash
./scripts/start-gpu-deployment-with-fallback.sh vllm-whisper
./scripts/start-gpu-deployment-with-fallback.sh vllm-borealis
kubectl -n vllm patch cronjob vllm-scale-up --type=merge -p '{"spec":{"suspend":true}}'
# Wait for any active scaler Job to finish before stopping the deployments.
kubectl -n vllm get jobs
kubectl -n vllm scale deployment/vllm-whisper deployment/vllm-borealis --replicas=0
```

Cold starts still need a fresh GPU node when the pools are scaled to zero. The
custom vLLM images are stored in Artifact Registry, and model artifacts are
stored in the GCS model bucket; new nodes still need to fetch or stream those
remote artifacts because their local container cache disappears with the node.
GPU node pools enable GKE Image Streaming (`gcfs_config`) to reduce container
image pull latency for Artifact Registry images. Continuous retries give
Whisper and Borealis more opportunities to obtain GPU capacity.

The scaler logs UTC timestamps, deployment, pool, scheduling outcome, and elapsed
seconds. Later hourly checks also report readiness for preserved pods. Scheduling
success means a node was assigned, not that the model API is ready. Timeout logs
include pod events explaining capacity failures. The last 75 successful scaler
Jobs are retained for comparison:

```bash
kubectl -n vllm get jobs --sort-by=.metadata.creationTimestamp
kubectl -n vllm logs job/<vllm-scale-up-job-name>
```

Autoscaler retries between hourly zone rotations remain visible in GKE events
and cluster autoscaler logs. Zone retries now run outside office hours as well.

The default regional `gpu-l4` pool is still used for normal GPU workloads.
Additional zero-min fallback pools (`gpu-l4-a`, `gpu-l4-b`, `gpu-l4-c`) allow
the starter script and CronJob to try one zone at a time when L4 capacity is
scarce. They do not create GPU nodes unless a model deployment is explicitly
started through the fallback logic.

Manual A100 escalation is available through zero-min pools (`gpu-a100-a`,
`gpu-a100-b`). They are intentionally not used by the CronJob. Use them only
when L4 capacity stays unavailable and the higher cost is acceptable:

```bash
./scripts/start-gpu-deployment-on-a100.sh --yes vllm-borealis
```

Continuous operation permits up to 168 GPU-node hours per model each week.
Allocated GPUs are billed outside office hours as well. Actual cost depends on
provisioning success, GPU/node type, and scale-down delay; the system pool remains
active even without GPUs. Suspending the retry CronJob alone does not stop model
pods or deallocate GPUs. Reapplying the manifests restores continuous retries.

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
