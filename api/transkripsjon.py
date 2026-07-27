import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from runtime import jobbkø
from settings import ARBEIDSMAPPE, MODELL_ID
from transkribering.batch import estimert_total_s

router = APIRouter()


@router.post("/transkriber")
async def start_transkribering(
    lydfil: UploadFile = File(...),
    n_talere: int = Form(0),
):
    """Mottar lydfil, sender til arbeiderprosess, returnerer jobb-ID.

    n_talere: 0 = auto-deteksjon, 2/3/4 = eksakt antall talere.
    """
    suffix = Path(lydfil.filename or "opptak.webm").suffix or ".webm"
    jobb_id = str(uuid.uuid4())

    lydfil_sti = ARBEIDSMAPPE / f"{jobb_id}{suffix}"
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"

    with lydfil_sti.open("wb") as f:
        shutil.copyfileobj(lydfil.file, f)

    resultat_sti.write_text(json.dumps({"status": "venter"}))
    jobbkø.put((jobb_id, str(lydfil_sti), str(resultat_sti), n_talere))

    return {"jobb_id": jobb_id}


@router.get("/status/{jobb_id}")
async def sjekk_status(jobb_id: str):
    """Returnerer status, fremdrift og elapsed tid for en transkriberingsjobb."""
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"
    if not resultat_sti.exists():
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = json.loads(resultat_sti.read_text())

    svar: dict = {"jobb_id": jobb_id, "status": data["status"]}

    if data["status"] == "transkriberer":
        start_tid = data.get("start_tid")
        lyd_s = data.get("lyd_varighet_s")
        modell_id = data.get("modell_id", MODELL_ID)
        enhet = data.get("enhet", "cpu")
        fase = data.get("fase", "transkriberer")

        if start_tid:
            elapsed = time.time() - start_tid
            svar["elapsed_s"] = round(elapsed, 1)
            svar["fase"] = fase

            if lyd_s:
                estimert = estimert_total_s(modell_id, lyd_s, enhet)
                svar["estimert_total_s"] = round(estimert, 1)
                svar["lyd_varighet_s"] = round(lyd_s, 1)
                if fase == "diariserer":
                    fremdrift = 0.85 + 0.10 * min(elapsed / estimert, 1.0)
                else:
                    fremdrift = min(elapsed / estimert * 0.85, 0.84)
                svar["fremdrift"] = round(fremdrift, 3)

    return svar


@router.get("/resultat/{jobb_id}")
async def hent_resultat(jobb_id: str):
    """Returnerer ferdig transkripsjon."""
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"
    if not resultat_sti.exists():
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = json.loads(resultat_sti.read_text())
    if data["status"] == "feil":
        raise HTTPException(status_code=500, detail=data.get("feilmelding", "Ukjent feil"))
    if data["status"] != "ferdig":
        raise HTTPException(status_code=409, detail=f"Jobb ikke ferdig (status: {data['status']})")
    return {"jobb_id": jobb_id, "tekst": data["tekst"], "segmenter": data["segmenter"]}
