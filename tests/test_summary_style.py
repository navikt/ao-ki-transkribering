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
