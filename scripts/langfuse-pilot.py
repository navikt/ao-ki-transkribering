#!/usr/bin/env python3
"""Manage a localhost-only pilot; never print or overwrite generated credentials."""

import argparse
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "observability" / "langfuse"


def initialize(path):
    if path.is_symlink():
        raise RuntimeError("Pilot credentials must not be a symbolic link.")
    if path.exists():
        if not path.is_file():
            raise RuntimeError("Pilot credentials must be a regular file.")
        if path.stat().st_mode & 0o077:
            raise RuntimeError("Pilot credentials must have mode 0600; restrict permissions before continuing.")
        return False
    values = {
        "LANGFUSE_ENABLED": "false",
        "LANGFUSE_BASE_URL": "http://localhost:3000",
        "LANGFUSE_ENVIRONMENT": "local",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-" + secrets.token_hex(16),
        "LANGFUSE_SECRET_KEY": "sk-lf-" + secrets.token_hex(32),
        "LANGFUSE_PILOT_EMAIL": "pilot@localhost.local",
    }
    for name in ("PILOT_PASSWORD", "POSTGRES_PASSWORD", "CLICKHOUSE_PASSWORD", "REDIS_PASSWORD",
                 "STORAGE_PASSWORD", "SALT", "ENCRYPTION_KEY", "SESSION_SECRET"):
        values["LANGFUSE_" + name] = secrets.token_hex(32)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
        output.write("# Local credentials. Do not commit, share, or regenerate while keeping existing volumes.\n")
        output.writelines(f"{key}={value}\n" for key, value in values.items())
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "up", "down", "status", "validate"))
    args = parser.parse_args()
    path = DIRECTORY / ".env"
    if not path.exists() and args.command != "init":
        if args.command != "up":
            raise SystemExit("Pilot credentials are missing. Restore them, or use init for a new pilot.")
        try:
            volumes = subprocess.run(
                ["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=transkriberit-langfuse",
                 "--format", "{{.Name}}"], capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            raise SystemExit("Docker is required. Install/start your container runtime and retry.")
        if volumes.returncode:
            raise SystemExit("Start Docker/OrbStack before creating a new pilot.")
        if volumes.stdout.strip():
            raise SystemExit("Existing pilot volumes found without credentials. Restore the original .env; do not regenerate it.")
    initialize(path)
    if args.command == "init":
        print(f"Pilot credentials ready in {path} (mode 0600).")
        return
    command = ["docker", "compose", "--env-file", str(path), "-f", str(DIRECTORY / "compose.yaml")]
    commands = {"up": ["up", "-d", "--wait", "--wait-timeout", "300"],
                "down": ["down"], "status": ["ps"], "validate": ["config", "--quiet"]}
    try:
        result = subprocess.run(command + commands[args.command], check=False)
    except FileNotFoundError:
        raise SystemExit("Docker Compose is required. Install/start your container runtime and retry.")
    if result.returncode:
        raise SystemExit(result.returncode)
    if args.command == "up":
        print("Langfuse: http://localhost:3000. Login details are in the local pilot .env file.")
        print("Backend tracing remains disabled until LANGFUSE_ENABLED=true is explicitly set.")
    elif args.command == "down":
        print("Pilot stopped. Named volumes and credentials are retained.")


if __name__ == "__main__":
    main()
