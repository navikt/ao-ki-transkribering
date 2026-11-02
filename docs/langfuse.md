# Langfuse Pilot

For the requested hosted setup, see [NAIS dev deployment preparation](langfuse-nais.md).
The Compose instructions below are optional local testing, not the hosting target.

This optional local pilot complements Grafana; it does not replace NAIS metrics,
logs or tracing. It does not deploy to GCloud, allocate GPUs or enable production
exports. The backend uses the Python SDK directly, without LangChain/LangGraph.

## Start

Start Docker/OrbStack first, then run from the repository root:

```bash
python3 scripts/langfuse-pilot.py up
```

Open http://localhost:3000. Initial migrations and image downloads can take several
minutes. The command waits for web health and datastore health. A failed timeout
does not delete containers or data: use `status` and retry after investigating.

The script generates `observability/langfuse/.env` once, with random credentials
and mode `0600`. It is ignored by Git and excluded from the app image. Use
`LANGFUSE_PILOT_EMAIL` and `LANGFUSE_PILOT_PASSWORD` from that file to sign in;
the project and SDK keys are bootstrapped automatically. The script never prints
credentials. Do not regenerate this file while retaining the existing volumes:
changing encryption/session/database secrets can make existing data inaccessible.

```bash
python3 scripts/langfuse-pilot.py status
python3 scripts/langfuse-pilot.py validate
python3 scripts/langfuse-pilot.py down
```

`down` retains the named volumes and credentials. There is deliberately no
automatic destructive reset command. Never share expanded `docker compose config`
output: it includes credentials. `validate` uses `config --quiet` instead.

## Connect the Local Backend

Install the API requirements into the environment used to start the application:

```bash
python -m pip install -r requirements/api.txt
set -a
source observability/langfuse/.env
set +a
export LANGFUSE_ENABLED=true
export LANGFUSE_RELEASE=$(git rev-parse HEAD)
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 8765
```

Keep your existing model/backend settings. These commands do not configure or
start Whisper/Borealis; model calls still go wherever your app configuration
directs them. Choose a free API port if 8765 is already occupied. The SDK endpoint
is reached from the host backend; `localhost` would mean the backend container
itself if you later containerize that process.

Generate a referat from fictional text. In the pilot project, look for a `summary`
trace with child `llm_call`/`llm_stream` observations. The summary root covers
processing after the job leaves the queue. Older direct/stream API paths have
model-call observations but do not create the background-job summary root.
Exports are batched; allow a few seconds before refreshing the UI.

## Data Boundary

Exported fields are limited to fixed operation names, known model/template/variant
identifiers, a schema version, optional Git SHA release, environment, timestamps,
bounded outcomes and provider-reported integer input/output token counts.
Unknown client-supplied model/template identifiers become `other`.

No audio, prompts, custom instructions, source text, generated text, user identity,
job owner, headers, URLs or exception messages are recorded. We do not use SDK
automatic decorators that capture arguments/results. A private tracer provider
keeps Langfuse separate from the NAIS exporter; an export-time attribute allowlist
removes unexpected fields. Tests inspect serialized OTLP payloads using the real
SDK. Changing this boundary requires an explicit privacy review.

Usage is absent when the model server does not return it; we do not substitute
character counts or fabricated token/currency estimates. No extra usage request
or LLM review call is added. GPU billing still depends on provisioned hours,
not Langfuse's per-token cost estimates. Ollama calls are timed too, but their
usage counts are not currently forwarded.

Instrumentation is disabled unless `LANGFUSE_ENABLED=true`. Missing/invalid
configuration disables it without stopping the app. Remote endpoints must use
HTTPS; HTTP is allowed only for localhost. SDK calls are guarded, network export
is asynchronous, and shutdown flushes outside the event loop. No per-request
flush or authentication-check network call is added to generation.

## Scope and Operating Cost

The Compose stack has web/worker, PostgreSQL, ClickHouse, Redis and MinIO containers
on one host. Only the web port is bound, to `127.0.0.1:3000`; databases, object
storage and worker have no published ports. Registration is disabled after
bootstrap and vendor telemetry/onboarding/AI-agent features are disabled. Media
uploads and browser-accessible object storage are intentionally not configured.

This is not a HA production deployment: no automated backups, SSO, managed storage
or availability guarantees. Data remains on local Docker volumes until explicitly
removed. Do not upload real meeting data to this pilot. Local storage still needs
access control and a deletion policy. Internet egress is not firewall-isolated;
disabled telemetry is not a substitute for network policy.

There is no additional cloud bill for running on your existing laptop, but the
stack consumes real CPU/RAM/disk. Vendor production minima total about 9 vCPU and
21.5 GiB RAM before object-storage overhead; smaller local experiments need
measurement rather than a promised footprint. Do not schedule this on a GPU node
without checking inference headroom. Moving to a cloud CPU host requires a
separate sizing/cost/security decision; this change provisions nothing there.

Prompt experiments and adviser scoring can be added next using fictional cases.
Production instrumentation stays off: no NAIS secrets, egress rules or manifests
are changed by this pilot.

## Sources

- [Official Compose setup](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml)
- [Docker Compose deployment](https://langfuse.com/self-hosting/deployment/docker-compose)
- [Export-time masking](https://langfuse.com/docs/observability/features/masking)
- [Production sizing](https://langfuse.com/self-hosting/configuration/scaling)
