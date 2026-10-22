# Summary Grounding

All generation, evidence extraction and checking continue to use Borealis. No
third-party inference provider or alternative model is introduced.

## Default Compact Path

The application uses `frontend/compact_summary.py`. When the source fits the
writer's context budget, it skips evidence extraction and drafts directly from
the complete original transcript. One compact `CompactReview` call normally
follows. It returns a fixed-length true/false verdict array aligned with the
draft claims, contradictions with source references,
and missing substantive topics, rather than a row for every sentence/source pair.
This is less exhaustive than the reference pipeline below, with the latency
tradeoff explicitly accepted for the two-minute response target.

An unsupported sentence can be removed and rechecked. Missing topics or remaining
errors allow one rewrite, followed by another review. Contradictions anywhere in
the original take precedence over support elsewhere. Larger inputs are grouped
by the actual context budget; every source part is reviewed. Evidence extraction
is reserved for sources that do not fit the writer. All output remains provisional
until the review completes; partial JSON and unreviewed revisions are not shown.
The compact job has a ceiling of 16 inference calls, not a latency guarantee.

The review reserves 768 output tokens and uses contiguous source-ID ranges in its
schema rather than large enums. Membership is still validated against the actual
source. This permits a full-meeting review with more than 512 contiguous source
references without changing gateway size/depth/array limits. Model context limits
still apply. The normal fitting, accepted result takes two inference calls.

Two minutes is a target for ordinary fitting meetings, not an SLA. Long generated
answers, GPU queueing, extraction and repairs can exceed it. Detailed templates
retain their existing output budget; no silent reduction to a brief summary is
used to make a latency benchmark look better. Human review remains mandatory:
the model can approve its own incorrect interpretation, as already observed.

`llm.summary_call_completed` records stage, elapsed seconds, input-token count,
requested output cap and call number without meeting content. Use these timings
and an independent review of the result when assessing the tradeoff.

## Source-Only Follow-Up

### Adviser Formats

`veiledermote` now defaults to a source-only vi/du conversation referat. It can
include relevant health/function, housing, finances, language and practical
constraints when spoken, without importing case background or inferring benefits,
diagnoses, employers' views, decisions, responsibilities or deadlines. The general
meeting template remains the application's initial selection; this change affects
the adviser template, not internal meetings or transcription.

The shared `SYSTEM_VEILEDER_BASIS` defines source faithfulness, vi/du language,
uncertainty, attribution, conditional agreements and adaptive detail. A separate
scope layer selects normal or restricted content. One server-defined purpose
overlay is then added:

- `kartlegging`: situation, experience/education, interests, resources, wishes,
  barriers and further exploration. It must not invent a job goal, diagnosis or
  the assumption that this is a first meeting.
- `oppfolging`: status, experiences, progress/obstacles and agreed next steps.
  Comparisons and previous plans require explicit source support. Complex
  follow-ups may need more detail than a simple initial assessment.

The optional request field `veileder_variant` selects the purpose. Omission keeps
`oppfolging` as the adviser default and preserves old job fingerprints. Invalid
values and purposes supplied for other templates/actions are rejected, not
silently ignored. An explicit change of purpose changes the job fingerprint.
The existing scenario-list endpoint publishes variant IDs, titles, descriptions
and the default, not prompt instructions. Response/job metadata identifies the
effective purpose and restriction setting for comparison and review.

In the UI choose **Veilederreferat**, then **Møteformål: Kartlegging / Oppfølging**.
The restricted-format switch is independent and available for either purpose.
The result heading captures both choices; neither changes audio transcription,
recording acknowledgement or manually edited notes. Purpose settings are locked
while generation is running. Other meeting templates remain unchanged.

The optional `begrenset_referat: true` request field selects the previous restricted
adviser format. It defaults to false, accepts only JSON booleans and is rejected
for other templates/actions. Final, rolling and background-job paths share this
choice. The existing checkbox is now next to the adviser template, unchecked by
default, rather than a recording confirmation. Changing it does not revoke the
separate recording acknowledgement. Output settings are locked during generation,
and the generated output's title identifies the selected restricted format.

In restricted format the guard never appends raw-source follow-up excerpts,
including its brief-summary fallback. This prevents a code-added quotation from
bypassing the selected exclusions. The prompt's exclusions themselves remain
fallible model instructions, not a verified personal-data redactor or an access
control. Neither format changes authentication, processing permission, transcript
capture, retention, disclosure or the permitted destination for a document.
Human review of relevance, accuracy and the intended recipient remains necessary.

### Adviser Evaluation

Treat the six supplied situations as evaluation cases, not six canned histories
embedded in prompts. Start with scenario 3 for Kartlegging and the remaining
cases for Oppfølging, but let actual conversation purpose determine the choice.
For comparison, generate both variants from the same spoken transcript using
the same scope setting and model. Background, agenda and expected findings are
reviewer material, not extra meeting facts fed to the generator.

Advisers should record missing relevant topics, unsupported statements, changed
proposals/agreements, lost qualifications, usefulness of detail and editing effort.
In particular, distinguish an agreed course from a proposed measure awaiting a
doctor's view, and a participant's impression of an employer from the employer's
own assessment. Do not reward filling in background that was never spoken.

Record case ID, selected purpose, scope, generation time, findings and approximate
editing time. Prefer anonymized/synthetic pilot material; retain real transcripts
only in approved storage, not Git or application logs. Unit tests check prompt
composition, routing and isolation; they do not establish semantic accuracy.
No adviser quality study, cloud benchmark or production rollout is implied by
implementing these variants.

The compact path now applies `frontend/summary_assignments.py` before review and
after any sentence filtering. Generated task/ownership sections are discarded.
Norwegian assignment-like sentences and common invented job-role forms outside
those sections are conservatively removed as well. Draft instructions tell the
writer not to generate assignments in the narrative or conclusion. This is a
code boundary in addition to instructions, not another model's approval.

Detailed outputs receive a separate `Oppfølging – kildeutsagn` section containing
only unchanged original utterances. It retains anonymous speaker IDs, conditions
and negations; it never maps a speaker ID to a person's name or paraphrases an
owner/task/deadline. These are source statements about possible follow-up, not
verified commitments, completed tasks or current legal permission. Quoted source
markup is escaped. Brief summaries do not receive an extra follow-up appendix.
If a brief draft consists only of removed assignments, a single source quote of
at most 60 words can preserve a documented task instead of returning an empty
result. Without documented remaining content, generation fails closed.

Candidate selection is deliberately conservative: whole utterances only, no
questions, no utterance over 1,200 characters and at most 16 distinct excerpts.
Only direct named or first-person action statements are selected, not generic
plans such as "Vi skal ...". A first-person modal requires a concrete follow-up
verb, not a social aside. Named statements are prioritized, then restored to
source order. An omitted or ambiguous task is not assigned to a guessed owner.
This is neither an exhaustive task extractor nor a semantic proof: unusual
assignment phrasing can evade the narrative filter, source wording can itself
be inaccurate, and later corrections can affect an earlier statement. The other
summary prose remains model-generated and requires human review. Do not claim
that this guard eliminates all hallucinations or independently verifies facts.

Diagnostics log only numbers of removed units and source excerpts; no quotes,
names, responsibilities or meeting content. The existing job retention applies.
No additional inference call, model image, JSON schema or gateway permission is
introduced. This API-side change requires the ordinary frontend/API deployment
to become live; a code edit alone does not change the running service.

## Exhaustive Reference Pipeline

The older exhaustive implementation remains available through the evaluation
script's `--exhaustive` option, not as the default application path.

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
draft. The gateway permits these schemas and `CompactReview` with bounded structure,
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
For the compact-review update the existing adapter already supports counting;
skip its rebuild/restart. Apply only the gateway schema-allowlist change before
the frontend/API rollout. Do not restart model pods for an API-only change.

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

## Compact Cloud Measurements: 21 October 2026

Tests used the existing cloud Borealis Q8 L4, not a local model. The native
localhost tunnel excludes gateway/IAM overhead and is not a browser end-to-end
benchmark. Only synthetic text was submitted; no inaccurate reference transcript
was used as ground truth.

| Case | Elapsed | Inference | Limitation |
| --- | ---: | --- | --- |
| Early compact prototype, 60,795-character repeated fixture | 54.69 s | Draft + review | Warm/cached draft input; omitted six expected markers and failed the independent coverage check |
| Deliberately false approval and assignment, 720-character fixture | 49.17 s | Review + corrected draft + review | Both injected errors rejected; all expected markers returned, but human inspection still found questionable inferred responsibilities |

These measurements show reduced pipeline overhead, not a quality sign-off or a
two-minute guarantee. The long repeated fixture is an attention/length stress
case, not a representative natural meeting. The observed exhaustive production
jobs took about 24 and 37 minutes before failing verification; content-free job
logs, not access to meeting text, established those durations. Compact review
does not solve Borealis's semantic accuracy limitations.
