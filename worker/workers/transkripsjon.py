import multiprocessing
import os
import traceback
from pathlib import Path

from shared.services.jobs import JobStore
from worker.transkribering.batch import LokalBatchTranskriberer


def arbeider(
    jobbkø: multiprocessing.Queue,
    modell_id: str,
    klar_event: "multiprocessing.synchronize.Event | None" = None,
):
    """
    Queue worker for transcription jobs.

    This module owns process orchestration and job state. The model-heavy
    transcription implementation lives in transkribering.batch so it can later
    move behind a separate model-service boundary.
    """
    os.environ["HF_HUB_OFFLINE"] = "1"
    transkriberer = LokalBatchTranskriberer(modell_id)
    if klar_event is not None:
        klar_event.set()

    while True:
        melding = jobbkø.get()
        if melding is None:
            break

        jobb_id, lydfil_str, resultat_fil_str, n_talere = melding
        lydfil = Path(lydfil_str)
        resultat_fil = Path(resultat_fil_str)
        job_store = JobStore(resultat_fil.parent)

        job_store.write_transcribing(
            resultat_fil,
            model_id=transkriberer.modell_id,
            device=transkriberer.enhet,
        )

        try:
            resultat = transkriberer.transkriber(
                lydfil,
                n_talere=n_talere,
                status_callback=lambda values: job_store.update_path(resultat_fil, values),
            )
            job_store.write_done(
                resultat_fil,
                text=resultat.tekst,
                segments=[s.model_dump() for s in resultat.segmenter],
                warnings=resultat.advarsler,
            )
        except Exception as exc:
            print(f"[arbeider] FEIL i jobb {jobb_id}: {exc}", flush=True)
            traceback.print_exc()
            job_store.write_failed(resultat_fil, str(exc))
        finally:
            lydfil.unlink(missing_ok=True)
