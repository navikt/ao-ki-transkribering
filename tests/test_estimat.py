import pytest
from worker.prompts.estimat import beregn_llm_estimat


def test_kjent_modell_gir_estimat():
    estimat = beregn_llm_estimat("qwen3:32b", "kort tekst")
    assert estimat > 0


def test_ukjent_modell_brukar_fallback():
    estimat = beregn_llm_estimat(None, "kort tekst", fallback="qwen3:32b")
    assert estimat > 0


def test_lang_transkripsjon_gir_hoyare_estimat():
    kort = beregn_llm_estimat("qwen3:32b", "ord " * 100)
    lang = beregn_llm_estimat("qwen3:32b", "ord " * 3000)
    assert lang > kort


def test_minimum_er_5_sekund():
    assert beregn_llm_estimat("ukjent:modell", "") >= 5
