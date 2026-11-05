#!/usr/bin/env python3
"""Stream pinned model weights into GCS without a local weights copy."""

import argparse
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path


def stage_model(spec):
    base = f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}"
    prefix = spec.get("prefix", "borealis-12b-gguf")
    destination = f"gs://{spec['bucket']}/{prefix}/{spec['revision']}"
    command = ["gcloud", "storage", "cp", "-", f"{destination}/{spec['filename']}",
               "--if-generation-match=0"]
    digest, size, reported = hashlib.sha256(), 0, 0
    with urllib.request.urlopen(f"{base}/{spec['filename']}", timeout=120) as source:
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                process.stdin.write(chunk)
                if size - reported >= 1024 ** 3:
                    print(f"Staged {size / 1024 ** 3:.1f} GiB", flush=True)
                    reported = size
            if size != spec["size"] or digest.hexdigest() != spec["sha256"]:
                raise RuntimeError("Publisher size/checksum mismatch; refusing to finalize upload")
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError("GCS upload failed")
        except BaseException:
            process.kill()
            process.wait()
            raise
    for name in spec.get("metadata_files", ["LICENSE", "README.md"]):
        with urllib.request.urlopen(f"{base}/{name}", timeout=60) as source:
            subprocess.run(["gcloud", "storage", "cp", "-", f"{destination}/{name}"],
                           input=source.read(), check=True)
    subprocess.run(["gcloud", "storage", "cp", "-", f"{destination}/provenance.json"],
                   input=json.dumps(spec, indent=2).encode(), check=True)
    print(f"Verified SHA-256 {digest.hexdigest()}; staged at {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path,
                        default=Path(__file__).resolve().parents[1] / "k8s/borealis-q8.json")
    stage_model(json.loads(parser.parse_args().spec.read_text()))
