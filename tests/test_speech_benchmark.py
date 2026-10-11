import contextlib
import json
import hashlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks import speech
from benchmarks import nb_samtale


def test_snapshot_revision_uses_snapshot_path_not_blob_symlink():
    assert speech.snapshot_revision("/cache/model/snapshots/abcdef/config.json") == "abcdef"
    assert speech.snapshot_revision("/models/whisper/config.json") is None


def test_audio_clipping_uses_requested_duration(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=np.zeros(16000, dtype="<f4").tobytes())

    monkeypatch.setattr(speech.subprocess, "run", run)
    assert len(speech.load_audio("audio.wav", 60)) == 16000
    assert calls[0][calls[0].index("-t") + 1] == "60"
    speech.load_audio("audio.wav")
    assert "-t" not in calls[1]


def test_empty_audio_is_rejected(monkeypatch):
    monkeypatch.setattr(speech.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=b""))
    with pytest.raises(ValueError, match="Empty audio"):
        speech.load_audio("audio.wav")


@pytest.mark.parametrize("with_manifest", [False, True])
def test_asr_uses_only_explicit_corpus_references(monkeypatch, tmp_path, with_manifest):
    if with_manifest:
        pytest.importorskip("jiwer", reason="Optional speech benchmark dependency")
    monkeypatch.setattr(speech, "ROOT", tmp_path)
    monkeypatch.setattr(speech, "sync", lambda device: None)
    monkeypatch.setattr(speech, "model_revision", lambda *args: "test-revision")
    audio_calls = []

    def load_audio(path, seconds):
        audio_calls.append((path, seconds))
        return np.zeros(16000, dtype="<f4")

    monkeypatch.setattr(speech, "load_audio", load_audio)

    class Processor:
        def __call__(self, *args, **kwargs):
            return SimpleNamespace(to=lambda *args, **kwargs: {})

        def decode(self, *args, **kwargs):
            return "Generated text"

    class Model:
        config = SimpleNamespace()
        dtype = "float32"

        def to(self, device):
            return self

        def eval(self):
            return self

        def generate(self, **kwargs):
            return SimpleNamespace(sequences=[1])

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=contextlib.nullcontext))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoModelForRNNT=SimpleNamespace(from_pretrained=lambda *args: Model()),
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *args: Processor()), pipeline=None,
    ))
    args = SimpleNamespace(asr_model="test-model", device="cpu", samples=["test002"],
                           seconds=60, output=tmp_path / "result.json")
    audio_path = tmp_path / "testdata/audio/test002.wav"
    if with_manifest:
        audio_path.parent.mkdir(parents=True)
        audio_path.write_bytes(b"audio")
        sample = {"sample": "test002", "audio_path": str(audio_path),
                  "reference": "Generated text", "orthography": "bm", "dialect": "e",
                  "speaker_id": "P1", "source_file_id": "50f-1",
                  "audio_sha256": hashlib.sha256(b"audio").hexdigest()}
        args.manifest = tmp_path / "manifest.json"
        args.manifest.write_text(json.dumps({"dataset": "Sprakbanken/nb_samtale", "split": "test",
                                             "reference_variant": "orthographic", "samples": [sample]}))
        args.seconds = None
    report = {"results": []}
    speech.run_asr("nemotron", args, report)
    assert audio_calls == [(audio_path, args.seconds)]
    result = json.loads(args.output.read_text())["results"][0]
    assert result["text"] == "Generated text"
    assert "agreement" not in result
    if with_manifest:
        assert result["reference"] == "Generated text"
        assert result["accuracy"]["wer"] == 0
        assert report["summary"]["nemotron/bm"]["clips"] == 1
    else:
        assert "reference" not in result
        assert "unscored" in result["scoring_note"]


def test_scoring_normalization_preserves_norwegian_letters_and_words():
    assert speech.normalize_for_scoring(" JA, bl\u00e5b\u00e6r! 42? ") == "ja bl\u00e5b\u00e6r 42"
    assert speech.normalize_for_scoring("\u00c5") == speech.normalize_for_scoring("A\u030a")
    assert speech.normalize_for_scoring("eh eh ja") == "eh eh ja"


def test_scoring_counts_missing_words_and_insertions():
    pytest.importorskip("jiwer", reason="Optional speech benchmark dependency")
    score = speech.score_transcript("en to tre fire", "en tre fire fem")
    assert score["deletions"] == 1
    assert score["insertions"] == 1
    assert score["reference_words"] == 4
    assert score["wer"] == .5
    assert score["deletion_rate"] == .25
    assert speech.score_transcript("en to", "")["wer"] == 1
    with pytest.raises(ValueError, match="empty reference"):
        speech.score_transcript("...", "en")


def test_summary_pools_word_counts_and_separates_written_norms():
    pytest.importorskip("jiwer", reason="Optional speech benchmark dependency")
    def item(language, reference, text):
        return {"backend": "whisper", "orthography": language,
                "audio_seconds": 10, "inference_seconds": 5,
                "accuracy": speech.score_transcript(reference, text)}

    summary = speech.summarize_scores([item("bm", "en", ""), item("bm", "en to tre", "en to tre"),
                                       item("nn", "ein", "ein")])
    assert summary["whisper/bm"]["wer"] == .25
    assert summary["whisper/bm"]["clips"] == 2
    assert summary["whisper/nn"]["wer"] == 0


def test_manifest_requires_reviewed_test_source_and_matching_audio(tmp_path):
    manifest = tmp_path / "manifest.json"
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    sample = {"sample": "clip", "audio_path": str(audio), "reference": "en to",
              "audio_sha256": hashlib.sha256(b"audio").hexdigest()}
    data = {"dataset": "Sprakbanken/nb_samtale", "split": "test",
            "reference_variant": "orthographic", "samples": [sample]}
    manifest.write_text(json.dumps(data))
    assert list(speech.asr_samples(SimpleNamespace(manifest=manifest))) == [sample]
    audio.write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum mismatch"):
        list(speech.asr_samples(SimpleNamespace(manifest=manifest)))
    data["dataset"] = "old-reference-files"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Only the reviewed"):
        list(speech.asr_samples(SimpleNamespace(manifest=manifest)))


def test_subset_selection_excludes_overlap_and_balances_dialects():
    def row(name, dialect, overlap=False):
        return {"file_name": name, "dialect": dialect, "speaker_id": name,
                "source_file_id": name, "source_type": "podcast", "orthography": "bm",
                "duration": 10, "overlap_previous": overlap, "overlap_next": False,
                "orthographic": "en to", "annotations": "en to"}

    rows = [row("a", "e"), row("b", "e"), row("c", "n"), row("d", "t", True)]
    assert [r["file_name"] for r in nb_samtale.select_samples(rows, {"bm": 2})] == ["a", "c"]
    assert nb_samtale.select_samples(rows[::-1], {"bm": 2}) == nb_samtale.select_samples(rows, {"bm": 2})
    with pytest.raises(ValueError, match="Not enough"):
        nb_samtale.select_samples(rows, {"nn": 1})


def test_archive_extracts_only_selected_regular_files(tmp_path):
    import io
    import tarfile

    archive = tmp_path / "audio.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("data/test/bm/clip.wav", "../unrelated.wav"):
            entry = tarfile.TarInfo(name)
            entry.size = 5
            tar.addfile(entry, io.BytesIO(b"audio"))
    destination = tmp_path / "output"
    destination.mkdir()
    nb_samtale.extract_selected(archive, [{"file_name": "data/test/bm/clip.wav"}], destination)
    assert (destination / "clip.wav").read_bytes() == b"audio"
    with pytest.raises(ValueError, match="Unsafe"):
        nb_samtale.extract_selected(archive, [{"file_name": "../unrelated.wav"}], destination)
