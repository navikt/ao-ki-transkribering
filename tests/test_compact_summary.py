import json

import pytest

from frontend import compact_summary as compact
from frontend.long_transcript import TranscriptProcessingError
from shared.services.llm_proxy_client import IncompleteModelResponse


async def roomy(*args):
    return 100


@pytest.mark.asyncio
async def test_approving_reviewer_cannot_publish_invented_task_ownership():
    source = (
        "Taler 1: Ada og Bo ser på hvordan kostnader kan presenteres.\n"
        "Taler 1: Cia, oppsummer innsikt.\n"
        "Taler 2: Behovet for PVK er uavklart."
    )

    async def call(system, user, model, **options):
        if system == compact.REVIEW_SYSTEM:
            return accepted(user)
        return (
            "## Status\nBehovet for PVK er uavklart.\n"
            "Ada og Bo skal avklare PVK.\n"
            "## Oppgaver og oppfølging\n"
            "- Cia skal lage notatet og risikovurderingen.\n"
            "- Bo (økonomi): Presentere et ferdig budsjett.\n"
            "## Åpne spørsmål\nNår kan testen starte?"
        )

    events = [item async for item in compact.generate(source, lambda text: ("Regler", text), None,
                                                     call=call, count=roomy, max_tokens=3072)]
    result = events[-1]["tekst"]
    assert "skal avklare PVK" not in result
    assert "skal lage notatet" not in result
    assert "Bo (økonomi)" not in result
    assert "Taler 1: Ada og Bo ser på hvordan kostnader kan presenteres." in result
    assert "Taler 1: Cia, oppsummer innsikt." in result
    assert "Behovet for PVK er uavklart." in result


def accepted(user):
    data = json.loads(user)
    return json.dumps({"accepted": [True for claim in data["claims"]],
                       "conflicts": [], "missing_topics": []})


@pytest.mark.asyncio
async def test_fitting_meeting_uses_one_draft_and_one_compact_review():
    source = "Gro: Jeg lager notatet.\nRoman: Kostnadene er uavklart.\n" * 300
    calls = []

    async def call(system, user, model, **options):
        calls.append((system, user, options))
        if system == compact.REVIEW_SYSTEM:
            assert "".join(json.loads(user)["source"].values()) == source
            assert options["max_tokens"] == compact.REVIEW_TOKENS
            return accepted(user)
        assert user == source
        return "Gro lager notatet. Kostnadene er uavklart."

    events = [item async for item in compact.generate(source, lambda text: ("Regler", text), "borealis-12b",
                                                     call=call, count=roomy, max_tokens=3072)]
    assert events[-1]["type"] == "ferdig"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_invented_owner_is_replaced_by_source_without_a_rewrite():
    source = "Gro: Jeg lager notatet. Vegard skal ikke lage notatet."
    checked = []

    async def call(system, user, model, **options):
        if system == compact.REVIEW_SYSTEM:
            data = json.loads(user)
            checked.extend(claim["text"] for claim in data["claims"])
            return accepted(user)
        return "Vegard lager notatet."

    events = [item async for item in compact.generate(source, lambda text: ("Regler", text), None,
                                                     call=call, count=roomy, max_tokens=3072)]
    assert "Vegard lager notatet." not in events[-1]["tekst"]
    assert source in events[-1]["tekst"]
    assert not any(claim == "Vegard lager notatet." for claim in checked)


@pytest.mark.asyncio
async def test_truncated_review_splits_source_and_keeps_later_contradiction():
    source = "Gro: Testen er godkjent.\nMarte: Rettelse, ingen test er godkjent."
    reviewed = []

    async def call(system, user, model, **options):
        data = json.loads(user)
        if len(data["source"]) > 1:
            raise IncompleteModelResponse("Synthetic truncation")
        index, text = next(iter(data["source"].items()))
        reviewed.append(text)
        conflict = "Rettelse" in text
        return json.dumps({"accepted": [not conflict],
                           "conflicts": [{"id": 0, "source_ids": [int(index)]}] if conflict else [], "missing_topics": []})

    summary = compact.Summary(lambda text: ("Regler", text), None, call, roomy, 3072, True)
    report = await compact.review(summary, source, "Testen er godkjent.")
    assert "".join(reviewed) == source
    assert [issue["claim"] for issue in report["issues"]] == ["Testen er godkjent."]


@pytest.mark.asyncio
async def test_missing_topic_is_repaired_without_claiming_a_complete_review():
    source = "Gro: PVK er uavklart.\nFredrik: Veilederne trenger KI-kompetanse."
    drafts = iter(["PVK er uavklart.", "PVK er uavklart. Veilederne trenger KI-kompetanse."])

    async def call(system, user, model, **options):
        if system != compact.REVIEW_SYSTEM:
            return next(drafts)
        data = json.loads(user)
        reply = json.loads(accepted(user))
        if "KI-kompetanse" not in data["draft"]:
            reply["missing_topics"] = [{"topic": "KI-kompetanse", "source_ids": [2]}]
        return json.dumps(reply)

    events = [item async for item in compact.generate(source, lambda text: ("Regler", text), None,
                                                     call=call, count=roomy, max_tokens=3072)]
    assert "KI-kompetanse" in events[-1]["tekst"]


@pytest.mark.asyncio
async def test_irreducible_truncation_fails_closed():
    async def call(system, user, model, **options):
        if system == compact.REVIEW_SYSTEM:
            raise IncompleteModelResponse("Synthetic truncation")
        return "Ingen test er godkjent."

    events = []
    with pytest.raises(TranscriptProcessingError, match="svargrensen"):
        async for item in compact.generate("Gro: Ingen test er godkjent.", lambda text: ("Regler", text), None,
                                           call=call, count=roomy):
            events.append(item)
    assert not any(item["type"] == "ferdig" for item in events)


@pytest.mark.asyncio
async def test_unknown_source_reference_is_never_accepted():
    checks = []

    async def call(system, user, model, **options):
        checks.append(True)
        return json.dumps({"accepted": [False], "conflicts": [{"id": 0, "source_ids": [999]}], "missing_topics": []})

    summary = compact.Summary(lambda text: ("Regler", text), None, call, roomy, 3072, True)
    with pytest.raises(TranscriptProcessingError, match="kildehenvisninger"):
        await compact.review(summary, "Gro: Ingen test er godkjent.", "Testen er godkjent.")
    assert len(checks) == 2


@pytest.mark.asyncio
async def test_brief_summary_does_not_require_every_meeting_topic():
    async def call(system, user, model, **options):
        if system == compact.REVIEW_SYSTEM:
            assert json.loads(user)["check_coverage"] is False
            return accepted(user)
        return "PVK er uavklart."

    events = [item async for item in compact.generate("Gro: PVK er uavklart.\nRoman: Vi drøfter kostnader.",
        lambda text: ("Maks tre punkter", text), None, call=call, count=roomy, comprehensive=False)]
    assert events[-1]["tekst"] == "PVK er uavklart."


def test_compact_schema_accepts_long_contiguous_reference_ranges_without_large_enums():
    from images.litellm.scoped_auth import _safe_response_format

    source = {index: "Gro: Syntetisk kilde.\n" for index in range(1, 1001)}
    payload = json.loads(compact.review_payload(source, "Uavklart.", [{"id": 0, "text": "Uavklart."}], "Regler", True))
    assert _safe_response_format({"type": "json_schema", "json_schema": {
        "name": "transkriberit_CompactReview", "strict": True, "schema": payload["response_schema"],
    }})
