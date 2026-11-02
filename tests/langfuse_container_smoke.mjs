// Run inside an adapter image; no database, credentials or network service required.
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {run} from "/app/nais-bootstrap.mjs";

const component = process.argv[2] || "web";
assert.ok(["web", "worker"].includes(component));
const directory = mkdtempSync(join(tmpdir(), "langfuse-synthetic-"));
const cert = join(directory, "client.crt"), key = join(directory, "client.key");
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=synthetic.invalid", "-keyout", key, "-out", cert], {stdio: "ignore"});
for (const prefix of ["LF_", "LF_READWRITE_"]) {
  for (const [name, value] of Object.entries({PGHOST:"synthetic.invalid", PGPORT:"5432",
    PGDATABASE:"langfuse", PGUSER:prefix === "LF_" ? "migration" : "runtime",
    PGSSLMODE:"verify-full", PGSSLCERT:cert, PGSSLKEY:key, PGSSLROOTCERT:cert})) {
    process.env[prefix + name] = value;
  }
}
Object.assign(process.env, {
  CLICKHOUSE_USER:"langfuse", CLICKHOUSE_PASSWORD:"synthetic_hex_1234",
  CLICKHOUSE_URL:"https://synthetic.invalid:8443", CLICKHOUSE_MIGRATION_URL:"clickhouse://synthetic.invalid:9440",
  VALKEY_HOST_LANGFUSE:"synthetic.invalid", VALKEY_PORT_LANGFUSE:"6379",
  VALKEY_USERNAME_LANGFUSE:"synthetic", VALKEY_PASSWORD_LANGFUSE:"synthetic",
  LANGFUSE_AUTO_POSTGRES_MIGRATION_DISABLED:"true", LANGFUSE_AUTO_CLICKHOUSE_MIGRATION_DISABLED:"true",
});
run(component, ["node", "-e", `
  const assert = require('node:assert/strict'), fs = require('node:fs'), cp = require('node:child_process');
  const url = new URL(process.env.DATABASE_URL);
  assert.equal(url.username, 'runtime');
  assert.equal(url.searchParams.get('sslaccept'), 'strict');
  const identity = url.searchParams.get('sslidentity');
  assert.equal(fs.statSync(identity).mode & 0o777, 0o600);
  cp.execFileSync('openssl', ['pkcs12','-in',identity,'-passin','pass:','-info','-noout'], {stdio:'ignore'});
  assert.equal(process.env.REDIS_TLS_ENABLED, 'true');
  assert.equal(process.env.REDIS_TLS_REJECT_UNAUTHORIZED, 'true');
  console.log('Vendor entrypoint, private PKCS12 identity and strict TLS environment verified (no network calls).');
`]);
