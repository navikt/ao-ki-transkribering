from shared.services.jobs import JobStore


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
