# ADR 0005: Model capacity and cost on the existing L4 nodes

Date: 2026-10-14

## Status

Accepted for the pilot. Performance results are workload-specific, not a production
capacity guarantee. The later Q8_0 decision below supersedes the initial BF16
Borealis runtime decision; the earlier measurements remain the baseline.
This extends ADR 0004's API contract and updates its speech
runtime and capacity assumptions; the API remains self-hosted and authenticated.

## Context

We have two allocated NVIDIA L4 GPUs in GKE `europe-west4`: one for NB-Whisper
large plus Nemotron diarization, and one for Borealis 12B. Each node is a
`g2-standard-12` (12 vCPU, 48 GiB RAM, one L4), with a 200 GiB SSD (`pd-ssd`) boot disk.
Speech runs in zone `b`, Borealis in zone `c`. No GPU was added for this experiment.

The gateway accepts 32 concurrent requests per application key and enforces
120 requests/minute. Acceptance is distinct from GPU execution capacity. Previously,
speech rejected all but one active request. Increasing the gateway limit alone
did not increase speech throughput. Borealis accepted a burst of 32 but only 22
finished within Cloud Run's 300-second request timeout; ten returned HTTP 504.
Timed-out work continued on the GPU, so automatic retries would add more work.

Both working-hours CronJobs are currently suspended. One replica of each model
remains deployed 24/7. Idle, allocated GPU nodes still incur VM charges.
Retaining the nodes avoids reacquiring scarce regional GPU capacity every morning;
the working-hours cost scenario trades that availability for lower spending.

## Decision

1. Keep the two existing L4 nodes. Optimize serving before buying larger GPUs.
2. Serve NB-Whisper large with faster-whisper 1.2.1 / CTranslate2 4.8.1, FP16,
   two inference workers. Convert the existing mounted NB-Whisper weights on the
   GCloud node; do not substitute generic Whisper weights or run inference locally.
3. Keep Norwegian decoding, beam size 5, word timestamps and quiet audio. Disable
   VAD-based filtering and the no-speech threshold; skip only digital silence.
   Retain the separate Nemotron diarization model and per-recording state.
4. Bound speech admission at 32 outstanding requests. Serialize updates for the
   same recording, permit independent recordings to progress concurrently, and
   retain admission until a cancelled request's inference thread actually finishes.
   Diarization model access remains serialized. Retained recording capacity is
   eight, not 32. Proxy timeouts do not reliably propagate cancellation.
5. Initially raise Borealis `--max-num-seqs` from two to four, keeping its 8192-token context,
   512-token prefill budget, BF16 weights and 6 GiB CPU offload unchanged.
6. Keep gateway IAM, scoped application keys, TLS and private model services.
   Do not raise timeouts or disable limits to disguise overload.
7. Retain the Transformers batch-of-two backend as a rollback option. Assess
   Borealis quantization separately, with provenance and quality checks before
   deployment (see the later Q8_0 decision below). A different precision is not
   automatically equivalent in quality.

## Measured Results

All inference measurements below ran on the deployed GCloud models through the
authenticated Cloud Run gateway, not local models. Warm-up is excluded. Speech
input was 15 seconds of synthetic Norwegian, 16 kHz mono, created from a known
single-speaker sentence and looped. Each request included Nemotron diarization.
The user's inaccurate reference transcriptions were not used as ground truth.

Raw results and the probe are in [the dated benchmark directory](../docs/benchmarks/2026-10-14/).

| Speech runtime | Concurrent requests | Successful | Mean latency | Last completion | Throughput |
|---|---:|---:|---:|---:|---:|
| Transformers, serial baseline | 1 (8 requests) | 8/8 | 9.71 s | 77.65 s total | 6.18 requests/min |
| Transformers, GPU batch 2 | 1 | 1/1 | 9.90 s | 9.90 s | 6.06 requests/min |
| Transformers, GPU batch 2 | 32 | 32/32 | 118.56 s | 222.20 s | 8.64 requests/min |
| CTranslate2 FP16, 2 workers | 1 | 1/1 | 1.75 s | 1.75 s | 34.32 requests/min |
| CTranslate2 FP16, 2 workers | 8 | 8/8 | 5.78 s | 9.22 s | 52.05 requests/min |
| CTranslate2 FP16, 2 workers | 32 | 32/32 | 19.53 s | 36.50 s | 52.60 requests/min |
| CTranslate2 FP16, isolated repeat | 1 | 1/1 | 1.73 s | 1.73 s | 34.59 requests/min |
| CTranslate2 FP16, isolated repeat | 32 | 32/32 | 21.82 s | 41.66 s | 46.09 requests/min |

The 32-request speech bursts completed about 5.3-6.1 times faster than Transformers
batching. The first CT2 run overlapped Borealis traffic but had no rejected speech
requests; the isolated repeat confirms the improvement, with normal run-to-run
variation. Observed idle GPU memory after the first load test was 4,616 MiB for CT2 plus
diarization, versus approximately 19,830 MiB for Transformers plus diarization.
These are observations, not memory reservations or worst-case peaks. GPU memory
headroom does not establish a safe higher concurrency without another test.

Four persistent recording sessions then submitted five rounds of chunks at
14-second intervals (15-second audio with one-second overlap): **20/20 HTTP 200**,
mean latency **3.71 s**, maximum **4.96 s**, total **60.90 s**. Completion lag stayed
below five seconds, rather than increasing each round. This is a one-minute
synthetic session test, not a long-duration soak or an eight-session guarantee.
The final round included `SPEAKER_UNKNOWN` for some words despite a single voice;
speaker attribution remains a known quality limitation. The harness saved the
complete results, then hit a reporting-only `NameError`; that harness defect was
fixed before subsequent probes. No inference request failed in this run.

A separate cloud smoke test returned the same synthetic sentence at normal
volume and at -20 dB (1.85 s / 1.80 s); digital silence returned no text (0.33 s).
This checks that quiet audio is not discarded, not accuracy across real recordings.

The short Borealis prompt asks for three bullet points from fictional meeting
notes. It requests at most 128 output tokens but actually generates 48 tokens.
This is not representative of a long, extensive meeting summary.

| Borealis configuration | Burst size | Successful | Mean successful latency | Last completion |
|---|---:|---:|---:|---:|
| 2 active sequences | 32 | 22/32; 10 HTTP 504 | 160.00 s | 300.26 s including failures |
| 4 active sequences | 4 | 4/4 | 27.19 s | 27.33 s |
| 4 active sequences | 8 | 8/8 | 40.53 s | 54.03 s |
| 4 active sequences | 32 | 32/32 | 120.70 s | 214.33 s |

The isolated four-sequence 32-request test completed without HTTP 429/504,
at **8.96 successful requests/minute**, versus **4.40** for the timed-out baseline.
The baseline metric excludes the ten late completions after their HTTP 504,
so this is successful gateway throughput, not a pure GPU throughput ratio.
There were no pod restarts during these runs. Observed Borealis GPU memory was
19,860 MiB, with 100% GPU utilization while generating.

The four-sequence experiment used the same vLLM image digest as the baseline:
`sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3`.
The first overlapping speech/Borealis run hit the shared gateway concurrency
limit and is excluded from isolated model-capacity comparisons.

A separate, longer fictional meeting prompt requested approximately 150 words,
with a 384-token output cap. **Two simultaneous summaries both succeeded**,
producing **323 tokens each**, `finish_reason=stop`, in **178.34 / 178.97 seconds**.
There was no truncation or HTTP timeout. This remains approximately three minutes
for an extensive answer even at low concurrency; the short-prompt capacity is
not a useful latency promise for extensive summaries. This was an endpoint and
latency check, not an independent evaluation of factual summary quality.

## Quality And Capacity Boundaries

- A nonempty transcription and `SPEAKER_00` prove endpoint operation, not word
  accuracy or correct speaker separation. The synthetic fixture has one speaker.
- CT2 and Transformers use the same weights and beam size, but differ in decoding
  implementation. Their outputs were not byte-identical. Human-reviewed Norwegian
  conversations, quiet speech and multiple speakers are needed before claiming
  quality equivalence. No WER or diarization-error score is claimed here.
- A burst is finite queued work. Ongoing meetings continuously submit chunks;
  sustainable capacity must keep completion lag bounded across many chunks.
- Per-key gateway limits are shared by speech and summaries. Test each model in
  isolation for a baseline, then test the realistic mixed workload separately.
- Long summaries can still exceed gateway/application deadlines. A successful
  48-token burst does not establish success for 512- or 1024-token outputs.
- CPU offload requires CPU/GPU transfers and is a plausible Borealis bottleneck,
  not a separately measured causal result in this experiment.

## Cost Calculation

Regional on-demand list prices retrieved on 2026-10-14: each complete G2 VM costs
USD 1.051487566/hour, including its L4, CPU and RAM. Do not add another GPU charge.
The system `n2-standard-4` costs USD 0.213832/hour; zonal SSD costs USD 0.187/GiB-month;
the regional GKE management fee is USD 0.10/hour. Sources and live machine/disk
verification are in [the research note](../docs/research/gpu-cost-and-borealis-quantization-2026-10-14.md).
These are [Google regional machine prices](https://cloud.google.com/products/compute/pricing/accelerator-optimized),
[system-node prices](https://cloud.google.com/products/compute/pricing/general-purpose) and
[GKE pricing](https://cloud.google.com/kubernetes-engine/pricing), not invoice amounts.

NOK estimates use **9.5357 NOK/USD**, Norges Bank observation **2026-10-13**,
retrieved 2026-10-14. This is a reference conversion, not Google's NOK SKU tariff.
[Dated exchange-rate query](https://data.norges-bank.no/api/data/EXR/B.USD.NOK.SP?format=csv&lastNObservations=1&endPeriod=2026-10-14).

| Compute only | Per hour NOK | Per 8-hour day NOK | 22 working days, 176 h NOK | 24/7 planning month, 730 h NOK |
|---|---:|---:|---:|---:|
| Speech VM, including diarization | 10.03 | 80.21 | 1,764.69 | 7,319.47 |
| Borealis VM | 10.03 | 80.21 | 1,764.69 | 7,319.47 |
| Both GPU VMs | 20.05 | 160.43 | 3,529.39 | 14,638.94 |

Monthly base: two 200 GiB GPU SSDs USD 74.80; one always-on system VM USD 156.10;
its 50 GiB SSD USD 9.35; regional cluster management USD 73.00. Retaining disks
all month is a conservative assumption for the working-hours scenario: if node
removal also deletes boot disks, their billable lifetime is shorter.

| Infrastructure subtotal including disks and shared base | 176 GPU hours USD | 176 GPU hours NOK | 730 GPU hours USD | 730 GPU hours NOK |
|---|---:|---:|---:|---:|
| Both models | 683.37 | **6,516.42** | 1,848.42 | **17,625.97** |
| Per model, shared base allocated equally | 341.69 | 3,258.21 | 924.21 | 8,812.99 |

Formula, with `H` GPU hours per model:
`USD = 2 * H * 1.051487566 + 400 * 0.187 + 730 * 0.213832 + 50 * 0.187 + 730 * 0.10`.
Multiply the unrounded USD total by 9.5357 for NOK. Shared-base allocation is an
accounting choice, not a second infrastructure charge. 730 hours is a planning
month, not the exact length of October.

These subtotals exclude Cloud Run, GCS storage/operations, Artifact Registry,
load balancers, network/egress, NAT/IPs, logging/monitoring, snapshots and VAT.
No negotiated discounts, commitments, sustained-use discounts or credits are
assumed. They are not a complete project bill.

The eight-hours/day scenario requires GPU nodes to be deleted/scaled to zero
outside that window; scaling only the application to zero is not sufficient.
Allow extra startup/warm-up/drain hours. **The current deployment is 24/7**, so
the 730-hour column is the relevant budget. Faster inference improves capacity
per krone, but does not reduce this hourly price while nodes remain allocated.

## Initial AWQ/GPTQ/FP8 Assessment

The current source is `NbAiLab/borealis-12b`, not its open or preview variants.
No trustworthy compatible AWQ/GPTQ/FP8 derivative was verified. GGUF/MLX
companions are not drop-in replacements for this deployment. L4 hardware support
alone does not prove compatibility with this checkpoint and runtime.

Do not replace the model silently. A later experiment should derive from a pinned,
verified original revision, retain tokenizer/chat template and applicable NB/Gemma
license terms, and validate Norwegian quality, context, memory and throughput
before removing CPU offload. See the [source-backed assessment](../docs/research/gpu-cost-and-borealis-quantization-2026-10-14.md#borealis-lineage-and-quantization).

## Consequences And Rollback

The pilot gets more throughput without adding a GPU. Two active CT2 workers are
not a promise of 32 simultaneous GPU executions, nor of 32 ongoing recordings.
Conversion happens at pod startup and the converted artifact lives in `emptyDir`;
pod replacement repeats conversion and model downloads. A versioned, verified
converted artifact in GCS is a follow-up, not part of this change.

For speech rollback, set `SPEECH_ASR_BACKEND=whisper` and `SPEECH_BATCH_SIZE=2` in
`k8s/vllm-whisper.yaml`; the comparison image contains both runtimes. The previous
known image is `whisper-diarization:20261014-batch2`. The initial Borealis tuning
could be rolled back by restoring `--max-num-seqs` to `2`. After the Q8 switch,
use the full BF16 rollback manifest described below instead. Deploy using `scripts/render-model-manifest.py` with
`--secure`, the gateway CIDR and the current node pool, or the established secure
deployment script. Do not apply the unrendered manifest and lose TLS/auth settings.

Follow-up decisions: validated Borealis quantization without CPU offload, a
cancel-aware asynchronous API for long summaries, realistic mixed-load tests,
and quality evaluation with independently verified Norwegian conversation data.

Verification at completion: both model deployments ready, zero container restarts;
220 non-integration tests passed, two local-model integration tests intentionally
excluded. No additional GPU nodes were provisioned. These model settings were
applied directly to GCloud for the experiments; no NAIS release was required for
the unchanged endpoint contract.

## Follow-up: Official Borealis Q8_0 With llama.cpp

On 2026-10-14, the user approved replacing BF16 vLLM serving with the publisher's
**full-release** `NbAiLab/borealis-12b-gguf` Q8_0, not the older instruct-preview
or the separate open variant. The earlier search found no verified AWQ/GPTQ/FP8
drop-in for vLLM; that did not mean quantized GGUF weights were unavailable.
[Publisher GGUF release](https://huggingface.co/NbAiLab/borealis-12b-gguf).

The model is 12,510,213,152 bytes, revision
`8c53f991d8d88a9f70aa7834732aaa327d3862e0`, SHA-256
`ba3413bfbdcaf1d6bb54b0e4ea39e62e9cb10d1fb110e33901c38d4d9deaad04`.
`scripts/stage-borealis-gguf.py` streams it into the existing GCS bucket, verifies
the checksum before completing the upload, and retains license/provenance files.
The pod verifies the same checksum after download. No local inference or extra
GPU is used. A publisher checksum verifies integrity against that source, not an
independent audit of the model or its license.

The existing Borealis L4 now serves via the official CUDA llama.cpp runtime with
four slots and 32768 aggregate context tokens (8192 per slot). Full GPU layer
offload is requested and context shifting is disabled. Native serving binds only
to loopback port 8001. `worker.llama_api` exposes the existing TLS port 8000 with
the same backend key and route restrictions, a 32-request admission bound, and
streaming response support. It accepts LiteLLM's existing model alias, so NAIS and
Cloud Run configuration do not need to change. Native UI/admin routes are not
exposed. [Official server documentation](https://github.com/ggml-org/llama.cpp/tree/master/tools/server).

The first CUDA-image startup exposed a GKE-specific library-path problem:
`libcuda.so.1` was mounted under `/usr/local/nvidia/lib64`, but that path was absent
from `LD_LIBRARY_PATH`. Device discovery returned no GPUs and llama.cpp fell back
to CPU. Adding the driver path made device discovery report the NVIDIA L4. The
manifest now requires `CUDA0` explicitly so this failure cannot silently produce
a CPU-only deployment. The path/device requirement is covered by a regression test.

This changes both precision and serving engine; any measured speed difference
cannot be attributed solely to quantization. Q8_0 reduces weight memory, but its
size alone does not prove a particular context/concurrency fits or quality is
unchanged. Norwegian summary quality still needs representative human review.

The GPU VM count and types are unchanged, so the compute/base cost calculation
above still applies. GCS now also stores the 12.5 GB quantized artifact; GCS and
registry storage were already excluded from that infrastructure subtotal. Original
BF16 weights remain available for rollback. `k8s/rollback/vllm-borealis-bf16.yaml`
preserves the exact former vLLM image digest and configuration. Render that manifest
with TLS enabled and the allocated node pool to restore BF16; both directions
replace the pod and interrupt Borealis briefly. See the operations guide for the
explicit command. Ordinary deployment applies Q8_0 again unless the desired
manifest is also restored.

### Q8_0 Results

The authenticated GCloud gateway benchmark used the same prompts, token budgets
and temperature as the BF16 baseline. No other load test ran concurrently.

| Workload | BF16 vLLM, 4 sequences | Q8_0 llama.cpp, 4 slots | Q8 outcome |
|---|---:|---:|---|
| Single short summary, 48 output tokens | 31.26 s | 2.80 s | HTTP 200 |
| Four short summaries | 27.33 s total | 3.77 s total | 4/4 HTTP 200 |
| Eight short summaries | 54.03 s total | 6.54 s total | 8/8 HTTP 200 |
| 32 short summaries | 214.33 s total | 26.59 s total | 32/32 HTTP 200 |
| Two longer summaries, 323 tokens each | 178.97 s total | 19.99 s total | 2/2 HTTP 200, `finish_reason=stop` |

The 32-request burst is approximately **8.1 times faster**; the longer-summary
pair is approximately **9.0 times faster**, with equal output token counts in
these specific tests. Mean Q8 burst latency was 15.01 s, last completion 26.58 s;
successful throughput was 72.20 requests/minute. These are finite synthetic
workloads, not a guarantee for all prompts, sustained loads or production users.
Output lengths match the baseline, but that alone does not establish quality
equivalence or factual correctness.

The running llama.cpp process used 16,244 MiB of GPU memory; device discovery
reported the allocated NVIDIA L4. Native `/props` confirmed four slots and 8192
context tokens per slot. Runtime: `0.6.0-dev`, build 11610, commit `091b6a7fc`,
official CUDA image digest
`sha256:b0baccbe2e00b5d74e63f85f9f023c93d61d0f329475269e1e7002e87eb875ea`.
The TLS adapter image digest is
`sha256:b9cde1f79a30c8e300b0c38a2c6b242627a4c2f9f4f16dcf1d92e6c2a7885a64`.

In-pod route checks returned 401 without/with an invalid key, 200 with the valid
key, and 403 for native props/completion routes. Connecting to native port 8001
via the pod IP failed, while loopback serving worked. Those local route probes
did not validate the TLS certificate chain; successful gateway inference uses
the existing CA-verifying LiteLLM configuration. No credentials were logged.

An end-to-end gateway streaming smoke test returned the correct synthetic answer
about Norway's capital and the final SSE `[DONE]` marker in 1.24 seconds. A request
containing 18,000 repeated words was rejected with HTTP 400 rather than silently
shifting/truncating context. The existing model alias works without gateway or
NAIS environment changes.

Raw Q8 measurements are archived beside the BF16 measurements in the dated
benchmark directory. The new deployment has two ready containers and no restarts;
the speech pod was not replaced. The non-integration suite passed 239 tests.

## Technical References

- [faster-whisper documentation and benchmark caveats](https://github.com/SYSTRAN/faster-whisper).
- [CTranslate2 TransformersConverter](https://opennmt.net/CTranslate2/python/ctranslate2.converters.TransformersConverter.html).
- [vLLM offloading configuration](https://docs.vllm.ai/en/stable/api/vllm/config/offload/).
- [Transformers pipeline batching](https://huggingface.co/docs/transformers/en/main_classes/pipelines#pipeline-batching).
