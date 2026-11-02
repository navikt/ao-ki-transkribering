import {readFileSync, writeFileSync} from "node:fs";
import {pathToFileURL} from "node:url";

export function secureMigrations(source) {
  const secured = source.replaceAll("&secure=true&skip_verify=true", "&secure=true");
  if (secured.includes("skip_verify=true")) throw new Error("Review upstream migration TLS configuration");
  return secured;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const path = process.argv[2];
  writeFileSync(path, secureMigrations(readFileSync(path, "utf8")));
}
