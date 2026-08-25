import pytest
from worker.prompts.normalisering import normaliser_til_bokmal


@pytest.mark.parametrize("inn, forventet", [
    ("ikkje",           "ikke"),
    ("Ikkje",           "Ikke"),
    ("brukar",          "bruker"),
    ("Brukaren",        "Brukeren"),
    ("rettleiar",       "veileder"),
    ("tilskot",         "tilskudd"),
    ("kva",             "hva"),
    ("møtest",          "møtes"),
    ("snakka",          "snakket"),
    ("jobba",           "jobbet"),
    ("handla",          "handlet"),
    ("ønskjer",         "ønsker"),
    ("søkjer",          "søker"),
    # Skal ikkje endra bokmål-ord
    ("ikke",            "ikke"),
    ("bruker",          "bruker"),
])
def test_erstatningar(inn, forventet):
    assert normaliser_til_bokmal(inn) == forventet


def test_think_blokk_vert_strippa():
    tekst = "<think>lang tenking her</think>\n\nEt vanlig svar."
    assert normaliser_til_bokmal(tekst) == "Et vanlig svar."


def test_think_blokk_med_nynorsk():
    tekst = "<think>ignorert</think>\nBrukaren snakka om tilskot."
    resultat = normaliser_til_bokmal(tekst)
    assert "<think>" not in resultat
    assert "Brukeren" in resultat
    assert "tilskudd" in resultat


def test_ingen_endringar_bokmaal():
    tekst = "Vi avtalte at du skal søke om AAP neste uke."
    assert normaliser_til_bokmal(tekst) == tekst
