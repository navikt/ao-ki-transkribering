import pytest

from worker.prompts.handlinger import hent_handling, hent_referat_scenario
from worker.prompts.motereferat import SAMMENDRAG_STIL


@pytest.mark.parametrize("prompt", [
    hent_handling("sammendrag").system_prompt,
    hent_referat_scenario("minimalt_sammendrag").system_prompt,
    hent_referat_scenario("utfyllende_sammendrag").system_prompt,
])
def test_summaries_request_direct_language_without_losing_attribution(prompt):
    assert SAMMENDRAG_STIL in prompt
    assert "Det ble diskutert" in prompt
    assert "skillet mellom forslag og beslutninger" in prompt
    assert "Ikke gjør en deltakers påstand til et fastslått faktum" in prompt


def test_detailed_summary_requests_thematic_report_without_guessing_speakers():
    scenario = hent_referat_scenario("utfyllende_sammendrag")
    prompt = scenario.bygg_bruker_prompt("KILDE: Simulering først, pilot er ikke avklart.")
    assert "# Møtereferat" in prompt
    assert "## Kort oppsummering" in prompt
    assert "én nummerert seksjon per vesentlig tema" in prompt
    assert "Oppgaver og oppfølging" in prompt
    assert "Viktigste uavklarte spørsmål" in prompt
    assert "Overordnet konklusjon" in prompt
    assert "Ikke gjett navn fra taler-ID" in scenario.system_prompt
    assert "Gjengi juridiske synspunkter som deltakernes vurderinger" in scenario.system_prompt
    assert "ikke korriger dem ved gjetning" in scenario.system_prompt
    assert prompt.count("KILDE:") == 1
    assert scenario.max_tokens == 3072
    assert hent_referat_scenario("minimalt_sammendrag").max_tokens is None
