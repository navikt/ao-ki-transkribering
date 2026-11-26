# Nordlyd Qwen 0.6B ASR integration research

Retrieval date: **2026-11-23**. Read-only primary-source research; no weights downloaded, application behavior changed, or deployment performed by this research task.

## Publisher metadata and access

The Hugging Face metadata API confirms the repository exists. It exposes public metadata but declares **manual gated access**. At retrieval, the current revision was `3851466595f3a4229451935779bf14f83698855a`, last modified `2026-11-23T13:48:09Z`. Metadata declares Apache-2.0, `nb`/`nn`/`en`, base model `NbAiLab/Qwen3-ASR-0.6B-custom-tokenizer`, architecture `Qwen3ASRForConditionalGeneration`, and model type `qwen3_asr`. Safetensors metadata reports **782,426,112 BF16 parameters**. These are metadata declarations, not an independently evaluated accuracy or compatibility result. [Publisher model metadata](https://huggingface.co/api/models/NbAiLab/nordlyd-asr-qwen06B-nov26preview)

The public tree lists a **1,564,926,456-byte** `model.safetensors`, tokenizer/processor metadata, `chat_template.jinja`, a model-specific `inference.py`, and training documentation. The weight LFS SHA-256 is masked by the unauthenticated API, so an artifact-integrity pin cannot yet be recorded. [Pinned file tree](https://huggingface.co/api/models/NbAiLab/nordlyd-asr-qwen06B-nov26preview/tree/3851466595f3a4229451935779bf14f83698855a)

Unauthenticated HTTPS requests for the pinned `README.md`, `config.json`, and `inference.py` each returned **HTTP 401**, stating access is restricted. No existing local HF credential was found through the normal `huggingface_hub.get_token()`/HF environment lookup. Therefore the publisher's inference instructions, custom tokenizer behavior, benchmark results, timestamp contract, and minimum runtime versions remain **unverified**. Authorized access is required before selecting a concrete implementation. [Publisher model](https://huggingface.co/NbAiLab/nordlyd-asr-qwen06B-nov26preview), [pinned inference example](https://huggingface.co/NbAiLab/nordlyd-asr-qwen06B-nov26preview/blob/3851466595f3a4229451935779bf14f83698855a/inference.py)

## Upstream serving choices: do not conflate formats

### Authenticated follow-up

The operator subsequently configured a local Hugging Face token. `HfApi().whoami()` succeeded, confirming valid authentication, but downloading the pinned `inference.py` returned HTTP 403: the account is not in the model's authorized list. Account access approval remains necessary; successful login alone does not establish model access. No model weights were downloaded.

The pinned README was available in the local Hugging Face cache. It documents a publisher-supplied `NordlydASR` helper using `qwen-asr`, Norwegian controls (`nob`, `nno`), reading-optimised/verbatim styles, and an optional **separate Norwegian forced aligner**, `NbAiLab/nordlyd-forcedaligner-qwen06B-nov26preview`. Word timestamps require verbatim mode and inputs at most 180 seconds; ASR alone accepts inputs at most 1,200 seconds. Norwegian uses a literal assistant prefix, so upstream language-list validation must not be used blindly. The actual inference implementation and aligner access have not yet been verified. [Pinned publisher README](https://huggingface.co/NbAiLab/nordlyd-asr-qwen06B-nov26preview/blob/3851466595f3a4229451935779bf14f83698855a/README.md)

The README describes downloads as public, but this contradicts the observed gated API response. Treat the actual server access response as authoritative for this account. The publisher strongly discourages public-facing or commercial deployment of this preview and recommends evaluation first; this is advisory under its declared Apache-2.0 license. A selectable evaluation pilot preserving the Whisper default is the intended integration direction, subject to runtime, alignment and GPU validation.

The official `qwen-asr` package source, pinned at upstream commit `7c6daf77a2421100f5fb066495372c00129d39ff`, identifies itself as version `0.0.6`. Its package metadata requires Python `>=3.9`, **`transformers==4.57.6`**, `accelerate==1.12.0`, and additional audio/text dependencies. Its optional vLLM extra pins **`vllm==0.14.0`**. This describes the upstream wrapper, not a verified Nordlyd requirement. [Pinned package metadata](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/pyproject.toml)

Native Transformers support is a separate documented route: the official `Qwen/Qwen3-ASR-0.6B-hf` card states **Transformers `>=5.13.0`**. The supported example loads `AutoProcessor` and `AutoModelForMultimodalLM`, formats audio with `processor.apply_transcription_request`, calls `generate`, slices off input token IDs, and decodes with `return_format="transcription_only"`. Whether Nordlyd's custom-tokenizer checkpoint matches that route must be established from its gated files; matching an architecture name alone is insufficient. [Native HF model instructions](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf/blob/main/README.md), [Transformers Qwen3-ASR documentation](https://huggingface.co/docs/transformers/model_doc/qwen3_asr)

## Audio, timestamps and languages

- Upstream wrapper source normalizes audio to **16 kHz**, accepts float NumPy audio with its sample rate, and splits long inputs. Constants are `MAX_ASR_INPUT_SECONDS=1200`, `MAX_FORCE_ALIGN_INPUT_SECONDS=180`, and `MIN_ASR_INPUT_SECONDS=0.5`. Those are upstream implementation limits, not verified Nordlyd limits. [Pinned audio utilities](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/qwen_asr/inference/utils.py)
- The wrapper returns transcription text/language; timestamps require a **separate forced aligner**. Streaming is currently documented only for its vLLM backend, without batched streams or timestamps. The official demo uses 16 kHz PCM and an example two-second streaming chunk. These capabilities must not be assumed for the custom checkpoint without its inference instructions. [Official upstream usage](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/README.md)
- The upstream wrapper validates forced languages against a fixed list that **does not contain Norwegian**. Blindly calling `language="Norwegian"` will fail validation in that wrapper. Upstream forced alignment also does not list Norwegian among its supported languages. Publisher metadata tagging Bokmål/Nynorsk is not evidence that the upstream wrapper's language validation or aligner was adapted. [Language validation source](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/qwen_asr/inference/utils.py), [forced-alignment language support](https://huggingface.co/docs/transformers/model_doc/qwen3_asr)
- No publisher-specific diarization capability was verified. Keep the existing separate speaker-identification path unless the publisher documents and testing confirms an alternative.

## Repository implications and recommended sequence

The current speech image pins Transformers Git revision `55c3cc9f03cd4fbf2d27d382dcc8fcc544631f8e` in `requirements/benchmark-speech-runtime.txt` for the Nemotron diarizer. Installing the older `qwen-asr` exact Transformers pin could conflict with that runtime. This is a compatibility risk, **not a reproduced dependency failure**. Test a separate environment/image before changing the production dependency set.

`SpeechSessions.transcribe` consumes timestamped ASR chunks to assign speakers. Text-only Qwen output is therefore not a drop-in replacement for the current Whisper path. Do not fabricate word timings or assume an aligner unsupported for Norwegian preserves the contract. The ~1.56 GB BF16 weight size is not total GPU memory: decoder cache, activations, batches, the diarizer, and the cohosted model must be measured together. No Nordlyd L4 memory/latency measurement was available.

Recommended next steps:

1. Obtain manually approved model access and authenticate through the normal HF mechanism; do not put tokens in code, deployment manifests, commands or logs.
2. Read the pinned publisher README/config/inference example, confirm terms and tokenizer/output semantics, and record the weight SHA-256 after authorized download.
3. Choose the publisher-supported runtime and implement an **additional selectable pilot model** with the existing Whisper aliases retained. Validate the exact returned segment/timestamp contract and Norwegian language handling.
4. Evaluate fictional or approved Norwegian samples, silence, interruptions, long recordings, speaker assignment, concurrent requests, and real-time backlog. Measure memory while the current diarizer/cohost run.
5. Promote to the default only after those checks and a rollback test. No production default replacement can be justified by the currently accessible metadata alone.

## Opt-in GPU trial

Authenticated access to the ASR model and aligner now succeeds. The adapter in
`worker/transkribering/nordlyd.py` calls the publisher helper in Bokmål verbatim
mode with word alignment. It runs in a separate image to avoid replacing the
current speech service's Transformers dependencies.

Download the assets from a machine authenticated with Hugging Face. The aligner
revision below is the one specified in the pinned publisher helper. The speech
container loads both directories offline and does not need a Hugging Face token.

```sh
hf download NbAiLab/nordlyd-asr-qwen06B-nov26preview \
  --revision 3851466595f3a4229451935779bf14f83698855a --local-dir /path/to/nordlyd
hf download NbAiLab/nordlyd-forcedaligner-qwen06B-nov26preview \
  --revision 6ddd1268709255a14f576b4db898942eb24b8e38 --local-dir /path/to/nordlyd-aligner
docker build -f images/nordlyd.Dockerfile -t nordlyd-speech .
# Export VLLM_API_KEY with a nonempty test secret before starting.
docker run --rm --gpus all -e VLLM_API_KEY -p 127.0.0.1:8000:8000 \
  -v /path/to/nordlyd:/models/nordlyd:ro \
  -v /path/to/nordlyd-aligner:/models/nordlyd-aligner:ro nordlyd-speech
```

For a local app trial, set `TRANSKRIPSJON_BACKEND=remote`,
`TRANSKRIPSJON_SERVICE_URL=http://127.0.0.1:8000`,
`TRANSKRIPSJON_API_PATH=/v1/audio/transcriptions`, and
`TRANSKRIPSJON_MODELL=nordlyd-qwen06b-preview`. Set `TRANSKRIPSJON_API_KEY`
to the same test secret used for `VLLM_API_KEY`. The app already sends speech in
short chunks. The standalone speech endpoint accepts at most 180 seconds per
request. The word aligner supplies timing, while speaker labels require the
separate diarizer. Compatibility with the existing Nemotron diarizer in this
image has not been verified. The local API test uses a stand-in for the publisher
helper. Real inference, GPU memory, latency and diarization need validation on
an NVIDIA host before connecting this service to the shared gateway.
