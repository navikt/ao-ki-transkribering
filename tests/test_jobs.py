from shared.services.jobs import JobStore
import os


def test_job_store_writes_and_reads_state(tmp_path):
    store = JobStore(tmp_path)
    paths = store.create_paths(".wav")

    store.write_queued(paths.result_path)
    assert store.exists(paths.job_id)
    assert store.read(paths.job_id) == {"status": "venter"}

    store.write_transcribing(paths.result_path, model_id="modell", device="cpu")
    store.update_path(paths.result_path, {"fase": "transkriberer", "lyd_varighet_s": 12.5})

    data = store.read(paths.job_id)
    assert data["status"] == "transkriberer"
    assert data["fase"] == "transkriberer"
    assert data["modell_id"] == "modell"
    assert data["enhet"] == "cpu"
    assert data["lyd_varighet_s"] == 12.5

    store.write_done(paths.result_path, text="hei", segments=[{"tekst": "hei"}])
    assert store.read(paths.job_id) == {
        "status": "ferdig",
        "tekst": "hei",
        "segmenter": [{"tekst": "hei"}],
    }

    store.write_done(paths.result_path, text="hei", segments=[], warnings=["diarisering feilet"])
    assert store.read(paths.job_id) == {
        "status": "ferdig",
        "tekst": "hei",
        "segmenter": [],
        "advarsler": ["diarisering feilet"],
    }


def test_job_store_writes_failure(tmp_path):
    store = JobStore(tmp_path)
    paths = store.create_paths(".wav")

    store.write_failed(paths.result_path, "boom")

    assert store.read(paths.job_id) == {"status": "feil", "feilmelding": "boom"}


def test_job_store_rejects_invalid_job_id(tmp_path):
    store = JobStore(tmp_path)

    assert not store.exists("../annen-fil")


def test_retention_expires_only_completed_jobs(tmp_path):
    store = JobStore(tmp_path)
    old = store.create_paths(".wav")
    active = store.create_paths(".wav")
    recent = store.create_paths(".wav")
    store.write_done(old.result_path, text="sensitive", segments=[])
    store.write_queued(active.result_path)
    store.write_failed(recent.result_path, "failed")
    for path in (old.result_path, active.result_path):
        os.utime(path, (100, 100))
    os.utime(recent.result_path, (950, 950))
    assert store.cleanup_completed(100, now=1000) == 1
    assert not old.result_path.exists()
    assert active.result_path.exists()
    assert recent.result_path.exists()
    assert recent.result_path.stat().st_mode & 0o777 == 0o600
