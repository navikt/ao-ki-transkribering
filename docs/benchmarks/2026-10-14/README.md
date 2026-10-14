# GCloud model-capacity measurements, 2026-10-14

Decision, interpretation and cost calculation: [ADR 0005](../../../adr/0005-model-capacity-and-cost.md).

These probes use **remote GCloud inference only**, through the existing authenticated
gateway. They consume paid GPU capacity and share the production application-key
limits. Run one benchmark at a time, away from user traffic. Do not retry failed
bursts automatically: timed-out Borealis requests can continue on the server.
Credentials are obtained through gcloud subprocesses, held in memory and never
written into results. Authenticate both gcloud and the service's scoped key access
before running. The probes do not provision GPUs or disable authentication.

## Fixture And Method

`synthetic.wav` is 15 seconds of looped, synthetic Norwegian, mono 16 kHz PCM16.
The spoken source sentence is: "Dette er en test av transkripsjon. Vi har avtalt
et nytt møte på mandag, Kari sender planen på fredag." The final loop is cut at
15 seconds. It contains one voice, not a real meeting or personal data. It cannot
validate multi-speaker diarization. User reference transcripts are not used.

Fixture SHA-256: `41d309d4f9ab7d16e75a681d45eee0eabc5237ab3ad02b3a53b7f52fb03b9ddf`.
Speech comparison image: `whisper-diarization:20261014-runtime-comparison`, digest
`sha256:7178291865a8b48f1e79bd861065edcf1a39236e636f417857fdb4733a4419d0`.
Borealis baseline and four-sequence image digest:
`sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3`.

The short Borealis prompt is embedded in `cloud_probe.py`; responses generated
48 tokens with a 128-token budget. `summary-prompt.txt` contains a longer fictional
meeting for a separate summary check. Saved JSON includes counts, timing, statuses
and finish reasons, not credentials, audio payloads or transcript text. Latencies
include network, gateway, queuing and inference. Throughput counts successful
requests divided by wall time. Warm-up is excluded unless explicitly skipped.

## Reproduce

From the repository root, after the appropriate cloud runtime is deployed:

```bash
.venv/bin/python docs/benchmarks/2026-10-14/cloud_probe.py \
  --kind whisper+nemotron-diarization --levels 1,2,4,8,32 \
  --output /tmp/speech-capacity.json

.venv/bin/python docs/benchmarks/2026-10-14/cloud_probe.py \
  --kind whisper+nemotron-diarization --levels 4 --rounds 5 \
  --interval 14 --recording --output /tmp/speech-recordings.json

.venv/bin/python docs/benchmarks/2026-10-14/cloud_probe.py \
  --kind borealis --levels 4,8,32 --output /tmp/borealis-capacity.json

.venv/bin/python docs/benchmarks/2026-10-14/cloud_probe.py \
  --kind borealis --levels 2 --skip-warmup --max-tokens 384 \
  --prompt-file docs/benchmarks/2026-10-14/summary-prompt.txt \
  --output /tmp/borealis-long-summary.json

.venv/bin/python docs/benchmarks/2026-10-14/quality_smoke.py
```

`--skip-warmup` is only appropriate after the same model is already warm. Recording
rounds have scheduled start times but wait for each preceding round to finish.
`completion_lag_s` measures completion relative to that round's planned start and
exposes backlog; this is not an unbounded open-loop load generator. A round uses
one request per session, and each session retains its UUID and advances 14 seconds.
Only use levels 1 through 8 for retained sessions (server capacity is eight).
The smoke test creates normal, -20 dB and digital-silence variants in memory,
prints the synthetic text and writes metrics to `/tmp/transkriberit-cloud-ct2-quality-smoke.json`.

## Evidence Files

- `transkriberit-loadtest-results.json`: initial serial speech / two-sequence Borealis.
- `transkriberit-burst-limit32-results.json`: raised gateway limit, old runtimes;
  Borealis 22 successes and ten 300-second HTTP 504 responses.
- `transkriberit-cloud-batched-speech-results.json`: Transformers batch size two.
- `transkriberit-cloud-batched-sessions.json`: two sessions, two chunks each with batching.
- `transkriberit-cloud-ct2-results.json`: CTranslate2 FP16, two workers, speech burst.
- `transkriberit-cloud-ct2-isolated.json`: repeat without Borealis traffic; all 32
  succeeded in 41.66 s (the first run took 36.50 s). Retain both, not just the fastest.
- `transkriberit-cloud-ct2-sustained.json`: four sessions, five rounds, one minute.
- `transkriberit-cloud-ct2-quality-smoke.json`: quiet-audio retention and digital silence.
- `transkriberit-cloud-borealis-seq4-isolated.json`: isolated four-sequence test,
  all 32 requests completed before the gateway timeout.
- `transkriberit-cloud-borealis-long-summary.json`: two longer fictional meeting
  summaries, 323 output tokens each, both completed in approximately 179 s.
- `transkriberit-cloud-borealis-q8-results.json`: official Q8_0 with llama.cpp on
  the same L4; 32/32 short summaries completed in 26.59 s.
- `transkriberit-cloud-borealis-q8-long-summary.json`: the same longer prompt and
  two 323-token outputs, completed in 19.99 s.
- `transkriberit-cloud-borealis-q8-contract.json`: gateway streaming and over-context
  rejection, reproducible with `q8_contract_smoke.py` (uses cloud credentials).
- `borealis-seq4-overlapping-excluded.json`: deliberately retained but **excluded**
  from isolated capacity conclusions: the speech run overlapped and consumed the
  same gateway slots, producing 429s unrelated to Borealis GPU capacity.

The first sustained probe saved all results then failed in a reporting branch
with `NameError: sys`; that harness issue is fixed in the archived probe. The
recorded requests all succeeded. Results are small samples, not statistically
stable percentiles, quality scores, or production service-level guarantees.
