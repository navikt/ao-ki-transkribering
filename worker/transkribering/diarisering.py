import importlib.util
import logging
import os
import threading

import numpy as np

from worker.transkribering.konstanter import SAMPLE_RATE

log = logging.getLogger(__name__)

# ECAPA-TDNN fra SpeechBrain — ~80 MB, ingen HF-token nødvendig.
# Sett ECAPA_CACHE til en persistent mappe for å unngå re-nedlasting.
_ECAPA_KILDE = os.getenv("ECAPA_MODELL", "speechbrain/spkrec-ecapa-voxceleb")
_ECAPA_CACHE = os.getenv("ECAPA_CACHE", "/app/.cache/speechbrain")

_SPEECHBRAIN_TILGJENGELIG = importlib.util.find_spec("speechbrain") is not None
if not _SPEECHBRAIN_TILGJENGELIG:
    log.warning(
        "speechbrain er ikke installert — taler-diarisering er deaktivert. "
        "Installer worker-avhengigheter (requirements/worker-cpu.txt) for å aktivere."
    )

_VINDU_S = 1.5   # sekunder per embedding-vindu
_RATE    = 3.0   # embeddings per sekund (hop = 1/rate)

_encoder = None
_encoder_lock = threading.Lock()


def hent_voice_encoder():
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                from speechbrain.inference.speaker import EncoderClassifier
                # Temporarily allow HF download even when HF_HUB_OFFLINE=1 so
                # the model can be fetched on first use and cached to _ECAPA_CACHE.
                _prev = os.environ.pop("HF_HUB_OFFLINE", None)
                try:
                    _encoder = EncoderClassifier.from_hparams(
                        source=_ECAPA_KILDE,
                        savedir=_ECAPA_CACHE,
                        run_opts={"device": "cpu"},
                    )
                finally:
                    if _prev is not None:
                        os.environ["HF_HUB_OFFLINE"] = _prev
    return _encoder


def _embed_med_vindu(wav: np.ndarray) -> "tuple[np.ndarray, list[slice]]":
    """Generer ECAPA-TDNN speaker-embeddings med glidende vindu over wav."""
    import torch
    encoder = hent_voice_encoder()

    vindu   = int(_VINDU_S * SAMPLE_RATE)
    hop     = max(1, int(SAMPLE_RATE / _RATE))
    min_len = int(SAMPLE_RATE * 0.5)

    embeds: list[np.ndarray] = []
    slices: list[slice]      = []

    for start in range(0, max(1, len(wav) - vindu + 1), hop):
        end   = min(start + vindu, len(wav))
        chunk = wav[start:end]
        if len(chunk) < min_len:
            continue
        tensor = torch.tensor(chunk, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            embed = encoder.encode_batch(tensor)
        embeds.append(embed.squeeze().cpu().numpy())
        slices.append(slice(start, end))

    if not embeds:
        return np.zeros((0, 192), dtype=np.float32), []
    return np.array(embeds, dtype=np.float32), slices


def auto_n_talere(embeds: np.ndarray, maks: int = 4) -> int:
    """Finner optimalt antall talere via silhouette score (2..maks)."""
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    beste_n     = 2
    beste_score = -1.0
    for n in range(2, maks + 1):
        if len(embeds) < n * 2:
            break
        labels = AgglomerativeClustering(n_clusters=n, linkage="ward").fit_predict(embeds)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(embeds, labels, metric="cosine")
        print(f"[diarisering] n={n} silhouette={score:.3f}", flush=True)
        if score > beste_score:
            beste_score = score
            beste_n     = n
    print(f"[diarisering] Auto-valgt n_talere={beste_n}", flush=True)
    return beste_n


def diariser(
    wav: np.ndarray,
    n_talere: int = 0,
    prototyper: "np.ndarray | None" = None,
) -> "tuple[list[dict], np.ndarray | None]":
    """
    Kjører speaker diarization på float32 PCM (16 kHz, mono).

    Args:
        wav:        float32 numpy-array, 16 kHz
        n_talere:   antall forventede talere. 0 = auto-deteksjon.
        prototyper: shape (n_talere, 192) – kjente talere fra tidligere chunks.

    Returns:
        (diari_segs, prototyper)
        diari_segs: [{start, slutt, taler}]
        prototyper: oppdaterte prototyper for neste kall
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics.pairwise import cosine_similarity

    if len(wav) < SAMPLE_RATE * 1.0:
        return [], prototyper

    if not _SPEECHBRAIN_TILGJENGELIG:
        return [], prototyper

    try:
        partial_embeds, partial_slices = _embed_med_vindu(wav)
    except Exception as exc:
        print(f"[diarisering] Embed feil: {exc}", flush=True)
        return [], prototyper

    if len(partial_embeds) == 0:
        return [], prototyper

    if n_talere == 0:
        if prototyper is not None:
            n_talere = len(prototyper)
        else:
            n_talere = auto_n_talere(partial_embeds)

    if len(partial_embeds) < n_talere:
        labels = np.zeros(len(partial_embeds), dtype=int)
    elif prototyper is None:
        labels = AgglomerativeClustering(
            n_clusters=n_talere, linkage="ward"
        ).fit_predict(partial_embeds)
    else:
        sims   = cosine_similarity(partial_embeds, prototyper)
        labels = np.argmax(sims, axis=1)

    embed_dim    = partial_embeds.shape[1]
    ny_prototyper = np.zeros((n_talere, embed_dim), dtype=np.float32)
    for i in range(n_talere):
        maske = labels == i
        if maske.any():
            ny_snitt = partial_embeds[maske].mean(axis=0)
            if prototyper is not None:
                ny_prototyper[i] = 0.8 * prototyper[i] + 0.2 * ny_snitt
            else:
                ny_prototyper[i] = ny_snitt
        elif prototyper is not None:
            ny_prototyper[i] = prototyper[i]

    diari_segs: list[dict] = []
    for sl, label in zip(partial_slices, labels):
        start = sl.start / SAMPLE_RATE
        slutt = sl.stop  / SAMPLE_RATE
        taler = f"SPEAKER_{int(label):02d}"
        if diari_segs and diari_segs[-1]["taler"] == taler:
            diari_segs[-1]["slutt"] = round(slutt, 2)
        else:
            diari_segs.append({"start": round(start, 2), "slutt": round(slutt, 2), "taler": taler})

    return diari_segs, ny_prototyper


def tilordne_taler(
    seg_start: float,
    seg_slutt: float,
    diari_segs: list[dict],
    forrige_taler: str = "SPEAKER_00",
) -> str:
    """Finn dominerende taler for et whisper-tidsvindu basert på overlapp med diariseringssegmentene."""
    stemmer: dict[str, float] = {}
    for d in diari_segs:
        overlapp = min(seg_slutt, d["slutt"]) - max(seg_start, d["start"])
        if overlapp > 0:
            stemmer[d["taler"]] = stemmer.get(d["taler"], 0) + overlapp
    return max(stemmer, key=stemmer.get) if stemmer else forrige_taler

