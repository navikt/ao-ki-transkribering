const {test} = require("node:test");
const assert = require("node:assert/strict");

function credentials(prefix, user) {
  return Object.fromEntries(Object.entries({PGHOST:"db.pg-team.svc.cluster.local", PGPORT:"5432",
    PGUSER:user, PGDATABASE:"langfuse", PGSSLMODE:"verify-full", PGSSLCERT:"/certs/client.crt",
    PGSSLKEY:"/certs/client.key", PGSSLROOTCERT:"/certs/ca.crt"}).map(([key,value])=>[prefix+key,value]));
}
function environment() {
  return {...credentials("LF_","migration-admin"), ...credentials("LF_READWRITE_","runtime-user"),
    VALKEY_HOST_LANGFUSE:"cache.example.test", VALKEY_PORT_LANGFUSE:"26483",
    VALKEY_USERNAME_LANGFUSE:"app", VALKEY_PASSWORD_LANGFUSE:"PRIVATE_PASSWORD",
    CLICKHOUSE_USER:"langfuse", CLICKHOUSE_PASSWORD:"private_hex_only_123"};
}

test("private ClickHouse trust preserves platform roots for Valkey and public services", async () => {
  const {configureTrust} = await import("../images/langfuse/nais-bootstrap.mjs");
  const writes = [];
  const configured = configureTrust({LANGFUSE_CLICKHOUSE_CA_FILE:"/clickhouse/ca.crt"}, "/private",
    file => file === "/etc/ssl/certs/ca-certificates.crt" ? "PLATFORM ROOTS\n" : "CLICKHOUSE ROOT\n",
    (file, data) => writes.push([file, data]));
  assert.deepEqual(writes, [["/private/ca-bundle.pem", "PLATFORM ROOTS\n\nCLICKHOUSE ROOT\n"]]);
  assert.equal(configured.NODE_EXTRA_CA_CERTS, "/private/ca-bundle.pem");
  assert.equal(configured.SSL_CERT_FILE, "/private/ca-bundle.pem");
  assert.equal(configured.REDIS_TLS_CA_PATH, "/etc/ssl/certs/ca-certificates.crt");
  const old = {NODE_EXTRA_CA_CERTS:"/platform-ca.pem"};
  assert.deepEqual(configureTrust(old, "/private"), old);
});

test("web uses separate runtime and migration client certificates with strict TLS", async () => {
  const {configureEnvironment} = await import("../images/langfuse/nais-bootstrap.mjs");
  let index = 0;
  const configured = configureEnvironment(environment(), "web", () => `/private/identity-${index++}.p12`);
  const runtime = new URL(configured.DATABASE_URL), migrations = new URL(configured.DIRECT_URL);
  assert.equal(runtime.username, "runtime-user");
  assert.equal(migrations.username, "migration-admin");
  assert.notEqual(runtime.searchParams.get("sslidentity"), migrations.searchParams.get("sslidentity"));
  for (const url of [runtime, migrations]) {
    assert.equal(url.searchParams.get("sslmode"), "require");
    assert.equal(url.searchParams.get("sslaccept"), "strict");
    assert.equal(url.searchParams.get("sslcert"), "/certs/ca.crt");
  }
  assert.equal(configured.REDIS_AUTH, "PRIVATE_PASSWORD");
  assert.equal(configured.REDIS_TLS_REJECT_UNAUTHORIZED, "true");
  assert.equal(configured.REDIS_TLS_ENABLED, "true");
});

test("worker needs no migration identity and rejects insecure or absent settings", async () => {
  const {configureEnvironment} = await import("../images/langfuse/nais-bootstrap.mjs");
  const env = environment();
  for (const key of Object.keys(credentials("LF_", "unused"))) delete env[key];
  const configured = configureEnvironment(env, "worker", () => "/private/runtime.p12");
  assert.equal(configured.DATABASE_URL, configured.DIRECT_URL);
  assert.throws(() => configureEnvironment({...env, LF_READWRITE_PGSSLMODE:"prefer"}, "worker", () => ""), /verified TLS/);
  assert.throws(() => configureEnvironment({...env, NODE_TLS_REJECT_UNAUTHORIZED:"0"}, "worker", () => ""), /TLS verification/);
  assert.throws(() => configureEnvironment({...env, VALKEY_PASSWORD_LANGFUSE:""}, "worker", () => ""), /connection settings/);
  assert.throws(() => configureEnvironment({...env, CLICKHOUSE_PASSWORD:"bad&query"}, "worker", () => ""), /URL-safe/);
});

test("database identity components are encoded instead of interpolated into SQL URLs", async () => {
  const {postgresUrl} = await import("../images/langfuse/nais-bootstrap.mjs");
  const env = {...credentials("LF_", "user@name"), LF_PGDATABASE:"db/name"};
  const url = new URL(postgresUrl(env, "LF_", () => "/path with spaces/identity.p12"));
  assert.equal(decodeURIComponent(url.username), "user@name");
  assert.equal(decodeURIComponent(url.pathname), "/db/name");
  assert.equal(url.searchParams.get("sslidentity"), "/path with spaces/identity.p12");
});

test("upstream migration TLS patch removes verification bypass and fails on unknown forms", async () => {
  const {secureMigrations} = await import("../images/langfuse/secure-migrations.mjs");
  assert.equal(secureMigrations("URL?x=1&secure=true&skip_verify=true"), "URL?x=1&secure=true");
  assert.equal(secureMigrations("URL?secure=true"), "URL?secure=true");
  assert.throws(() => secureMigrations("URL?skip_verify=true"), /Review upstream/);
});
