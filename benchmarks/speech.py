"""Offline speech-model comparison; never changes application configuration."""

import argparse
import gc
import hashlib
import json
import os
import platform
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASR_MODEL = "nvidia/nemotron-3.5-asr-streaming-0.6b"
DIAR_MODEL = "nvidia/Nemotron-3-Diarization"


def normalize_for_scoring(text):
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in text).split())


def score_transcript(reference, hypothesis):
    from jiwer import process_words

    reference = normalize_for_scoring(reference)
    if not reference:
        raise ValueError("Cannot score an empty reference")
    result = process_words(reference, normalize_for_scoring(hypothesis))
    words = result.hits + result.substitutions + result.deletions
    return {"wer": result.wer, "deletion_rate": result.deletions / words,
            "reference_words": words, "hits": result.hits, "substitutions": result.substitutions,
            "deletions": result.deletions, "insertions": result.insertions}


def asr_samples(args):
    manifest = getattr(args, "manifest", None)
    if manifest is not None:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("dataset") != "Sprakbanken/nb_samtale" or data.get("split") != "test":
            raise ValueError("Only the reviewed NB Samtale test manifest is accepted")
        if data.get("reference_variant") != "orthographic" or not data.get("samples"):
            raise ValueError("Manifest needs orthographic references and audio samples")
        for sample in data["samples"]:
            path = Path(sample["audio_path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != sample["audio_sha256"]:
                raise ValueError(f"Audio checksum mismatch: {path}")
            if not normalize_for_scoring(sample["reference"]):
                raise ValueError(f"Empty reference: {sample['sample']}")
            yield sample
    else:
        for name in args.samples:
            yield {"sample": name, "audio_path": ROOT / "testdata/audio" / f"{name}.wav"}


def summarize_scores(results):
    groups = {}
    for item in results:
        if "accuracy" not in item:
            continue
        key = f"{item['backend']}/{item['orthography']}"
        group = groups.setdefault(key, {"clips": 0, "audio_seconds": 0, "inference_seconds": 0,
                                       "reference_words": 0, "substitutions": 0,
                                       "deletions": 0, "insertions": 0})
        group["clips"] += 1
        for field in ("audio_seconds", "inference_seconds"):
            group[field] += item[field]
        for field in ("reference_words", "substitutions", "deletions", "insertions"):
            group[field] += item["accuracy"][field]
    for group in groups.values():
        group["wer"] = sum(group[f] for f in ("substitutions", "deletions", "insertions")) / group["reference_words"]
        group["deletion_rate"] = group["deletions"] / group["reference_words"]
        group["real_time_factor"] = group["inference_seconds"] / group["audio_seconds"]
    return groups


def load_audio(path, seconds=None):
    import numpy as np

    command = ["ffmpeg", "-v", "error", "-i", str(path)]
    if seconds is not None:
        command += ["-t", str(seconds)]
    command += ["-ar", "16000", "-ac", "1", "-f", "f32le", "pipe:1"]
    audio = np.frombuffer(subprocess.run(command, check=True, capture_output=True).stdout,
                          dtype="<f4").copy()
    if not len(audio):
        raise ValueError(f"Empty audio: {path}")
    return audio


def sync(device):
    import torch

    if device.startswith("cuda"):
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_revision(path):
    parts = Path(path).parts
    if "snapshots" in parts:
        index = parts.index("snapshots") + 1
        if index < len(parts):
            return parts[index]
    return None


def model_revision(model_id, config):
    revision = getattr(config, "_commit_hash", None)
    if revision:
        return revision
    from huggingface_hub import try_to_load_from_cache

    # Recent Transformers releases no longer retain the commit on the config.
    if Path(model_id).is_dir():
        path = str(Path(model_id).absolute() / "config.json")
    else:
        path = try_to_load_from_cache(model_id, "config.json")
    return snapshot_revision(path) if isinstance(path, str) else None


def run_asr(backend, args, report):
    import torch
    from transformers import AutoModelForRNNT, AutoProcessor, pipeline

    samples = list(asr_samples(args))
    started = time.perf_counter()
    if backend == "whisper":
        from worker.transkribering.batch import _CHUNK_LENGDE_S, _STRIDE_LENGDE_S, _NUM_BEAMS

        model = pipeline("automatic-speech-recognition", model=args.whisper_model,
                         device=args.device, ignore_warning=True)
        processor = None
        config = model.model.config
    else:
        processor = AutoProcessor.from_pretrained(args.asr_model)
        model = AutoModelForRNNT.from_pretrained(args.asr_model).to(args.device).eval()
        config = model.config
    sync(args.device)
    load_seconds = time.perf_counter() - started
    for sample in samples:
        name = sample["sample"]
        audio_path = Path(sample["audio_path"])
        audio = load_audio(audio_path, args.seconds)
        duration = len(audio) / 16000
        sync(args.device)
        started = time.perf_counter()
        with torch.inference_mode():
            if backend == "whisper":
                result = model({"raw": audio, "sampling_rate": 16000},
                               chunk_length_s=_CHUNK_LENGDE_S, stride_length_s=_STRIDE_LENGDE_S,
                               return_timestamps="word",
                               generate_kwargs={"language": "no", "task": "transcribe",
                                                "num_beams": _NUM_BEAMS})
                text = result["text"].strip()
            else:
                inputs = processor(audio, sampling_rate=16000, language="nb-NO", return_tensors="pt")
                inputs = inputs.to(args.device, dtype=model.dtype)
                output = model.generate(**inputs, return_dict_in_generate=True)
                text = processor.decode(output.sequences, skip_special_tokens=True)
                if isinstance(text, list):
                    text = " ".join(text)
                text = text.strip()
        sync(args.device)
        elapsed = time.perf_counter() - started
        item = {"task": "asr", "backend": backend, "sample": name,
                "model": args.whisper_model if backend == "whisper" else args.asr_model,
                "revision": model_revision(args.whisper_model if backend == "whisper" else args.asr_model, config),
                "device": args.device, "audio_seconds": duration,
                "load_seconds": load_seconds, "inference_seconds": elapsed,
                "real_time_factor": elapsed / duration, "text": text,
                "scoring_note": "Accuracy is unscored; existing reference transcripts are not used."}
        if backend == "whisper":
            item["decoding"] = {"language": "no", "task": "transcribe", "num_beams": _NUM_BEAMS,
                                "chunk_length_s": _CHUNK_LENGDE_S, "stride_length_s": _STRIDE_LENGDE_S,
                                "timestamps": "word"}
        else:
            item["decoding"] = {"language": "nb-NO", "mode": "offline_full_clip",
                                "num_lookahead_tokens": getattr(processor, "default_num_lookahead_tokens", None)}
        if "reference" in sample:
            item.update({k: sample[k] for k in ("reference", "orthography", "dialect", "speaker_id",
                                               "source_file_id", "audio_sha256")})
            item["accuracy"] = score_transcript(sample["reference"], text)
            item["scoring_note"] = "Reviewed NB Samtale orthographic reference; case/punctuation normalized; no independent audio validation."
        report["results"].append(item)
        report["summary"] = summarize_scores(report["results"])
        write_report(args.output, report)
        accuracy = f"WER={item['accuracy']['wer']:.1%}" if "accuracy" in item else "accuracy unscored"
        print(f"{backend} {name}: {elapsed:.2f}s, RTF={elapsed / duration:.3f}; {accuracy}", flush=True)
    del model, processor
    gc.collect()
    if args.device.startswith("cuda"):
        torch.cuda.empty_cache()
    elif args.device == "mps":
        torch.mps.empty_cache()


def run_diarisation(backend, args, report):
    import torch

    audio = load_audio(ROOT / "testdata/tre_stemmer_test.wav", args.seconds)
    started = time.perf_counter()
    if backend == "ecapa":
        from worker.transkribering.diarisering import hent_voice_encoder, diariser

        hent_voice_encoder()
        device = "cpu"
        revision = None
        model_id = "speechbrain/spkrec-ecapa-voxceleb"
    else:
        from transformers import AutoProcessor, AutoModelForAudioFrameClassification

        processor = AutoProcessor.from_pretrained(args.diar_model)
        model = AutoModelForAudioFrameClassification.from_pretrained(args.diar_model)
        model = model.to(args.device).eval()
        device = args.device
        revision = model_revision(args.diar_model, model.config)
        model_id = args.diar_model
    sync(device)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    with torch.inference_mode():
        if backend == "ecapa":
            result, _ = diariser(audio)
            if not result:
                raise RuntimeError("ECAPA returned no segments; check its dependencies and model cache")
            segments = [{"start": s["start"], "end": s["slutt"], "speaker": s["taler"]} for s in result]
        else:
            inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
            inputs = inputs.to(device, dtype=model.dtype)
            output = model(**inputs)
            result = processor.extract_speaker_dict(output.logits, inputs.attention_mask)[0]
            segments = [{"start": float(s["Start"]), "end": float(s["End"]),
                         "speaker": str(s["Speaker"])} for s in result]
    sync(device)
    elapsed = time.perf_counter() - started
    duration = len(audio) / 16000
    report["results"].append({"task": "diarisation", "backend": backend, "model": model_id,
                              "revision": revision, "device": device,
                              "sample": "tre_stemmer_test", "audio_seconds": duration,
                              "load_seconds": load_seconds, "inference_seconds": elapsed,
                              "real_time_factor": elapsed / duration, "segments": segments,
                              "speaker_count": len({s["speaker"] for s in segments}),
                              "der": None, "scoring_note": "No trusted speaker timeline; DER is not scored."})
    write_report(args.output, report)
    print(f"{backend}: {elapsed:.2f}s, {len({s['speaker'] for s in segments})} speakers; DER unscored", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backends", nargs="+", choices=["whisper", "nemotron", "ecapa", "nemotron-diar"],
                        default=["whisper", "nemotron", "ecapa", "nemotron-diar"])
    parser.add_argument("--samples", nargs="+", choices=["test001", "test002"], default=["test001", "test002"])
    parser.add_argument("--manifest", type=Path, help="Reviewed NB Samtale test manifest; ASR only, full clips")
    parser.add_argument("--seconds", type=float, default=60, help="Clip limit; 0 means full recordings")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--whisper-model", default="NbAiLab/nb-whisper-large")
    parser.add_argument("--asr-model", default=ASR_MODEL)
    parser.add_argument("--diar-model", default=DIAR_MODEL)
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/benchmarks/speech.json")
    args = parser.parse_args()
    if args.seconds < 0 or args.threads < 1:
        parser.error("seconds must be non-negative and threads must be positive")
    args.seconds = args.seconds or None
    if args.manifest:
        if any(b not in {"whisper", "nemotron"} for b in args.backends):
            parser.error("--manifest supports ASR backends only; diarisation needs continuous audio")
        if args.seconds is not None:
            parser.error("Use --seconds 0 with --manifest to avoid scoring clipped audio against full references")
    import torch
    import transformers

    torch.set_num_threads(args.threads)
    os.environ.setdefault("ECAPA_CACHE", str(ROOT / ".cache/speechbrain"))
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "torch_version": torch.__version__, "transformers_version": transformers.__version__,
              "threads": args.threads, "results": [], "errors": [],
              "notes": ["Existing reference transcripts are not read or used. Accuracy is unscored.",
                        "No streaming latency benchmark; timings exclude model loading but include preprocessing.",
                        "Whisper uses current raw batch decoding settings, without app text corrections.",
                        "CPU and GPU timings must not be compared as model-only speed differences."]}
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report["dataset"] = {k: v for k, v in manifest.items() if k != "samples"}
        report["notes"][0] = "Only explicit NB Samtale references are scored; existing testdata/transcriptions are never read."
        report["notes"] += ["Pooled WER by written norm; numbers are not verbalized and dialect word forms are not equated.",
                            "Nynorsk is a separate written-norm challenge: Whisper keeps language=no; Nemotron is prompted for Bokmal (nb-NO).",
                            "Short non-overlapping clips are not a continuous conversation or streaming benchmark.",
                            "The development Transformers Nemotron implementation has not been cross-validated against native NeMo.",
                            "Training-data overlap is unverified; this is a diagnostic comparison, not an unbiased model ranking."]
    for backend in args.backends:
        print(f"Starting {backend}...", flush=True)
        try:
            if backend in {"whisper", "nemotron"}:
                run_asr(backend, args, report)
            else:
                run_diarisation(backend, args, report)
        except Exception as exc:
            report["errors"].append({"backend": backend, "error": str(exc)})
            print(f"{backend} failed: {exc}", flush=True)
        finally:
            write_report(args.output, report)
            gc.collect()
    print(f"Report: {args.output}", flush=True)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
