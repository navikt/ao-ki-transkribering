# Summary Grounding

All generation, evidence extraction and checking continue to use Borealis. No
third-party inference provider or alternative model is introduced.

## Processing

1. Count the model-formatted prompt using the running Borealis tokenizer. Reserve
   output and template space. Use the original transcript for drafting whenever
   it fits; do not replace a fitting transcript with intermediate summaries.
2. Extract structured topics, proposals, decisions, open questions, tasks and
   constraints. References select numbered original excerpts. Code attaches the
   exact text; the model cannot substitute a translated or invented quotation.
   Unknown owners/deadlines remain unassigned. Unsupported assignment metadata
   is cleared, not used to guess a person or date.
3. Draft in the selected presentation style. When the complete transcript fits,
   the writer receives that original text rather than the extractor's paraphrases.
   Oversized inputs use cited evidence, then compact evidence only if needed.
4. Split draft lines into Norwegian sentences using `sentence-splitter` and audit
   each sentence against original excerpts, in bounded batches.
   Every claim ID must be assessed; references must exist. Support elsewhere in
   the source can satisfy a claim, but contradictory evidence triggers correction.
5. Independently check every original utterance for missing substantive topics.
   Preserve line boundaries and divide only long utterances into smaller excerpts;
   a short topic must not disappear inside a multi-topic source block.
   This does not depend solely on the extractor's topic list. Minimal/short
   summaries still receive claim checking but do not need to cover every topic.
6. Remove rejected sentences and recheck the retained draft before publication.
   If topics are missing or further correction is needed, minimally edit the
   previous draft using the original source and specific findings. After
   two unsuccessful repairs, fail with a controlled message. Never publish an
   unchecked revision or stream provisional tokens to the browser.

The source is never mechanically converted to Bokmal. Structured model responses
are also left untouched so source references and quoted evidence retain their
original wording. Only the generated presentation uses the existing Bokmal rules.
Truncated model responses are rejected. A truncated evidence extraction is
retried on smaller original source groups, without dropping characters.
Truncated fact checks retry smaller claim batches, then smaller source groups
if needed. Topic checks start with at most 16 excerpts and halve their group on
truncation. Completed checks are retained; the draft is not regenerated just
because a verification response was cut off. All claims and original excerpts
still require checking. A single-check failure and the overall call ceiling
remain bounded failures; no partial JSON or unchecked summary is published.
Fact checks select at most eight direct source references per claim; coverage
checks select at most four representative draft references per topic instead of
listing every repetition.

There are at most 128 inference calls per summary job, including repairs. This
bound accommodates utterance-level coverage of long meetings with small context
windows; it is a ceiling, not a target. Evidence
and audit responses have 4096-token output reservations. A request-local cache
stores prompt hashes and token counts, not a global cache of meeting content.
Evidence and drafts stay in job memory; only the completed summary/status uses
the existing retention-controlled job store. Diagnostics contain stage counts and
fixed error categories, not transcripts, drafts, evidence, tokens or credentials.
An `llm.output_truncated` event records the stage (`EvidenceBatch`, `Audit`,
`Coverage` or `draft`), input-token count, requested output cap and call number.
`llm.verification_split` records the stage and batch sizes when recovery splits
a check, without including the source text or model response.

These are automated checks by the same model that writes the summary, not an
independent factual oracle. A valid reference proves where evidence came from,
not that the model interpreted it correctly. Human review remains mandatory.
More checks can increase latency substantially; no speedup or accuracy score is
claimed from unit tests or a short synthetic meeting.

## Authenticated Token Counting

The client uses the existing IAM/scoped-key protected `POST /v1/chat/completions`
with `stream: false`, `max_tokens: 1` and the sole permitted extension:

```json
{"extra_body":{"transkriberit_count_tokens":true}}
```

LiteLLM forwards this OpenAI-provider extra body to the Borealis adapter. The
adapter applies the server's chat template and tokenizes it with `add_special`
and `parse_special` enabled, matching the inference path's BOS token. It returns
a normal chat-completion envelope containing a typed token count; no inference
is performed. The unused one-token output cap protects against an old adapter.
Unavailable/unsupported counters fall back conservatively for that job. A 401
does not downgrade authentication. Native `/tokenize`, `/props`, `/slots` and
`/apply-template` remain inaccessible externally.

Structured calls use named JSON schemas for evidence, claim checks and topic
coverage. Runtime schemas restrict reference IDs to the supplied source and
draft. The gateway permits only these three schema names with bounded structure,
size and local references; it also accepts `json_object` for compatibility.
Arbitrary `extra_body`, provider overrides, external or cyclic schema references,
native administration routes and unknown models remain forbidden.

The gateway caps schema arrays, including reference-ID enums, at 512 entries.
Evidence extraction and claim verification must therefore group at most 512
original excerpts per request even when the entire prompt fits the context
window. Source IDs and all original characters remain intact across groups.
This limit applies to checks, not to the writer's full-transcript input. A schema
with 513 references otherwise receives HTTP 403 before reaching Borealis.

## Rollout Order

The ordinary frontend workflow alone does not deploy the adapter or gateway auth
code. Do not release the API first: the old gateway rejects JSON-mode parameters.

1. Build a new immutable Borealis adapter image using
   `images/llama-api/Dockerfile`, publish it, and update the `vllm` container image
   in `k8s/vllm-borealis.yaml`. Retain the existing `llama` image, 32K slots, TLS,
   service account, assigned node pool and GPU. Apply via the existing secure
   manifest renderer and verify readiness. The current Recreate strategy briefly
   interrupts summaries while the pod reloads; speech is separate.
2. Apply the reviewed OpenTofu gateway change. `security.tf` versions
   `images/litellm/scoped_auth.py` in Secret Manager and Cloud Run mounts that
   specific version. Confirm the new auth revision accepts only the two narrow
   extensions above. Existing IAM and application keys are unchanged.
3. Build/deploy the frontend/API through its normal NAIS workflow.
4. Check exact token counts, a full-source summary, a genuinely oversized summary,
   and authenticated failures. Do not infer production quality from these checks.

No live deployment, new image tag or gateway revision is implied by a source-code
change. Inspect the running revisions before declaring the feature live.

## Evaluation

Use only the built-in synthetic fixture, not the inaccurate reference transcripts:

```bash
.venv/bin/python scripts/evaluate-summary-grounding.py
.venv/bin/python scripts/evaluate-summary-grounding.py --long
.venv/bin/python scripts/evaluate-summary-grounding.py --long --count-only
.venv/bin/python scripts/evaluate-summary-grounding.py --inject-bad-draft
```

The default path uses the configured model gateway and its existing credentials.
For predeployment testing of the pipeline against the existing cloud GPU, open a
temporary localhost-only kubectl tunnel and use `--native-url`. This bypasses the
HTTP gateway only inside the operator's RBAC-protected test connection, not in the
application. It loads no local model and needs no model API key on the laptop.

```bash
kubectl --context gke_ao-ki-taskforce-prod-2472_europe-west4_ao-ki-gpu \
  -n vllm port-forward deployment/vllm-borealis 18001:8001 --address=127.0.0.1
.venv/bin/python scripts/evaluate-summary-grounding.py --native-url http://127.0.0.1:18001
```

The native test asserts tokenizer counts against actual inference usage. `--debug`
prints structured replies only for this built-in synthetic data. It must not be
adapted into production prompt logging. Inspect the synthetic result for invented
assignments and lost topics; a `checks_passed` result means the pipeline checks passed,
not that independent quality has been established.

The adversarial option inserts a false approval to test without PVK and assigns
PVK work to Vegard, neither supported by the fixture. It requires a correction
before accepting a final result. A separate lexical check requires known names
and topic markers in this synthetic fixture; missing markers fail the evaluation
even if the model approved the draft. This is a coarse omission detector, not
proof of semantic correctness. Inspect the final output as well: this is a
pipeline regression exercise, not an independent accuracy benchmark.

A native count-only cloud check of the repeated synthetic fixture measured
18,969 input tokens for 60,795 source characters; the old conservative estimate
was 33,004 tokens. The measured prompt fits the 29,184-token input budget with a
3,072-token output reservation. This demonstrates avoidable splitting, not a
quality or latency improvement on real meetings.

### Predeployment Finding: 20 October 2026

An adversarial evaluation on the cloud Borealis L4 rejected the two injected
claims, then generated and checked a replacement. The utterance-level run took
139 seconds for a 720-character synthetic source. Nevertheless, the approved
replacement omitted KI competence and assigned privacy follow-up to Marte
without an explicit assignment. An independent fixture-marker check detects the
missing competence topic; human inspection detects the invented responsibility.
The evaluation script now fails on missing known fixture markers instead of
treating model approval alone as success.

This is a failed quality evaluation, not a production-readiness result. Source
IDs and schema-constrained output validate structure, not interpretation. The
same-model verifier remains fallible. The requested rollout proceeds with this
known limitation; it is not a quality sign-off. Generated summaries still require
human review. Do not present the unit suite or the model's own approval as
evidence that these omission and assignment cases are solved.

Protocol references: [llama.cpp template/tokenizer endpoints](https://github.com/ggml-org/llama.cpp/tree/master/tools/server),
[LiteLLM OpenAI-compatible provider](https://docs.litellm.ai/docs/providers/openai_compatible).
