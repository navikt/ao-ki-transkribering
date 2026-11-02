# Langfuse Dev Deployment Preparation

Status: Applications **not deployed**. Dedicated dev Postgres and Valkey creation
was requested through NAIS on 2026-11-02 after deployment approval. The user then
chose to wait for confirmed durable ClickHouse storage rather than use disposable
storage. No ClickHouse, Langfuse application or telemetry export has been enabled.
Removal versus retention of the unused backing resources is awaiting the user's
choice; they can incur idle charges. The existing application, production
resources and GPUs are unchanged. This is separate from the local Compose pilot.

## Target

- `ao-ki-langfuse`: web/API on NAIS dev-gcp, browser ingress
  `https://ao-ki-langfuse.intern.dev.nav.no` (not live yet).
- `ao-ki-langfuse-worker`: background processing on NAIS dev-gcp, no ingress.
- Dedicated NAIS Postgres `langfuse` and Valkey `langfuse`.
- GCS bucket owned by web; worker gets bucket-scoped access through its own identity.
- Separately approved, persistent ClickHouse service with verified private routing,
  HTTPS port 8443, native TLS port 9440 and a matching DNS certificate.

The SDK stays disabled until ingestion and access checks succeed. See
[research and source references](langfuse-nais-findings.md) for compatibility details.

## Live Checks on 2026-11-02

GCloud/NAIS authentication was refreshed. Dev-gcp exposes the new
`nais.io/v1` Postgres and Valkey resources, and this user can create Postgres in
`ao-ki-taskforce`. There are no Postgres/Valkey resources in that team namespace
and only the existing Transkriberit Application at the initial inspection. The installed Postgres CRD accepts
major version 18; manifests use that version rather than older documentation's 16/17.

The existing GKE cluster has one system CPU node and the two model GPU nodes.
Listing peerings on its `default` VPC returned none. This is **not proof that all
private routing is impossible**, but no NAIS-to-ClickHouse route has been verified.
Do not expose native database ports publicly to work around it.

Local checks: the web adapter image built successfully on arm64 after adding the
OpenSSL CLI from a matching UBI build stage. A container smoke test passed through
the real vendor entrypoint and verified private PKCS12 generation, file permissions
and strict TLS settings with synthetic certificates and migrations disabled.
This was **not** a database connection/migration or browser-ingestion test. The
worker image download was cancelled due to very slow registry transfer; a later
amd64 build attempt was rejected with HTTP 429 by the upstream registry. That
image's local build/smoke test remains unverified. The manual workflow smoke-tests
both built images before it can deploy either one.

A server-side dry run of the proposed Applications was rejected because the
referenced Valkey does not exist. This confirms the provisioning prerequisite;
it is not successful end-to-end admission validation. No objects were persisted.

Subsequent provisioning requests succeeded for dev Postgres `langfuse` (PostgreSQL
18, 250m CPU, 1Gi RAM, 20Gi disk) and Valkey `langfuse` (1GB SingleNode, Aiven
`hobbyist` plan). Last checks showed Postgres `PROGRESSING` and Valkey `REBUILDING`;
creation is not proof of readiness. The dry run above predates these creations.
No application secrets or real meeting data have been added for Langfuse.

Public repository research did not identify a verified shared NAV Langfuse
endpoint. It does not establish that no internal service exists. Ask NAIS/AI
platform owners before provisioning a duplicate service.

## Files and Build

`images/langfuse/Dockerfile` has `web` and `worker` targets based on upstream v4.
The bootstrap adapter maps NAIS certificate credentials to Prisma PKCS12 identities
in memory-backed ephemeral storage. Web uses the admin identity for `DIRECT_URL`
and the readwrite identity for `DATABASE_URL`; worker receives readwrite only.
Certificate files are private, connection strings are not printed, and TLS
verification is not relaxed. Vendor entrypoints still perform migrations and
start the normal processes; `dumb-init` and shutdown signals are retained.

The upstream ClickHouse migration helper currently appends `skip_verify=true`
when TLS is selected. Our web image removes that known bypass during build and
rejects unknown remaining bypass forms. App traffic uses HTTPS. This needs a real
certificate/migration smoke test, not only a unit test. Migration usernames and
passwords must use URL-safe characters because upstream interpolates them in the
native migration URL; generate passwords as random hex rather than weakening TLS.

```bash
docker build --target web -t transkriberit-langfuse-web:dev images/langfuse
docker build --target worker -t transkriberit-langfuse-worker:dev images/langfuse
```

The synthetic smoke entrypoint is `tests/langfuse_container_smoke.mjs`, mounted
read-only into the image at `/smoke` and run with `--entrypoint node`; it does not
need real credentials, a GPU or external services.

Before deploying, verify matching vendor web/worker releases, actual OpenSSL and
Prisma compatibility, database ownership and renewal/reconnect behaviour when
NAIS certificate credentials rotate. A startup identity conversion alone does
not establish rotation support. Roll pods when credentials rotate until that
behaviour has been demonstrated. No local build result proves CNPG migrations.

## Provisioning Order and Cost Gate

Do not run the following provisioning steps until ClickHouse ownership/private
connectivity and the full monthly estimate are reviewed. All persistent services
can incur costs, including when no meeting is running.

| Component | Proposed pilot allocation | Billing considerations |
| --- | --- | --- |
| Web + worker | Each requests 500m CPU / 2Gi RAM, memory limit 4Gi | NAIS workload charges; idle pods remain running |
| Postgres | 250m CPU / 1Gi RAM / 20Gi disk in resource manifest | Platform primary/standby, storage and backups; not a single free container |
| Valkey | Dedicated 1GB SingleNode, persistent, noeviction | Managed instance fee while provisioned |
| GCS | Private event bucket, 30-day object lifecycle | Stored bytes, operations and possible transfer charges |
| ClickHouse | Not selected or provisioned | CPU, persistent disks, backups and networking must be priced |
| GPUs | No additions or changes | Existing model GPU bill continues independently |

This is a bill of materials, **not a priced NOK/month quotation**. Use the actual
NAIS resource prices and chosen ClickHouse option before approving deployment.
The bucket lifecycle removes old raw event objects, not all ClickHouse/Postgres
traces; review database retention separately. Avoid retention locks as a substitute
for cleanup. No production or HA availability claim is made for this pilot.

After approval, provision the dedicated resources using the current NAIS resource
CLI (not `kubectl apply` on the CLI-format manifests):

```bash
nais apply .nais/langfuse/postgres-dev.yaml --environment dev
nais apply .nais/langfuse/valkey-dev.yaml --environment dev
```

Wait for both resources to become ready before applying Applications. Do not
reference the same Cloud SQL ownership declaration in two applications as a shortcut.

## Secrets

Create fresh dev-only secrets using NAIS secret tooling; never put values in Git,
workflow inputs, rendered Application YAML or logs. Do not reuse local-pilot or
production credentials.

`ao-ki-langfuse-server`, available to web and worker:

- `SALT`, `ENCRYPTION_KEY` (stable random 32-byte values; encryption key as hex).
- `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, optionally `CLICKHOUSE_DB`.

`ao-ki-langfuse-bootstrap`, available to web only:

- `NEXTAUTH_SECRET` (stable random session secret).
- `LANGFUSE_INIT_ORG_ID`, `LANGFUSE_INIT_ORG_NAME`.
- `LANGFUSE_INIT_PROJECT_ID`, `LANGFUSE_INIT_PROJECT_NAME`.
- `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`, `LANGFUSE_INIT_PROJECT_SECRET_KEY`.
- `LANGFUSE_INIT_USER_EMAIL`, `LANGFUSE_INIT_USER_NAME`, `LANGFUSE_INIT_USER_PASSWORD`.

Keep initialization identities/secrets stable across restarts. Confirm the initialized
project/account exists before removing bootstrap-only credentials. Open signup is
disabled. Browser users pass both the outer Entra gate and Langfuse's own login;
native SSO/project membership is not automatically configured by Wonderwall.

Postgres and Valkey connection credentials are supplied by NAIS, not copied into
these secrets. Native GCS uses ADC/Workload Identity: no JSON service-account key
or S3 HMAC key is required. Resolve the bucket resource reference and verify both
service-account IAM bindings before accepting the deployment.

## Opt-In Workflow

`.github/workflows/langfuse-dev.yml` is manual only. Its default builds images
without deploying. It is not part of the existing main/push or production pipeline.
It is not available remotely until the changes are committed/pushed.

Deployment requires explicit `deploy=true`, `prerequisites_verified=true`, the
reviewed ClickHouse DNS name, and a unique bucket name. This acknowledgement is
an operator gate, not an automated network, cost or certificate check. Web deploys
first so its migrations complete before the worker starts. Database/cache creation
is deliberately separate. Configure approval protection on the `langfuse-dev`
GitHub environment before allowing deployment.

`scripts/render-langfuse-nais.py` renders dev Application YAML from built NAIS
registry image references; it does not access a cluster or include secret values.
Its `--component web|worker` option supports separate migration-first deployment.

## Smoke Tests and Backend Enablement

Use fictional data only:

1. Check web/worker readiness, Postgres migrations and ClickHouse native migrations.
2. Confirm GCS event objects use the intended private bucket and Valkey jobs drain.
3. Verify an unauthenticated browser is redirected to Entra; open signup is denied.
4. POST the exact `/api/public/otel/v1/traces` endpoint without cookies: missing or
   invalid Langfuse keys must fail (401/403), not redirect to browser login.
5. Verify the Python SDK's valid Basic-auth OTLP request reaches the project and a
   synthetic job has summary/model-call observations without meeting content.
6. Repeat after pod restart and credential renewal. Inspect TLS failures without
   disabling verification or logging private keys/connection strings.

Only then add the dev client's public/secret SDK keys in a separate
`ao-ki-langfuse-client` secret, reference it from the existing dev Application,
allow outbound HTTPS to the Langfuse ingress, and set `LANGFUSE_ENABLED=true`,
`LANGFUSE_BASE_URL=https://ao-ki-langfuse.intern.dev.nav.no`,
`LANGFUSE_ENVIRONMENT=dev`. Do not patch whole `env`/`envFrom` arrays in a way that
removes the existing model secrets. Keep production unchanged until a separate review.

Only the exact OTLP trace endpoint bypasses browser autologin; Langfuse still
validates project keys. Do not exclude all `/api/**`, enable wildcard inbound access
or treat the outer browser gate as Langfuse API-key authentication.

## Question for NAIS

> Vi ønsker en liten Langfuse-pilot i dev-gcp for ao-ki-taskforce, uten møteinnhold
> i telemetrien. Finnes det allerede en felles Langfuse- eller ClickHouse-tjeneste
> vi bør bruke? Hvis ikke: hva er anbefalt plassering for en vedvarende ClickHouse
> med privat tilgang fra NAIS-appene? Modellprosjektets GKE-nett har ingen VPC-peering
> vi kan se. Vi vil ikke åpne databaseporter offentlig. Kan dere også bekrefte at
> den nye Postgres-tjenesten passer for Prisma med klientsertifikater i denne piloten?
