# Langfuse Application Tracing

Production Langfuse is hosted on NAIS at
https://ao-ki-langfuse.intern.nav.no . Browser users pass NAV production Entra
authentication and then Langfuse's own project login. The application uses project
API keys, not that login or database credentials.

## Application Connection

Only `.nais/prod.yaml` enables tracing. Dev and local runs remain disabled by
default. The dedicated NAIS secret `ao-ki-langfuse-client` contains
`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`. Do not store values in Git, browser
code, commands, logs or this document. Existing model secrets stay separate.

Configuration:

```text
LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=https://ao-ki-langfuse.intern.nav.no
LANGFUSE_ENVIRONMENT=prod
```

The app has an outbound NAIS rule for `ao-ki-langfuse` and HTTPS egress to its
ingress. Langfuse permits the app inbound. Only `/api/public/otel/v1/traces`
bypasses browser autologin; the endpoint still requires valid project Basic auth.
Do not broaden the browser-auth exception to all API paths.

## Exported Data

Each summary job has a `summary` span with nested `llm_call` or `llm_stream`
observations. Allowed fields are timing, bounded outcomes, provider-reported token
counts, model names and packaged prompt/variant IDs. Borealis 4B and 12B are
recognized, including explicit local `:latest` aliases normalized to cloud names.
Basis, Stikkord and Utfyllende 0.1/0.2 IDs are recognized alongside older scenarios.
Unrecognized models/templates become `other`; arbitrary names are never exported.
Token counts are absent when the provider does not report them, not estimated.

Audio, transcripts, prompt text, custom instructions, generated summaries, user
identities, recording/job IDs, exception messages and credentials are not exported.
An isolated tracer provider, restricted observation names and an attribute
allowlist prevent unrelated application spans from entering this exporter.
Tests inspect real serialized SDK exports with private sentinel values.

Use Langfuse for latency, failures, model/prompt-version comparisons and token
usage, not semantic quality inspection of actual meeting content.

## Validation and Rollback

After deployment, check the content-free `llm.observability_enabled` startup event.
Run a fictional summary and confirm its nested calls appear in the pilot project.
Inspect allowed metadata and verify input/output are empty. An SDK or exporter
failure must not change generation results or expose exception text.

Set `LANGFUSE_ENABLED=false` and redeploy to stop app tracing without deleting
stored traces. Credential rotation requires updating the client secret and rolling
the application. Disabling tracing does not delete persisted Langfuse data.

The production worker disables the optional legacy-API migration usage scanner
with `QUEUE_CONSUMER_V4_LEGACY_API_USAGE_QUEUE_IS_ENABLED=false`. It depends on
`system.query_log`, while ClickHouse query logging is deliberately disabled to
avoid recording SQL text. This does not disable trace ingestion. Do not enable
query logging merely to silence that optional scanner.

The separate Langfuse infrastructure remains a single-replica pilot; persistent disk
survival after restart does not establish tested backups or high availability.

## Summary Workflow

Summary jobs now run through an in-process LangGraph workflow:

```text
prepare -> generate -> finish
```

The existing prompt selection, context handling, model calls, reviews and bounded
repair logic still run inside the generation engine. This first migration changes
orchestration, not summary wording, repair limits or model quality. Progress,
streaming tokens, final warnings and the first completed result retain their
existing contract. Cancellation closes the running engine.

There is no graph checkpointer, database, separate server, agent loop or new login.
Graph state, including generated text, exists only in memory for the job. Existing
job-store retention remains separate. LangGraph brings `langchain-core` and
LangSmith SDK dependencies, but no LangChain agent or LangSmith service is used.
External LangSmith tracing is explicitly disabled around execution even if ambient
tracing environment variables are set. Do not add default Langfuse/LangChain
callbacks: those can capture graph state and meeting content. Our isolated,
metadata-only Langfuse exporter remains the only intended trace destination.

## Retention Safeguards

The event bucket's 30-day lifecycle does **not** delete ClickHouse/Postgres traces.
Self-hosted Langfuse's built-in retention management requires Enterprise licensing;
this installation must not assume that an environment variable enables it.
See [Langfuse retention](https://langfuse.com/docs/administration/data-retention).

`scripts/langfuse-retention.mjs` provides a separate operator-run cleanup tool using
the supported v4 observations and trace-deletion APIs. It defaults to **dry run**,
with a configurable 30-day cutoff and a 100-trace cap (hard maximum 1000).
The operator must specify a scan start, ideally the project's creation date, to
cover the desired history. The tool reads only core/timing fields, follows cursors
in bounded windows, deduplicates trace IDs, validates project scope and refuses
cap overflow before requesting any deletion. Incomplete or recently updated roots
and traces with recent child observations are skipped. Reports contain counts,
not trace IDs, content or credentials.

Run inside the existing Langfuse Web pod against its loopback API. This uses the
existing project-scoped bootstrap keys in memory, not another account or service;
it does not widen the public ingress's authentication exceptions:

```bash
kubectl --context prod-gcp -n ao-ki-taskforce exec -i deployment/ao-ki-langfuse \
  -c ao-ki-langfuse -- node --input-type=module - \
  --from 2026-10-01T00:00:00Z < scripts/langfuse-retention.mjs
```

Review the dry-run counts and scan coverage first. Actual deletion additionally
requires `--apply --confirm-project PROJECT_ID` with the exact expected project ID.
Project-scoped keys and `LANGFUSE_INIT_PROJECT_ID` already exist in this pod.
For a different execution environment, provide project keys, an HTTPS API origin
and `LANGFUSE_RETENTION_PROJECT_ID` through protected environment configuration.
Never put key values in command arguments. Redirects are refused.

Deletion is queued by Langfuse, not immediate secure erasure. Verify completion
before making retention claims. Backups, saved datasets and audit records need
separate policies. This tool does not configure backups or change those policies.
See [Langfuse deletion guarantees](https://langfuse.com/docs/administration/data-deletion).

**No automatic retention schedule is enabled.** An owner must approve the duration,
scan history, schedule and deletion procedure before making this unattended.
The production dry run on 2026-11-11 found no eligible traces and requested no
deletion. Unit tests cover deletion guards; no destructive production test was run.
