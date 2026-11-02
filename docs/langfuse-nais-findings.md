# Langfuse on NAIS dev-gcp: Findings

Researched 2026-11-02 against official documentation and upstream source. Research
only: no deployment or live infrastructure verification in this research note.
Cloud authentication was expired and Docker stopped at the start of research;
subsequent live checks and build results are recorded in [deployment preparation](langfuse-nais.md). Recommendations
below are conditional, not evidence that provisioning or connectivity succeeds.

## Decision

Use separate, single-replica web and worker Applications, NAIS Postgres
(CloudNativePG, currently preview), NAIS Valkey, and native GCS. ClickHouse must
have an explicitly owned, persistent GCP placement with private connectivity to
NAIS before proceeding. Pin matching web/worker releases and image digests: current
docs cover v4, not just v3. For v4, PostgreSQL >=15 and ClickHouse >=25.12 are
required; Langfuse recommends PostgreSQL 16 and ClickHouse 26.4, but NAIS Postgres
preview currently supports only PostgreSQL 18. Valkey >=8 is
officially supported. This is a dev architecture proposal, not a sized or HA
service. [NAIS Postgres](https://docs.nais.io/persistence/postgres/),
[Langfuse Postgres](https://langfuse.com/self-hosting/deployment/infrastructure/postgres),
[ClickHouse](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse),
[Valkey](https://langfuse.com/self-hosting/deployment/infrastructure/cache).

## Provisioning Boundary

Current provisioning formats and documented workflow contracts:

| Resource | Manifest envelope and key fields | Apply path |
| --- | --- | --- |
| Postgres | `version: v1`, `type: Postgres`, `name`, `spec.majorVersion: "18"` | `nais/setup@v1` with team, then `nais apply <file> --environment dev-gcp` |
| Valkey | `version: v1`, `type: Valkey`, `name`, `spec.memory`, `spec.tier`, explicit supported `spec.version`, `spec.maxMemoryPolicy: noeviction` | `nais/setup@v1`, then `nais apply <file> --team <team> --environment dev-gcp` |

These are NAIS CLI manifests, **not** `nais.io/v1alpha1` Kubernetes database/cache
CRDs. Workflows need repository authorization for the team and GitHub permissions
`contents: read`, `id-token: write`. Do not copy vendor push/main triggers into
this opt-in pilot. [Create Postgres](https://docs.nais.io/persistence/postgres/how-to/create/),
[preview version restriction](https://docs.nais.io/persistence/postgres/how-to/manage/?tab=cli),
[create Valkey via Actions](https://docs.nais.io/persistence/valkey/how-to/create-with-github-actions/index.html),
[Valkey schema](https://docs.nais.io/persistence/valkey/reference/valkey-spec/index.html).

**Implementation boundary:** render only `kind: Application` resources for now.
Make existing, cost-reviewed Postgres and Valkey resources in the team's dev-gcp
Console explicit prerequisites; confirm Postgres creation availability with NAIS.
Reference their names via `uses.postgres` and `valkey`. Do not render/apply
database/cache provisioning resources or let application deployment create them
implicitly. Console and Git must not compete as resource owners: subsequent
`nais apply` can overwrite Console changes. [Postgres management](https://docs.nais.io/persistence/postgres/how-to/manage/?tab=cli),
[Valkey Console guidance](https://docs.nais.io/persistence/valkey/how-to/create-explicit).

## Postgres: Credential Adapter Required

NAIS `spec.uses.postgres` supports `envPrefix` (literal, include the trailing
underscore) and `role: admin|readwrite|read`. Default `admin` supplies both
migration and runtime credentials. These authenticate using TLS client
certificates, **not a password or an IAM token**. For `envPrefix: LF_`:

| Purpose | NAIS variables | Langfuse target |
| --- | --- | --- |
| Web migrations | `LF_PGHOST`, `LF_PGPORT`, `LF_PGDATABASE`, `LF_PGUSER`, `LF_PGSSLCERT`, `LF_PGSSLKEY`, `LF_PGSSLROOTCERT`, `LF_PGSSLMODE` | `DIRECT_URL` using admin identity |
| Web/worker runtime | Same suffixes with `LF_READWRITE_` instead of `LF_` | `DATABASE_URL` using readwrite identity |

Request `admin` only on the migration-running web workload, and `readwrite` on
worker. Do not synthesize `DATABASE_PASSWORD`, or assume Prisma reads these
prefixed variables automatically. [NAIS credential contract](https://docs.nais.io/persistence/postgres/reference/workload-credentials/index.html),
[workload integration](https://docs.nais.io/persistence/postgres/how-to/use-in-workload/index.html).

PEM-to-PKCS12 is a reasonable bridge, **not yet tested here**. Upstream currently
uses Prisma 6; its TLS URL uses `sslidentity=<PKCS12 path>`,
`sslpassword=<identity password>`, `sslcert=<CA path>`, and
`sslaccept=strict`. Do not confuse Prisma's `sslcert` with libpq's client-cert
parameter. Generate separate runtime/admin identities from their certificate/key
pairs, percent-encode URL components, preserve hostname/CA verification, and use
private writable ephemeral storage (restrictive permissions, no secret logging).
Regenerate on startup; verify certificate renewal and reconnect behavior before
relying on long-running pods. Do not disable verification to fix TLS errors.
[Upstream dependencies](https://github.com/langfuse/langfuse/blob/main/packages/shared/package.json),
[Prisma 6 PostgreSQL TLS](https://www.prisma.io/docs/orm/v6/overview/databases/postgresql).

Web's vendor entrypoint runs cleanup plus `prisma migrate deploy`, with
`DIRECT_URL` defaulting to `DATABASE_URL`, then ClickHouse migrations. Set both
URLs explicitly and retain this entrypoint. A wrapper must preserve `dumb-init`,
`/app/web/entrypoint.sh` or `/app/worker/entrypoint.sh`, original command arguments,
and signal propagation. Worker does not run the web schema migration sequence;
start it after successful migration. Validate DDL ownership, cleanup permissions,
and migrations under the actual NAIS admin identity. Do not assume admin users
from different workloads own each other's tables.
[Web entrypoint](https://github.com/langfuse/langfuse/blob/main/web/entrypoint.sh),
[worker entrypoint](https://github.com/langfuse/langfuse/blob/main/worker/entrypoint.sh),
[worker image contract](https://github.com/langfuse/langfuse/blob/main/worker/Dockerfile),
[migration ownership](https://langfuse.com/faq/all/self-hosting-postgresql-table-ownership-migration-failures).

Langfuse's environment reference suggests a shadow database without `CREATEDB`,
but Prisma explicitly says `migrate deploy` does not use one. Do not provision
a shadow database merely to deploy vendor migrations; investigate any actual
permission failure. [Langfuse variables](https://langfuse.com/self-hosting/configuration),
[Prisma shadow database](https://www.prisma.io/docs/orm/prisma-migrate/understanding-prisma-migrate/shadow-database).

Cloud SQL is an alternative with injected password credentials, but NAIS's
documented integration restricts direct access to the owning Application.
Copying credentials or declaring the same instance on web and worker does not
establish supported sharing. Also adapt libpq TLS parameters to Prisma, rather
than blindly copying the injected URL. Do not confuse the newer preview
`/persistence/postgres/` service with the older `/persistence/postgresql/` page's
operator migration warning. Confirm current dev-gcp availability with NAIS.
[Cloud SQL reference](https://docs.nais.io/persistence/cloudsql/reference/),
[legacy operator notice](https://docs.nais.io/persistence/postgresql/).

## Valkey: Compatible, Explicit Mapping Needed

BullMQ supports Valkey; Langfuse requires `maxmemory-policy=noeviction`. Use a
dedicated instance with supported version, persistence enabled, and
`spec.valkey: [{instance: langfuse, access: readwrite}]` on both Applications.
NAIS readwrite includes scripting, pub/sub and transactions; verify actual BullMQ
startup/enqueue/consume ACL operations before considering broader privileges.
[BullMQ](https://docs.bullmq.io/guide/introduction),
[NAIS Valkey ACLs/credentials](https://docs.nais.io/persistence/valkey/reference/),
[instance settings](https://docs.nais.io/persistence/valkey/reference/valkey-spec/index.html).

Map injected `VALKEY_HOST_<INSTANCE>`, `VALKEY_PORT_<INSTANCE>`,
`VALKEY_USERNAME_<INSTANCE>`, `VALKEY_PASSWORD_<INSTANCE>` to `REDIS_HOST`,
`REDIS_PORT`, `REDIS_USERNAME`, `REDIS_AUTH` at runtime. Set
`REDIS_TLS_ENABLED=true`; retain certificate validation, using
`REDIS_TLS_CA_PATH` if the endpoint requires an additional CA. Use the primary,
not replica endpoint. Alternatively map the supplied Redis-compatible `rediss://`
URI to `REDIS_CONNECTION_STRING`; do not pass a `valkeys://` URI unchanged.
Credentials rotate: verify restart/refresh behavior. Do not assume manifest
strings expand runtime variables or that the platform exports Langfuse's names.
[NAIS mapping](https://docs.nais.io/persistence/valkey/reference/),
[Langfuse Redis/TLS options](https://langfuse.com/self-hosting/deployment/infrastructure/cache).

## GCS: Prefer Native ADC, Not S3 HMAC

On both workloads set `LANGFUSE_USE_GOOGLE_CLOUD_STORAGE=true`,
`LANGFUSE_S3_EVENT_UPLOAD_BUCKET=<bucket>`, and optionally
`LANGFUSE_S3_EVENT_UPLOAD_PREFIX=events/`. Leave
`LANGFUSE_GOOGLE_CLOUD_STORAGE_CREDENTIALS` unset to use environment credentials
(ADC); validate the deployed identity's bucket access. This is native GCS despite
the retained `S3` variable names. GCS S3 interoperability instead requires HMAC
access/secret keys; Workload Identity is not a drop-in S3 signing credential.
Langfuse recommends native GCS and documents deletion limitations in S3 mode.
Keep media and batch exports out of the first pilot; their signed URLs add
browser reachability/signing requirements. [Official storage/auth configuration](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage).

Give one manifest ownership of the bucket (`spec.gcp.buckets`, with
`publicAccessPrevention: true`), not duplicate ownership declarations in web and
worker. A dedicated helper/owner manifest is an option, not a deployed resource.
Bind both workload identities to that bucket; proposed fragment under each
Application's `spec`:

```yaml
gcp:
  permissions:
    - resource:
        apiVersion: storage.cnrm.cloud.google.com/v1beta1
        kind: StorageBucket
        name: <actual-bucket-resource-name>
      role: roles/storage.objectAdmin
```

This uses the documented generic IAM-binding fields; confirm the bucket resource
reference resolves and both bindings reconcile in this team's project before
apply. Do not grant project-wide storage admin. Check any additional bucket
metadata permissions against actual SDK calls. Set a reviewed lifecycle/deletion
policy; retention locks are not automatic cleanup. [NAIS buckets](https://docs.nais.io/persistence/buckets/),
[IAM/resource fields](https://docs.nais.io/workloads/application/reference/spec/#gcppermissions).

## ClickHouse and Health

Placement/private routing is a **blocking prerequisite** until live inspection
after authentication refresh. A team-operated GCP VM with persistent disk is one
dev option; managed ClickHouse requires a separate service, networking and cost
decision. Neither is provisioned by a web/worker Application. Do not place its
database on ephemeral application storage or assume installing the vendor Helm
chart is supported NAIS self-service.

For a single node set `CLICKHOUSE_CLUSTER_ENABLED=false`. Supply
`CLICKHOUSE_URL` (HTTP API) and `CLICKHOUSE_MIGRATION_URL` (native TCP), user and
password; prefer TLS endpoints with `CLICKHOUSE_MIGRATION_SSL=true`. Verify both
protocol paths, required DDL/system-table grants, UTC, disk capacity, backups and
owner. An open HTTP port alone does not prove migrations can run.
[ClickHouse requirements and ports](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse).

Use web port 3000 with `/api/public/health` and `/api/public/ready`; worker port
3030 with `/api/health` (no worker ingress). Worker HTTP binding comes from
`HOSTNAME` and `PORT` in `worker/src/env.ts`, consumed by `index.ts`; explicitly
set `HOSTNAME=0.0.0.0`, `PORT=3030`. Allow startup time for migrations, and verify
processing, not just HTTP health. [Health endpoints](https://langfuse.com/self-hosting/configuration/health-readiness-endpoints),
[worker env](https://github.com/langfuse/langfuse/blob/main/worker/src/env.ts),
[listener](https://github.com/langfuse/langfuse/blob/main/worker/src/index.ts).

## Browser Login Is Not SDK Authentication

Wonderwall/Entra browser login is an outer gate, not a Langfuse session or project
membership. Initially keep Langfuse's own controlled user login; disable open
signup after bootstrap. Native Langfuse Entra SSO is a separate integration with
`AUTH_AZURE_AD_*`, an email claim and `/api/auth/callback/azure-ad`; do not assume
NAIS variables configure it automatically. Set `NEXTAUTH_URL` to the canonical
HTTPS browser URL. [Langfuse authentication](https://langfuse.com/self-hosting/security/authentication-and-sso).

SDK OTLP uses `Authorization: Basic base64(public_key:secret_key)` at
`/api/public/otel/v1/traces`, not browser cookies or an Entra Bearer token.
Exclude `/api/public/otel/**` from
`spec.azure.sidecar.autoLoginIgnorePaths` when using Entra autologin. If using
generic `spec.login.enforce`, its exclusion field is `excludePaths` instead.
Do not mix these configurations or exempt all `/api/**`. Add other exact public
SDK paths only when observed (e.g. key checks or score ingestion). Exclusions
remove the outer login gate, **not Langfuse API-key validation**. Verify Basic
headers survive proxying; cookie-free valid ingestion must succeed and invalid
keys must fail without a browser redirect. Use compatible v4 SDKs; legacy
`/api/public/ingestion` is not a general v4 trace-ingestion replacement.
[OTLP endpoint/auth](https://langfuse.com/integrations/native/opentelemetry),
[NAIS autologin exclusions](https://docs.nais.io/auth/reference/#autologin-exclusions),
[generic login fields](https://docs.nais.io/workloads/application/reference/spec/#loginenforce),
[v4 compatibility](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4).

## Shared Service and Release Gates

Public documentation did not establish an operational shared NAV Langfuse
endpoint, owner or onboarding contract. This does **not** establish that no shared
service exists; ask NAIS/AI platform owners before creating another service.
Internal repository references are intentionally not reproduced in this public
repository.

Before provisioning: confirm shared-service options and CNPG dev-gcp readiness;
test the pinned image's certificate adapter/migrations with Docker; refresh cloud
auth and inspect private ClickHouse routing; verify bucket IAM/ADC, Valkey TLS/ACLs
and rotation; review costs for all persistent services and pod requests. Keep any
future workflow explicitly opt-in (`workflow_dispatch`), dev-gcp-only and manually
approved, with no main/push or prod deployment. No billable apply before cost
review. Use synthetic data only and retain stable encryption/session secrets.
These are proposed gates, not implemented automation or completed checks.
