import numpy as np
import pytest
from worker.transkribering.hallusinasjon import (
    er_hallusinasjon,
    fjern_hallusinasjon,
    trim_null_ord,
    trim_etter_stille,
)


# ---------------------------------------------------------------------------
# er_hallusinasjon
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tekst", [
    "takk for at du så på",
    "takk for at du hørte på",
    "ha det bra",
    "hei hei",
])
def test_kjente_fraser_er_hallusinasjon(tekst):
    assert er_hallusinasjon(tekst)


@pytest.mark.parametrize("tekst", [
    "Vi avtalte at du skal søke om AAP neste uke.",
    "Jeg har jobbet som lagermedarbeider i fem år.",
    "Neste møte er om tre uker.",
])
def test_normale_setningar_er_ikkje_hallusinasjon(tekst):
    assert not er_hallusinasjon(tekst)


def test_repetisjon_er_hallusinasjon():
    tekst = "ja ja ja ja ja ja ja ja ja ja ja ja"
    assert er_hallusinasjon(tekst)


def test_kort_tekst_er_ikkje_hallusinasjon():
    assert not er_hallusinasjon("ja")
    assert not er_hallusinasjon("ok takk")


# ---------------------------------------------------------------------------
# fjern_hallusinasjon
# ---------------------------------------------------------------------------

def test_fjern_hallusinasjon_trunkerer_repetisjon():
    god = "Vi snakket om AAP."
    repetisjon = "Hei. Hei. Hei. Hei."
    tekst = god + " " + repetisjon
    resultat = fjern_hallusinasjon(tekst)
    assert "AAP" in resultat
    assert resultat.count("Hei") <= 2


def test_fjern_hallusinasjon_uendra_utan_repetisjon():
    tekst = "Vi snakket om AAP. Du skal søke neste uke. Vi møtes igjen om tre uker."
    assert fjern_hallusinasjon(tekst) == tekst


# ---------------------------------------------------------------------------
# trim_null_ord
# ---------------------------------------------------------------------------

def test_trim_null_ord_fjernar_null_varighet():
    ord_liste = [
        {"timestamp": (0.0, 0.5), "text": "Hei"},
        {"timestamp": (0.5, 1.0), "text": "på"},
        {"timestamp": (1.0, 1.0), "text": "hallusinert"},  # null-varighet
        {"timestamp": (1.0, 1.0), "text": "hallusinert2"},
    ]
    resultat = trim_null_ord(ord_liste)
    assert len(resultat) == 2
    assert resultat[-1]["text"] == "på"


def test_trim_null_ord_bevarar_alle_reelle():
    ord_liste = [
        {"timestamp": (0.0, 0.5), "text": "Hei"},
        {"timestamp": (0.5, 1.2), "text": "verden"},
    ]
    assert trim_null_ord(ord_liste) == ord_liste


# ---------------------------------------------------------------------------
# trim_etter_stille
# ---------------------------------------------------------------------------

def test_trim_etter_stille_fjernar_ord_etter_stille():
    sample_rate = 16000
    # 1 sekund tale, deretter stille
    tale = np.ones(sample_rate, dtype="float32") * 0.5
    stille = np.zeros(sample_rate, dtype="float32")
    pcm = np.concatenate([tale, stille])

    ord_liste = [
        {"timestamp": (0.2, 0.8), "text": "Hei"},       # under tale
        {"timestamp": (1.5, 1.9), "text": "hallusinert"},  # under stille
    ]
    resultat = trim_etter_stille(ord_liste, pcm, sample_rate)
    assert len(resultat) == 1
    assert resultat[0]["text"] == "Hei"


def test_trim_etter_stille_kan_bevare_ord_med_storre_margin():
    sample_rate = 16000
    tale = np.ones(sample_rate, dtype="float32") * 0.5
    stille = np.zeros(sample_rate, dtype="float32")
    pcm = np.concatenate([tale, stille])

    ord_liste = [
        {"timestamp": (0.2, 0.8), "text": "Hei"},
        {"timestamp": (1.5, 1.9), "text": "etterheng"},
    ]

    resultat = trim_etter_stille(ord_liste, pcm, sample_rate, margin_s=1.5)

    assert len(resultat) == 2


def test_trim_etter_stille_bevarer_stille_taler_etter_hoyere_taler():
    pcm = np.concatenate([
        np.full(16000, 0.02, dtype="float32"),
        np.full(32000, 0.002, dtype="float32"),
        np.zeros(16000, dtype="float32"),
    ])
    words = [
        {"timestamp": (0.2, 0.8), "text": "Hei"},
        {"timestamp": (2.5, 2.9), "text": "lav tale"},
        {"timestamp": (3.6, 3.9), "text": "hallusinasjon"},
    ]
    assert trim_etter_stille(words, pcm) == words[:2]


def test_trim_etter_stille_behandler_siste_delvise_vindu():
    pcm = np.concatenate([np.zeros(16000, dtype="float32"), np.full(100, 0.005, dtype="float32")])
    words = [{"timestamp": (1.0, 1.006), "text": "slutt"}]
    assert trim_etter_stille(words, pcm) == words


def test_trim_etter_stille_fjerner_sen_tekst_fra_helt_stille_lyd():
    pcm = np.zeros(32000, dtype="float32")
    words = [{"timestamp": (1.0, 1.5), "text": "hallusinasjon"}]
    assert trim_etter_stille(words, pcm) == []
