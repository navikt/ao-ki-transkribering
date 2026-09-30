from worker.transkribering.nav_ordliste import nav_ordliste_prompt, normaliser_nav_ord


def test_normaliser_nav_ord_retter_kjente_asr_feil():
    tekst = "Du logger inn på navn.no og søker a a p."

    assert normaliser_nav_ord(tekst) == "Du logger inn på nav.no og søker AAP."


def test_normaliser_nav_ord_retter_arbeidsavklaringspenger():
    tekst = "Jeg har søkt arbeidstakernes penger og arbeidsavklarings penger."

    assert normaliser_nav_ord(tekst) == (
        "Jeg har søkt arbeidsavklaringspenger og arbeidsavklaringspenger."
    )


def test_nav_ordliste_prompt_har_sentrale_nav_begreper():
    prompt = nav_ordliste_prompt()

    assert "AAP" in prompt
    assert "nav.no" in prompt
    assert "nav-loven §14a" in prompt
