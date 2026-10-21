import pytest

from frontend.summary_assignments import guard_assignments, source_follow_up


@pytest.mark.parametrize("heading", [
    "## Oppgaver og oppfølging", "## 2. Oppgaver og oppfølging",
    "**Arbeidsfordeling**", "3. Oppgaver", "Oppgaver og ansvar",
    "### Action items",
])
def test_generated_task_sections_are_replaced_including_roles_and_deadlines(heading):
    source = "Taler 1: Cia, oppsummer innsikt."
    result = guard_assignments(source, f"{heading}\nBo (økonomi): Ferdig PVK fredag.\n"
                               "## Konklusjon\nPVK er uavklart.", comprehensive=True)
    assert "økonomi" not in result
    assert "fredag" not in result
    assert "PVK er uavklart." in result
    assert source in result


@pytest.mark.parametrize("claim", [
    "Ada og Bo skal avklare PVK.",
    "Cia (innsikt) skal lage notatet.",
    "Dag får ansvaret for testplanen.",
    "Bo er prosjektleder.",
    "Bo (økonomi) skal presentere budsjettet.",
    "Alle skal teste i november.",
    "Ada: Undersøk PVK.",
    "**Ada skal avklare PVK**",
    "Ada og Bo\n  skal avklare PVK.",
    "- Ada og Bo\n  skal avklare PVK.",
    "**Bo** (økonomi) skal presentere budsjettet.",
    "PVK-ansvar: Bo.",
])
def test_assignment_claims_are_removed_even_outside_task_section(claim):
    result = guard_assignments("Taler 1: PVK er uavklart.",
                               "## Status\n" + claim, comprehensive=True)
    assert claim not in result


def test_source_questions_are_not_turned_into_assignments():
    assert source_follow_up("Dag: Kan Bo avklare PVK?") == []


def test_source_anonymous_identity_negation_and_condition_are_not_rewritten():
    source = "Taler 4: Jeg lager notatet hvis vi får tid. Bo skal ikke lage det."
    result = guard_assignments(source, "Bo lager notatet.", comprehensive=True)
    assert source in result
    assert "Bo lager notatet." not in result
    assert guard_assignments(source, result, comprehensive=True) == result


def test_source_links_and_html_are_not_rendered_as_markup():
    result = guard_assignments("Ada: Jeg lager [notatet](https://example.org) <img>.",
                               "## Oppgaver\nBo lager notatet.", comprehensive=True)
    assert "[notatet]" not in result
    assert "<img>" not in result


def test_source_quote_is_not_silently_truncated_or_split():
    assert source_follow_up("Ada: Jeg lager notatet. " + "kontekst " * 200) == []


def test_brief_summary_does_not_append_unrelated_source_tasks():
    assert guard_assignments("Ada: Jeg lager notatet.", "PVK er uavklart.",
                              comprehensive=False) == "PVK er uavklart."


def test_named_assignment_late_in_meeting_is_not_crowded_out_by_generic_plans():
    source = "\n".join(f"Taler 1: Vi skal diskutere tema {index}." for index in range(30))
    source += "\nTaler 2: Ada og Bo ser på kostnadene."
    quotes = source_follow_up(source)
    assert len(quotes) == 1
    assert quotes[-1] == "Taler 2: Ada og Bo ser på kostnadene."


def test_only_a_heading_is_not_treated_as_remaining_summary_content():
    assert guard_assignments("PVK er uavklart.", "## Status\nBo skal avklare PVK.",
                              comprehensive=True) == ""


def test_explicit_assignment_after_generic_statement_is_retained():
    source = "Taler 1: Dere må lese utkastet. Cia, oppsummer innsikt."
    assert source_follow_up(source) == [source]


def test_first_person_modals_require_a_concrete_follow_up_verb():
    assert source_follow_up("Taler 1: Jeg må eventuelt mingle litt.") == []
    source = "Taler 1: Jeg skal skrive notatet."
    assert source_follow_up(source) == [source]


def test_brief_assignment_only_summary_uses_a_short_source_quote():
    source = "Ada: Jeg lager notatet."
    assert guard_assignments(source, "Ada lager notatet.", comprehensive=False) == "> " + source
