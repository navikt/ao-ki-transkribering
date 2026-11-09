import {chmodSync, mkdtempSync, rmSync, readFileSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawn, spawnSync} from "node:child_process";
import {pathToFileURL} from "node:url";

function required(env, key) {
  const value = env[key];
  if (!value || value.includes("\0")) throw new Error("Missing or invalid NAIS connection settings");
  return value;
}

export function postgresUrl(env, prefix, makeIdentity) {
  if (required(env, prefix + "PGSSLMODE") !== "verify-full") {
    throw new Error("Postgres requires verified TLS");
  }
  const host = required(env, prefix + "PGHOST");
  const port = required(env, prefix + "PGPORT");
  if (!/^[a-zA-Z0-9.-]+$/.test(host) || !/^\d+$/.test(port) || Number(port) > 65535 || Number(port) < 1) {
    throw new Error("Invalid Postgres endpoint");
  }
  const user = encodeURIComponent(required(env, prefix + "PGUSER"));
  const database = encodeURIComponent(required(env, prefix + "PGDATABASE"));
  const identity = makeIdentity(required(env, prefix + "PGSSLCERT"), required(env, prefix + "PGSSLKEY"));
  const query = new URLSearchParams({sslmode: "require", sslaccept: "strict",
    sslcert: required(env, prefix + "PGSSLROOTCERT"), sslidentity: identity, sslpassword: ""});
  return `postgresql://${user}@${host}:${port}/${database}?${query}`;
}

export function configureEnvironment(env, component, makeIdentity) {
  if (!["web", "worker"].includes(component)) throw new Error("Unknown component");
  if (env.NODE_TLS_REJECT_UNAUTHORIZED === "0") throw new Error("TLS verification must remain enabled");
  for (const key of ["CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"]) {
    if (!/^[A-Za-z0-9_-]+$/.test(required(env, key))) {
      throw new Error("ClickHouse migration credentials must be URL-safe");
    }
  }
  const configured = {...env};
  // CNPG's admin credentials are used only by the web migration entrypoint.
  configured.DATABASE_URL = postgresUrl(env, "LF_READWRITE_", makeIdentity);
  configured.DIRECT_URL = component === "web" ? postgresUrl(env, "LF_", makeIdentity) : configured.DATABASE_URL;
  configured.REDIS_HOST = required(env, "VALKEY_HOST_LANGFUSE");
  configured.REDIS_PORT = required(env, "VALKEY_PORT_LANGFUSE");
  configured.REDIS_USERNAME = required(env, "VALKEY_USERNAME_LANGFUSE");
  configured.REDIS_AUTH = required(env, "VALKEY_PASSWORD_LANGFUSE");
  configured.REDIS_TLS_ENABLED = "true";
  configured.REDIS_TLS_REJECT_UNAUTHORIZED = "true";
  configured.REDIS_TLS_SERVERNAME = configured.REDIS_HOST;
  delete configured.REDIS_CONNECTION_STRING;
  return configured;
}

export function configureTrust(env, directory, read = path => readFileSync(path, "utf8"),
  write = (path, data) => writeFileSync(path, data, {mode: 0o600})) {
  if (!env.LANGFUSE_CLICKHOUSE_CA_FILE) return env;
  const platform = "/etc/ssl/certs/ca-certificates.crt";
  const bundle = join(directory, "ca-bundle.pem");
  write(bundle, read(platform) + "\n" + read(env.LANGFUSE_CLICKHOUSE_CA_FILE));
  return {...env, NODE_EXTRA_CA_CERTS: bundle, SSL_CERT_FILE: bundle, REDIS_TLS_CA_PATH: platform};
}

export function run(component, args) {
  if (!args.length) throw new Error("Missing upstream startup command");
  const directory = mkdtempSync(join(process.env.LANGFUSE_IDENTITY_DIR || tmpdir(), "langfuse-tls-"));
  chmodSync(directory, 0o700);
  let count = 0;
  const cleanup = () => rmSync(directory, {recursive: true, force: true});
  let env;
  try {
    env = configureEnvironment(process.env, component, (cert, key) => {
      const file = join(directory, `identity-${count++}.p12`);
      const result = spawnSync("openssl", ["pkcs12", "-export", "-in", cert, "-inkey", key,
        "-out", file, "-passout", "pass:"], {stdio: "ignore"});
      if (result.error || result.status !== 0) throw new Error("Could not prepare Postgres TLS identity");
      chmodSync(file, 0o600);
      return file;
    });
    env = configureTrust(env, directory);
  } catch (error) { cleanup(); throw error; }
  const child = spawn(`/app/${component}/entrypoint.sh`, args, {env, stdio: "inherit"});
  const terminate = () => child.kill("SIGTERM");
  const interrupt = () => child.kill("SIGINT");
  process.on("SIGTERM", terminate);
  process.on("SIGINT", interrupt);
  child.once("error", () => {
    cleanup();
    console.error("Langfuse vendor entrypoint could not start");
    process.exitCode = 1;
  });
  child.once("close", (code, signal) => {
    cleanup();
    process.removeListener("SIGTERM", terminate);
    process.removeListener("SIGINT", interrupt);
    process.exitCode = code ?? (signal === "SIGTERM" ? 143 : 1);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { run(process.argv[2], process.argv.slice(3)); }
  catch { console.error("Langfuse NAIS bootstrap failed; check connection settings and mounted certificates"); process.exitCode = 1; }
}
