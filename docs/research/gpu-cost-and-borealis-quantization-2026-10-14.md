# GPU cost and Borealis quantization research

Environment/retrieval date: **2026-10-14**. ADR input, not a deployment decision. Only read-only infrastructure queries and public documentation/metadata retrieval were performed; no weights downloaded or deployments changed by this research task.

## Current evidence

Read-only `kubectl get nodes` and `gcloud compute disks list --project=ao-ki-taskforce-prod-2472` confirmed:

| Node suffix | Zone | Machine | Provisioning | Boot disk |
| --- | --- | --- | --- | --- |
| gpu-l4-b-5f602e9a-d2c7 | europe-west4-b | g2-standard-12 | standard | 200 GiB pd-ssd |
| gpu-l4-c-3e3b4e7a-mcbh | europe-west4-c | g2-standard-12 | standard | 200 GiB pd-ssd |
| system-2eaca063-sv53 | europe-west4-c | n2-standard-4 | standard | 50 GiB pd-ssd |

Each GPU node reports 12 CPUs and one NVIDIA L4. The machine specification is 48 GiB host RAM and 24 GB GPU memory; allocatable Kubernetes memory is lower. The system machine has 4 vCPUs and 16 GiB RAM. These are **not Spot nodes**. Importantly, live disks and `terraform/gke/main.tf` both say **pd-ssd**, not the initially assumed pd-balanced. A later pod snapshot placed Borealis on the c node and speech on the b node. [Google machine specifications](https://docs.cloud.google.com/compute/docs/gpus/)

Experiment-owner report during this research (not independently benchmarked here): same-weight CT2 FP16 + Nemotron speech completed all 32 requests successfully in 36.5 s, versus HF batch 222.2 s; reported GPU memory 4,616 versus 19,830 MiB. Borealis seq4, with unchanged image digest, completed a short four-request burst in 31.2 s versus seq2 approximately 54 s. The full 32-request Borealis rerun remains pending to isolate the shared gateway limit. These are preliminary workload-specific results, not general throughput or quality guarantees. Runtime improvement does not reduce the hourly VM price while the nodes remain provisioned.

## Verified price inputs

All prices below were retrieved on **2026-10-14**. Google pages are live price observations, not a dated historical tariff or an invoice; their tariff effective date was not exposed. Regional values were extracted from the official pages' embedded JSON table data by selecting the exact `Netherlands (europe-west4)` entry and default/on-demand column, not the initially displayed Iowa region. The accelerator page exceeded the web reader's size limit, but direct HTTPS retrieval and JSON parsing succeeded.

| Resource | USD unit price | Primary source |
| --- | ---: | --- |
| g2-standard-12, including 1 L4 + 12 vCPUs + 48 GiB | 1.051487566 / VM-hour | [Google accelerator pricing](https://cloud.google.com/products/compute/pricing/accelerator-optimized) |
| n2-standard-4 | 0.213832 / VM-hour | [Google general-purpose pricing](https://cloud.google.com/products/compute/pricing/general-purpose) |
| Zonal pd-ssd | 0.187 / GiB-month; 0.000256164 / GiB-hour | [Google disk table](https://cloud.google.com/products/compute/pricing/accelerator-optimized) |
| Zonal pd-balanced, hypothetical alternative | 0.11 / GiB-month; 0.000150685 / GiB-hour | [Google disk table](https://cloud.google.com/products/compute/pricing/accelerator-optimized) |
| Regional GKE Standard management | 0.10 / cluster-hour | [GKE pricing](https://cloud.google.com/kubernetes-engine/pricing) |

Do not add a separate L4 charge to the G2 machine total. A regional cluster does not imply regional replicated disks. GKE's free-tier cluster credit does not cover regional Standard clusters; assume standard support, not an extended-support surcharge. [GKE fee and eligibility](https://cloud.google.com/kubernetes-engine/pricing)

**FX observation date: 2026-10-13**, distinct from retrieval/environment date. The latest available business-day observation returned by Norges Bank's `B.USD.NOK.SP` series, bounded at 2026-10-14 with `lastNObservations=1`, was **1 USD = 9.5357 NOK** (`UNIT_MULT=0`, four decimals). No 14 October rate is assumed. NOK figures are indicative conversions, not Google's NOK billing tariff. [Exact Norges Bank query](https://data.norges-bank.no/api/data/EXR/B.USD.NOK.SP?format=csv&lastNObservations=1&endPeriod=2026-10-14), [API documentation](https://www.norges-bank.no/en/topics/statistics/open-data/guide-data-warehouse/)

## Cost calculation

Use `H = 8 * 22 = 176` or `H = 730` GPU-node hours per model. Retain all disks and the system node/cluster for the full 730-hour planning month in both scenarios. USD amounts use unrounded inputs; NOK = USD * 9.5357; displayed totals round to two decimals. Disk monthly prices avoid negligible rounding in published hourly disk rates.

| Compute only | 176 h USD | 176 h NOK | 730 h USD | 730 h NOK |
| --- | ---: | ---: | ---: | ---: |
| Speech, one G2 VM | 185.06 | 1,764.69 | 767.59 | 7,319.47 |
| Borealis, one G2 VM | 185.06 | 1,764.69 | 767.59 | 7,319.47 |
| Both | 370.12 | 3,529.39 | 1,535.17 | 14,638.94 |

| Separate monthly base | USD | NOK |
| --- | ---: | ---: |
| Each GPU's 200 GiB pd-ssd | 37.40 | 356.64 |
| Both GPU disks | 74.80 | 713.27 |
| System VM, 730 h | 156.10 | 1,488.50 |
| System 50 GiB pd-ssd | 9.35 | 89.16 |
| GKE regional cluster, 730 h | 73.00 | 696.11 |
| Shared system + system disk + GKE subtotal | 238.45 | 2,273.76 |

Formula for the verified configuration: `both = 2 * H * 1.051487566 + 2 * 200 * 0.187 + 730 * 0.213832 + 50 * 0.187 + 730 * 0.10`.

| Including disks and shared base | 176 h USD | 176 h NOK | 730 h USD | 730 h NOK |
| --- | ---: | ---: | ---: | ---: |
| Both, actual pd-ssd configuration | **683.37** | **6,516.42** | **1,848.42** | **17,625.97** |
| Per model, allocating shared base equally | 341.69 | 3,258.21 | 924.21 | 8,812.99 |
| Both, hypothetical 200 GiB pd-balanced GPU disks | 652.57 | 6,222.72 | 1,817.62 | 17,332.27 |
| Per model, balanced alternative and half shared base | 326.29 | 3,111.36 | 908.81 | 8,666.14 |

The balanced alternative retains the actual 50 GiB system SSD; each GPU disk would cost USD 22.00 / NOK 209.79 monthly. It is a cost comparison only, not an approved disk change. Shared-base allocation is accounting, not an additional marginal charge.

The 176-hour scenario requires actual GPU-node removal outside those hours; idle GPUs and merely limiting request hours do not achieve it. If autoscaling deletes boot disks too, disk cost falls with their provisioned lifetime; retained-disk totals above are conservative for that case. Allow extra startup, model-load, warm-up and drain time. 730 h is a planning convention, not the exact duration of October. [Google billing rules](https://cloud.google.com/products/compute/pricing), [GKE node billing](https://cloud.google.com/kubernetes-engine/pricing)

**Exclusions:** VAT/taxes, negotiated prices, CUDs, sustained-use discounts or other credits, Cloud Run gateway/runtime, logging/monitoring, GCS model/object storage and operations, Artifact Registry, snapshots, networking/egress, load balancers, external IPs, NAT and any other services. These are infrastructure subtotals, not a complete project bill. Eligible discounts can lower actual charges; none are assumed. Non-USD invoices use Google's currency-specific SKU prices rather than this reference FX conversion. [Google pricing and billing caveats](https://cloud.google.com/products/compute/pricing)

## Borealis lineage and quantization

`scripts/upload-modeller.sh` identifies **NbAiLab/borealis-12b** as the source, copied to GCS and loaded locally by `k8s/vllm-borealis.yaml`. It is not `borealis-open-12b` or `borealis-12b-instruct-preview`. The current upstream config is BF16 `Gemma3ForConditionalGeneration`; the model card identifies Gemma 3 12B instruction-tuned ancestry. The upload path alone does not prove the deployed weights' exact upstream revision or checksum. [Publisher model card](https://huggingface.co/NbAiLab/borealis-12b), [config](https://huggingface.co/NbAiLab/borealis-12b/raw/main/config.json)

The actual license is **NB-License v1.0, June 2026**, retaining underlying Gemma terms and additional NB use restrictions. It is not unrestricted Apache-2.0. Any derivative must preserve applicable license, notices and restrictions. This is identification of the published terms, not legal clearance. [Publisher LICENSE](https://huggingface.co/NbAiLab/borealis-12b/raw/main/LICENSE)

**No trustworthy, compatible AWQ/GPTQ/FP8 artifact was verified.** The publisher card links GGUF and MLX companion formats; a live Hub API search returned ten matching entries, including preview/open variants and GGUF/MLX entries, but no AWQ, GPTQ or FP8 checkpoint. This scoped negative search does not prove none exists under another name or privately. Do not substitute a generic quantized Gemma checkpoint or the preview/open Borealis variant. [Hub search queried](https://huggingface.co/api/models?search=borealis-12b&limit=100), [publisher companion formats](https://huggingface.co/NbAiLab/borealis-12b)

L4 is compute capability 8.9 (Ada). vLLM's hardware matrix supports AWQ, GPTQ/Marlin and FP8 W8A8 on Ada, but hardware support is not verification of this Gemma3 multimodal checkpoint, its quantized layers, or the deployed runtime version. MLX files are not an AWQ/GPTQ/FP8 drop-in. [NVIDIA capabilities](https://developer.nvidia.com/cuda/gpus), [vLLM quantization matrix](https://docs.vllm.ai/en/latest/features/quantization/)

**Assessment for the ADR:** retain the same Borealis lineage. A later controlled quantization study could derive an artifact from a pinned, authenticity-verified original revision, preserving tokenizer/chat template and licenses. Approximate weight-only arithmetic is 24 GB BF16, 12 GB FP8, or 6 GB at four bits for 12 billion parameters; these are not measured VRAM footprints and exclude scales, unquantized layers, KV cache and runtime overhead. Validate the exact pinned vLLM image on L4, Norwegian summarization quality, long-context behavior, concurrency, latency and memory before approval. No quantized deployment or performance claim is justified by the present evidence.
