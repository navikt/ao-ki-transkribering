"""Prepare a reproducible, cached podcast subset of the official NB Samtale test split."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

from benchmarks.speech import ROOT, write_report

REPO = "Sprakbanken/nb_samtale"
SOURCE = "https://huggingface.co/datasets/Sprakbanken/nb_samtale"


def curl(url, output=None):
    command = ["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
               "--connect-timeout", "15", "--max-time", "600", url]
    if output:
        command += ["--output", str(output)]
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Download failed: {url}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def select_samples(rows, counts):
    selected = []
    for language, count in counts.items():
        candidates = [r for r in rows if r["source_type"] == "podcast"
                      and r["orthography"] == language and 5 <= r["duration"] <= 25
                      and not r["overlap_previous"] and not r["overlap_next"]
                      and r["orthographic"].strip() and "(" not in r["annotations"]]
        speakers, dialects, sources = Counter(), Counter(), Counter()
        for _ in range(count):
            if not candidates:
                raise ValueError(f"Not enough eligible {language} podcast clips for {count} samples")
            candidates.sort(key=lambda r: (dialects[r["dialect"]], speakers[r["speaker_id"]],
                                          sources[r["source_file_id"]], r["file_name"]))
            row = candidates.pop(0)
            selected.append(row)
            speakers[row["speaker_id"]] += 1
            dialects[row["dialect"]] += 1
            sources[row["source_file_id"]] += 1
    return selected


def verify_archive(path, expected):
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum mismatch: {path}; remove the cached file and retry")


def extract_selected(archive, selected, destination):
    wanted = {r["file_name"] for r in selected}
    found = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name not in wanted:
                continue
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe archive entry: {member.name}")
            target = destination / path.name
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"Missing audio: {member.name}")
            with source, target.open("wb") as output:
                output.write(source.read())
            found.add(member.name)
    if found != wanted:
        raise ValueError(f"Archive missing selected clips: {sorted(wanted - found)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/nb-samtale")
    parser.add_argument("--bokmal", type=int, default=24)
    parser.add_argument("--nynorsk", type=int, default=6)
    args = parser.parse_args()
    if min(args.bokmal, args.nynorsk) < 0 or args.bokmal + args.nynorsk == 0:
        parser.error("Sample counts must be non-negative, with at least one sample")
    args.cache.mkdir(parents=True, exist_ok=True)
    info = json.loads(curl(f"https://huggingface.co/api/datasets/{REPO}"))
    revision = info["sha"]
    base = f"{SOURCE}/resolve/{revision}/data"
    metadata_path = args.cache / f"test_metadata-{revision}.jsonl"
    if not metadata_path.exists():
        curl(f"{base}/test_metadata.jsonl", metadata_path)
    rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
    selected = select_samples(rows, {"bm": args.bokmal, "nn": args.nynorsk})
    tree = json.loads(curl(f"https://huggingface.co/api/datasets/{REPO}/tree/{revision}/data"))
    audio_dir = args.cache / "audio"
    audio_dir.mkdir(exist_ok=True)
    for language in sorted({r["orthography"] for r in selected}):
        filename = f"test_{language}_1.tar.gz"
        metadata = next(f for f in tree if f["path"] == f"data/{filename}")
        archive = args.cache / f"{revision}-{filename}"
        if not archive.exists():
            print(f"Downloading {filename} ({metadata['size'] / 1e6:.1f} MB)...", flush=True)
            partial = archive.with_suffix(".partial")
            curl(f"{base}/{filename}", partial)
            verify_archive(partial, metadata["lfs"]["oid"])
            partial.replace(archive)
        verify_archive(archive, metadata["lfs"]["oid"])
        extract_selected(archive, [r for r in selected if r["orthography"] == language], audio_dir)
    samples = []
    for row in selected:
        audio = audio_dir / PurePosixPath(row["file_name"]).name
        samples.append({**row, "sample": audio.stem, "audio_path": str(audio.resolve()),
                        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                        "reference": row["orthographic"]})
    manifest = args.cache / "podcast-test.json"
    write_report(manifest, {"dataset": REPO, "source": SOURCE, "revision": revision,
                           "split": "test", "license": "CC0-1.0", "reference_variant": "orthographic",
                           "reference_review": "Corpus human correction and second-person review; not independently audio-validated here.",
                           "samples": samples})
    print(f"Prepared {len(samples)} clips, {sum(r['duration'] for r in samples):.1f}s: {manifest}")


if __name__ == "__main__":
    main()
