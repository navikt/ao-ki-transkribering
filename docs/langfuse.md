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

LangChain and LangGraph are not required or installed by this integration. The
separate Langfuse infrastructure remains a single-replica pilot; persistent disk
survival after restart does not establish tested backups or high availability.
