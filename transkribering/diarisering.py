import threading

import numpy as np

from transkribering.konstanter import SAMPLE_RATE

_voice_encoder = None
_voice_encoder_lock = threading.Lock()


def hent_voice_encoder():
    global _voice_encoder
    if _voice_encoder is None:
        with _voice_encoder_lock:
            if _voice_encoder is None:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    from resemblyzer import VoiceEncoder
                _voice_encoder = VoiceEncoder("cpu")
    return _voice_encoder


def auto_n_talere(embeds: np.ndarray, maks: int = 4) -> int:
    """Finner optimalt antall talere via silhouette score (2..maks)."""
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    beste_n = 2
    beste_score = -1.0
    for n in range(2, maks + 1):
        if len(embeds) < n * 2:
            break
        labels = AgglomerativeClustering(n_clusters=n).fit_predict(embeds)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(embeds, labels, metric="cosine")
        print(f"[diarisering] n={n} silhouette={score:.3f}", flush=True)
        if score > beste_score:
            beste_score = score
            beste_n = n
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
        prototyper: shape (n_talere, 256) – kjente talere fra tidligere chunks.

    Returns:
        (diari_segs, prototyper)
        diari_segs: [{start, slutt, taler}]
        prototyper: oppdaterte prototyper for neste kall
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics.pairwise import cosine_similarity

    if len(wav) < SAMPLE_RATE * 1.0:
        return [], prototyper

    encoder = hent_voice_encoder()
    try:
        _, partial_embeds, partial_slices = encoder.embed_utterance(
            wav, return_partials=True, rate=1.5
        )
    except Exception as exc:
        print(f"[diarisering] Embed feil: {exc}", flush=True)
        return [], prototyper

    partial_embeds = np.array(partial_embeds)
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
        labels = AgglomerativeClustering(n_clusters=n_talere).fit_predict(partial_embeds)
    else:
        sims = cosine_similarity(partial_embeds, prototyper)
        labels = np.argmax(sims, axis=1)

    ny_prototyper = np.zeros((n_talere, partial_embeds.shape[1]), dtype=np.float32)
    for i in range(n_talere):
        maske = labels == i
        if maske.any():
            ny_snitt = partial_embeds[maske].mean(axis=0)
            if prototyper is not None:
                ny_prototyper[i] = 0.85 * prototyper[i] + 0.15 * ny_snitt
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
