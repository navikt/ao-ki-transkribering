# Operations

Operational commands for the GKE GPU model stack.

API logging, metrics, dashboards, alerts and incident checks are documented in
[operational monitoring](monitoring.md). NAIS API telemetry and GCP GPU telemetry
are separate; an API readiness probe is not an inference availability test.

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

Deploy infrastructure, Kubernetes resources, and GPU administration:

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

### Gateway Concurrency

`model_api_max_parallel_requests` in `terraform/gke` defaults to 32. Terraform
sets both the LiteLLM general limit and the custom-auth per-application-key limit
through `MODEL_API_MAX_PARALLEL_REQUESTS`. Apply Terraform to deploy a change;
committing the files alone does not update the gateway. The 120 requests/minute
per-key limit and model/route authorization remain unchanged.

This allows more in-flight requests, not more simultaneous GPU inference.
Borealis Q8_0 runs two llama.cpp slots, so additional accepted requests
can wait and may exceed gateway or application timeouts. The speech service
admits up to 32 outstanding requests and returns HTTP 429 with `Retry-After`
when full. The deployed speech runtime is `SPEECH_ASR_BACKEND=faster-whisper`,
using the same NB-Whisper weights converted to CTranslate2 FP16 on the GPU node.
`SPEECH_INFERENCE_CONCURRENCY=2` permits two independent recordings to execute
concurrently. Quiet audio is retained, VAD filtering is disabled, and Norwegian
decoding uses beam size 5 and word timestamps. Conversion is repeated after pod
replacement because `/models` is an `emptyDir`; it does not run on the developer's
machine. The alternative `whisper` backend combines independent recordings into
Transformers batches (`SPEECH_BATCH_SIZE`, default 2, maximum 8), with a 0.1-second
collection window (`SPEECH_BATCH_WAIT_SECONDS`). Nemotron diarization retains
separate state per recording and uses serialized access to its shared model.
Concurrent retries for the same recording wait for its state update and reuse
the cached result. Cancelled work does not release admission while its inference
thread is still running. Disconnected requests are skipped before processing
when the disconnect reaches the speech server. Proxy timeouts do not necessarily
propagate a disconnect. The service still permits eight retained recording
sessions. More admitted requests do not mean more real-time meetings; a full
queue can exceed the gateway or application timeout. Deploy the speech image to
activate speech queue changes; applying Terraform only updates the gateway.

See [ADR 0005](../adr/0005-model-capacity-and-cost.md) for measured cloud capacity,
test limitations, rollback settings, and the infrastructure cost calculation.

### Borealis Q8 Runtime

`k8s/vllm-borealis.yaml` keeps the established deployment/service name but runs
two containers: `llama` holds the GPU and serves only on `127.0.0.1:8001`; `vllm`
is the lightweight authenticated TLS adapter on port 8000. The public gateway
model name stays `borealis-12b`. Its historical upstream `/models/borealis-12b`
alias is accepted by the adapter. Native llama.cpp UI/admin routes are not exposed.
The adapter bounds admitted requests at 32 and preserves streaming responses.
The GPU container explicitly includes GKE's `/usr/local/nvidia/lib64` driver path
and requires `--device CUDA0`; a missing CUDA backend must not silently serve on CPU.

The publisher's full-release Q8_0 GGUF revision, size and SHA-256 are recorded in
`k8s/borealis-q8.json`. Initial staging uses:

```bash
.venv/bin/python scripts/stage-borealis-gguf.py
```

This streams into GCS without a local weights file, verifies the SHA-256 before
finalizing, and preserves the publisher's license/provenance. It refuses to
overwrite an existing weights object. Pods download from GCS and verify the hash
again. Do not delete the existing BF16 model files; they remain the rollback path.

Inspect inference logs with `kubectl -n vllm logs deployment/vllm-borealis -c llama`;
adapter logs use `-c vllm`. Inference content logging is disabled. Two slots share
65536 context tokens, preserving 32768 per slot; context shifting is disabled.
Earlier four-slot/8K benchmark results describe the previous configuration and
are not capacity measurements of the current context allocation.

To roll back on the currently allocated `gpu-l4-c` node pool:

```bash
.venv/bin/python scripts/render-model-manifest.py \
  k8s/rollback/vllm-borealis-bf16.yaml 10.164.15.201 \
  --secure --gateway-cidr 10.120.0.0/26 --node-pool gpu-l4-c \
  | kubectl --context gke_ao-ki-taskforce-prod-2472_europe-west4_ao-ki-gpu apply -f -
```

Use the actual allocated pool if it changes. Both switch and rollback replace the
pod and briefly interrupt Borealis. Neither requests an extra GPU. Ordinary
`apply-k8s.sh` reapplies the Q8 manifest; restore the desired manifest as well if
the rollback is intended to persist.

## Start and Stop Models

Whisper and Borealis currently remain at one replica around the clock. Both
working-hours CronJobs are suspended. Allocated GPU nodes continue to cost money
while idle; the eight-hours/day cost scenario in ADR 0005 is not the current
operating schedule.

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

Speech and adapter images are stored in Artifact Registry; the official llama.cpp
CUDA image comes from GHCR. Model artifacts are stored in the GCS model bucket.
Nodes currently remain allocated 24/7. If a node is removed, its replacement has
no local container or model cache.

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
kubectl logs -n vllm deployment/vllm-borealis -c llama --tail=100
kubectl logs -n vllm deployment/vllm-borealis -c vllm --tail=100
```

Init container logs for model copy:

```bash
kubectl logs -n vllm deployment/vllm-borealis -c last-ned-modell --tail=100
kubectl logs -n vllm deployment/vllm-whisper -c last-ned-modell --tail=100
```
## Recording Connection Diagnostics

### Recoverable browser recordings

The current browser uploads 15-second float32 PCM chunks over authenticated HTTP
(`/recordings/{id}/chunks/{sequence}`), with one second of overlap. The legacy
WebSocket endpoint remains available for older clients; its diagnostics below do
not describe the new transport. Quiet audio is retained. Sample offsets, not wall
clock time, define the timeline and transcript timestamps.

Audio is stored in IndexedDB before uploading. Only one chunk per recording is
sent at a time. A processed response acknowledges the exact sequence and sample
range, including silence with no transcript. Failed or lost responses retry the
same chunk after three seconds; a 90-second request timeout does not stop capture.
Stop ends microphone capture and flushes the final partial chunk; pending uploads
continue. The displayed processed/captured durations expose any backlog.

The API caches the latest result and in-flight task per session, preventing
duplicate inference on the same API process. Cache entries expire after ten idle
minutes (cleanup runs every five minutes). This is NOT distributed exactly-once
execution: another replica or a restart may repeat inference and reset speaker
continuity. The browser only appends each acknowledged chunk once. No server-side
audio archive is introduced. `chunk_received`, `chunk_processed`, and `chunk_failed`
events contain sequence/sample metadata, not audio or transcript content.

There is no optional recording checkbox. After Stop, users can download a mono
16 kHz WAV or explicitly delete the temporary recording, cancelling pending
uploads. Download does not erase the recovery copy. WAV export removes overlap.
Reload offers recovery of stored chunks and transcript results. Up to the current
15-second not-yet-sealed chunk can be lost on a tab/browser crash; ordinary network
disconnects do not lose that buffer. Browser storage eviction can also remove data.

Temporary recordings expire eight hours after creation. An open page deletes its
copy at expiry; when the browser is closed, expired records are deleted on the
next app visit, not by a background guarantee. Explicit deletion is preferable on
shared devices. A 512 MiB captured-PCM limit (plus overlap storage) or storage
failure stops capture with a visible warning rather than silently dropping audio.
Downloaded files are outside this retention policy. Review browser-local sensitive
audio retention with the deployment owner before production rollout.

### Browser sleep and capture interruptions

During microphone recording the browser requests a **screen wake lock**. It is
released on Stop, and requested again when the page becomes visible. Unsupported
browsers, denied requests and system releases are shown as warnings; these do not
stop recording. The lock prevents automatic screen sleep where supported, not
manual screen locking, closing a laptop lid or operating-system suspension.
Reliable locked-screen/background recording on iOS cannot be guaranteed by a web
application; that requirement would need a native capture application.

AudioContext state changes and microphone mute/end events are observed separately
from HTTP uploads. Returning to the page attempts to resume an interrupted audio
context; **Fortsett opptak** allows a user-gesture retry. A disconnected microphone
requires Stop and a new recording; it is never silently reopened. Existing captured
audio remains available while processing retries continue.

The timer measures elapsed wall time rather than counting JavaScript callbacks.
A stalled audio clock, muted/ended track or missing frames can create a **possible
audio interruption** marker. Page visibility alone does not prove audio loss.
Markers are estimates, not proof that speech was lost, and cannot recover audio
that the operating system never delivered. They are retained with the local
recording under its existing eight-hour expiry and can be downloaded separately
as an interruption log. The WAV contains captured samples only: no artificial
silence is inserted. Transcript timestamps remain offsets in that captured audio,
not wall-clock meeting times; a persistent warning makes this explicit after a gap.
Sentence fragments are not joined across known interruption offsets.

`POST /diagnostics/recording-capture` emits best-effort `recording.browser_capture`
events with recording ID, a fixed event kind/reason, elapsed/interruption durations,
page visibility, AudioContext state, track state and wake-lock state. No device
labels, user identifiers, audio, transcript or arbitrary exception messages are
sent. Existing application authentication applies; the strict schema rejects extra
fields. Reports may be delayed or absent during suspension/network failure.

Real-device acceptance checks (iOS Safari and the affected laptop browser):

1. Start recording and check that the screen remains awake past its usual timeout.
2. Manually lock/unlock the device; verify the timer, interruption warning and
   automatic recovery or visible Resume action. Check what actually reached WAV.
3. Switch apps and return; verify that continuous capture does not create a gap
   merely because the page was hidden.
4. Stop; verify the wake lock is released, uploads drain, and the interruption log
   and audio can be downloaded. Reload and verify local recovery retains markers.

These hardware/OS behaviours cannot be established by the mocked lifecycle tests.

### Legacy WebSocket diagnostics

The API emits JSON events through the dedicated `transkriberit.diagnostics`
logger at INFO, independently of Uvicorn's default application log level.
Every accepted recording receives a server-generated `recording_id`; this also
identifies its remote diarization session. Search that ID to correlate events.

- `recording.opened`, `stop_requested`, `peer_disconnected`, `close_sent`, and
  `ended` describe the connection lifecycle. A peer disconnect code describes
  what the API observed, not necessarily what the browser observed.
- `recording.heartbeat` every 15 seconds includes connection age, last audio/result
  age, received packet/byte counts, queue depth and in-flight chunk duration.
- `recording.heartbeat_ack` confirms a numbered application heartbeat made the
  API-to-browser-to-API round trip. It includes round-trip time and cumulative
  acknowledgement count. Only the latest outstanding heartbeat is tracked;
  duplicate or stale acknowledgements are ignored. Old browsers do not acknowledge
  heartbeats, so reload before diagnosing missing acknowledgements.
- `recording.event_loop_delayed` reports heartbeat scheduling delays of at least
  one second. `recording.close_requested` records an application close attempt
  before sending it, including attempts during cleanup after a peer disconnect.
- `recording.chunk_queued`, `chunk_started`, `chunk_finished` report queue wait
  and processing duration. `queue_backpressure` reports a full four-chunk queue.
- `model.transcription_attempt`, `transcription_succeeded` and failure events
  report attempt number, latency and HTTP status when available.
- `recording.task_failed`, `send_failed`, `receive_failed`, `handler_failed` and
  `inference_failed` include exception type and stack locations, not exception
  messages, local variables or source lines.
- `recording.browser_closed` is a best-effort authenticated HTTP report from the
  browser after an unexpected socket close. It contains the server ID, close
  code, connection age, pending outgoing byte count, online flag, page visibility
  and AudioContext state. These are client-reported observations, not a verified
  cause. If the whole network is down, the report may not arrive; it is also
  written to the browser console. Absence of a report does not prove a clean exit.
  New clients also report the clean-close flag, received heartbeat count and time
  since the last received message/heartbeat. Older reports remain accepted.

Interpret the sequence, not just close code 1006: missing acknowledgements while
audio continues suggests a downstream delivery or browser receive problem;
event-loop delays suggest API scheduling trouble. Successful acknowledgements
until an abrupt peer disconnect narrow the problem to connection teardown but
do not identify the responsible proxy. Correlate the UTC timestamp with ingress
and Wonderwall logs; request NAIS support when ingress log access is denied.

No new diagnostics contain audio, transcript text, close-reason text, credentials,
headers, user identity or full URLs. The reporting endpoint rejects unknown fields
and restricts metadata types/values. Existing application authentication applies.
Follow the platform log retention policy; do not collect request payloads for
debugging. These events diagnose failures; they do not implement reconnection,
resume lost audio, or change model/proxy timeouts.

```bash
kubectl --context prod-gcp -n ao-ki-taskforce logs deployment/ao-ki-transkribering \
  -c ao-ki-transkribering --since=15m --timestamps | rg 'recording\.|model\.transcription'
```
