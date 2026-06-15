import numpy as np

# Kjente norske Whisper-hallusinasjoner under stillhet.
# Whisper fyller stille segmenter med høyfrekvente fraser fra treningsdata.
HALLUSINASJON_BLOCKLIST = {
    "det er en god idé",
    "takk for at du så på",
    "takk for at du så med",
    "takk for at du hørte på",
    "takk for meg",
    "ha det bra",
    "vi sees",
    "ikke sant",
    "ja, takk",
    "tusen takk",
    "hei hei",
    "og lykke til",
}


def er_hallusinasjon(tekst: str, maks_repetisjoner: int = 3) -> bool:
    """Oppdager Whisper-hallusinasjoner (gjentakende fraser eller kjente fraser under stillhet)."""
    normalisert = tekst.lower().strip().rstrip(".,!?")
    if normalisert in HALLUSINASJON_BLOCKLIST:
        print(f"[hallusinasjon] Blokkert kjent frase: '{tekst}'", flush=True)
        return True
    ord_liste = tekst.split()
    if len(ord_liste) < 4:
        return False
    for n in range(1, len(ord_liste) // maks_repetisjoner + 1):
        for start in range(n):
            fraser = [" ".join(ord_liste[i:i+n]) for i in range(start, len(ord_liste) - n + 1, n)]
            if len(fraser) >= maks_repetisjoner:
                if len(set(f.lower() for f in fraser)) == 1:
                    return True
    return False


def trim_null_ord(ord_liste: list) -> list:
    """
    Fjerner hallusinerte ord der Whisper har stapet dem på nøyaktig samme
    tidsstempel med varighet=0. Kapper alt etter siste ord med varighet >= 0.01s.
    """
    siste_reelle = -1
    for i, c in enumerate(ord_liste):
        ts0, ts1 = c["timestamp"]
        if ts1 is not None and (ts1 - ts0) >= 0.01:
            siste_reelle = i
    if siste_reelle == -1:
        return ord_liste
    kappet = len(ord_liste) - siste_reelle - 1
    if kappet > 0:
        print(f"[trim-null] Kappet {kappet} null-varighet hallusinerte ord "
              f"(fra {ord_liste[siste_reelle + 1]['timestamp'][0]:.2f}s)", flush=True)
    return ord_liste[:siste_reelle + 1]


def trim_null_ord_fw(ord_liste: list) -> list:
    """Samme som trim_null_ord, men for faster-whisper sitt ordformat."""
    siste_reelle = -1
    for i, c in enumerate(ord_liste):
        ts0, ts1 = c["timestamp"]
        if ts1 is not None and (ts1 - ts0) >= 0.01:
            siste_reelle = i
    if siste_reelle == -1:
        return ord_liste
    kappet = len(ord_liste) - siste_reelle - 1
    if kappet > 0:
        print(f"[sanntid trim-null] Kappet {kappet} null-varighet ord", flush=True)
    return ord_liste[:siste_reelle + 1]


def trim_etter_stille(
    ord_liste: list,
    pcm: np.ndarray,
    sample_rate: int = 16000,
    energi_terskel: float = 0.01,
    margin_s: float = 0.4,
) -> list:
    """
    Fjerner ord hvis tidsstempler starter etter at lyden faktisk er slutt.
    Whisper hallusinerer tekst ved stillhet på slutten av opptaket.
    """
    if not ord_liste or len(pcm) == 0:
        return ord_liste

    VINDU_N = int(0.05 * sample_rate)
    siste_tale = 0.0
    for i in range(0, len(pcm) - VINDU_N, VINDU_N):
        if np.sqrt(np.mean(pcm[i:i + VINDU_N] ** 2)) >= energi_terskel:
            siste_tale = (i + VINDU_N) / sample_rate

    tale_grense = siste_tale + margin_s
    print(f"[trim] Siste tale: {siste_tale:.1f}s  grense: {tale_grense:.1f}s  "
          f"(lydfil: {len(pcm)/sample_rate:.1f}s)", flush=True)

    return [c for c in ord_liste if c["timestamp"][0] < tale_grense]


def fjern_hallusinasjon(tekst: str, maks_repetisjoner: int = 3) -> str:
    """Trunkerer tekst ved første repetitive sekvens (Whisper-hallusinasjon)."""
    setninger = [s.strip() for s in tekst.replace("?", ".").replace("!", ".").split(".") if s.strip()]
    if len(setninger) < maks_repetisjoner + 1:
        return tekst
    for i in range(len(setninger) - maks_repetisjoner):
        vindu = setninger[i:i + maks_repetisjoner]
        if len(set(s.lower() for s in vindu)) == 1:
            rein = ". ".join(setninger[:i]).strip()
            return (rein + ".") if rein else ""
    return tekst
