# GKE GPU cluster — ao-ki-taskforce

Regional GKE Standard cluster in `europe-west4` with NVIDIA L4 GPUs for running
NB-Whisper/CTranslate2 with Nemotron diarization and Borealis-12b Q8 via llama.cpp.
The established deployment names still use the `vllm-` prefix.

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

Deploy the Kubernetes model resources and suspended GPU administration jobs:

```bash
./scripts/apply-k8s.sh
```

Day-to-day operation commands live in [`../docs/operations.md`](../docs/operations.md).

## Model API security

Follow [the ordered security cutover](../docs/model-api-security.md) before
using sensitive meeting data. It adds scoped dev/prod application credentials,
Cloud Run IAM invocation, and verified TLS to the GPU servers. The security
variable defaults are false for initial bootstrap, while the checked-in
`gke/security.auto.tfvars` enables both controls for the current environment.
Follow the ordered bootstrap for a new environment. Do not put the gateway master
key in NAIS secrets.

## Upload model weights

```bash
# From the repository root; downloads and uploads large model artifacts.
./scripts/upload-modeller.sh --kun-whisper
.venv/bin/python scripts/stage-borealis-gguf.py
```

The speech manifest also requires the separate Nemotron diarization checkpoint.
`scripts/upload-nemotron.sh` stages it together with the experimental Nemotron ASR
checkpoint; that ASR model is not selected in the current cloud deployment.
BF16 Borealis artifacts are retained for the explicit rollback procedure in
[operations](../docs/operations.md#borealis-q8-runtime).

## Cost control

Both model deployments start with `replicas: 1`, requesting one GPU per model.
The scale-up/zone-rotation and scale-down CronJobs in
`k8s/working-hours-scaler.yaml` are both suspended. There is no scheduled hourly
rotation or 17:00 shutdown. Pending pods can still trigger the cluster autoscaler;
suspending the CronJobs does not suspend that autoscaler.

The fallback starter preserves assigned pods rather than requesting additional
GPUs. Allocation is not guaranteed, and a scheduled pod does not
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
custom speech and adapter images are stored in Artifact Registry, and model
artifacts are stored in the GCS model bucket; new nodes still need to fetch or stream those
remote artifacts because their local container cache disappears with the node.
GPU node pools enable GKE Image Streaming (`gcfs_config`) to reduce container
image pull latency for Artifact Registry images. Applying the model manifests
can invoke fallback once; automatic scheduled zone rotation remains suspended.

The fallback script logs UTC timestamps, deployment, pool, scheduling outcome,
and elapsed seconds. It also reports readiness for preserved pods. Scheduling
success means a node was assigned, not that the model API is ready. Timeout logs
include pod events explaining capacity failures. The last 75 successful scaler
Jobs are retained for comparison:

```bash
kubectl -n vllm get jobs --sort-by=.metadata.creationTimestamp
kubectl -n vllm logs job/<vllm-scale-up-job-name>
```

Autoscaler attempts remain visible in GKE events and cluster autoscaler logs.
There are no scheduled zone retries while the CronJob is suspended.

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
pods or deallocate GPUs. Reapplying the manifests keeps the scheduled jobs
suspended and restores one requested replica per model.

The cluster is regional and may place default GPU nodes in `europe-west4-a`,
`europe-west4-b`, or `europe-west4-c`. The per-zone fallback pools make the
zone retry order explicit for Borealis.

## VPC peering

Peering is not required for the current HTTPS gateway path. Do not request it as
part of normal setup. Direct model access would need a separate security review
and is not the configured NAIS route.

## Architecture

```
NAIS prod-gcp (nais-prod-020f)
  └── ao-ki-transkribering pod
        └── HTTPS → LiteLLM gateway (Cloud Run, public URL)
                      └── Direct VPC egress → model internal load balancers
                            └── GKE ao-ki-gpu (europe-west4, zones a/b/c)
                                  ├── NB-Whisper/CTranslate2 + Nemotron diarization
                                  └── Borealis Q8: llama.cpp + authenticated adapter
                            └── GCS: ao-ki-taskforce-prod-2472-modeller
```
