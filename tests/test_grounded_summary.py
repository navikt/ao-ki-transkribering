import json

import pytest

from frontend import grounded_summary as grounded
from frontend.long_transcript import TranscriptProcessingError


SOURCE = (
    "Vegard: Kan vi teste uten PVK?\n"
    "Marte: Dette er fortsatt uavklart. Ingen test er godkjent.\n"
    "Gro: Jeg lager utkastet til ledernotatet. Ingen frist er satt.\n"
    "Roman: Kostnadene er foreløpige estimater."
)


def evidence():
    return {"items": [
        {"topic": "Juridisk avklaring", "kind": "open_question", "statement": "PVK er uavklart.",
         "source_ids": [1], "owner": None, "owner_ids": []},
        {"topic": "Ledernotat", "kind": "task", "statement": "Gro lager utkastet.",
         "source_ids": [1], "owner": "Gro", "owner_ids": [1]},
        {"topic": "Kostnader", "kind": "discussion", "statement": "Kostnadene er foreløpige.",
         "source_ids": [1], "owner": None, "owner_ids": []},
    ]}


async def roomy_counter(system, user, model):
    return max(1, len(system + user) // 5)


def test_source_references_keep_short_utterances_separate_and_preserve_all_characters():
    source = "Gro: Ingen godkjenning.\nFredrik: Vi trenger KI-kompetanse.\n\nRoman: Kostnadene er usikre."
    spans = grounded._sources(source)
    assert "".join(spans.values()) == source
    assert spans[2] == "Fredrik: Vi trenger KI-kompetanse.\n"
    assert all("Gro:" not in span or "Fredrik:" not in span for span in spans.values())


@pytest.mark.asyncio
async def test_whole_transcript_reaches_draft_and_verifier_and_repairs_invented_owner():
    calls = []
    drafts = iter(["Vegard lager ledernotatet.", "PVK er uavklart. Gro lager ledernotatet. Kostnadene er foreløpige."])

    async def call(system, user, model, **kwargs):
        calls.append((system, user, kwargs))
        if system == grounded.EXTRACT_SYSTEM:
            return json.dumps(evidence())
        if system == grounded.AUDIT_SYSTEM:
            data = json.loads(user)
            assert "".join(data["source"].values()) == SOURCE
            if data["draft"].startswith("Vegard"):
                return json.dumps({"checks": [{"id": claim["id"], "status": "contradicted",
                    "source_ids": [1]} for claim in data["claims"]]})
            return json.dumps({"checks": [{"id": claim["id"], "status": "supported", "source_ids": [1]}
                                          for claim in data["claims"]]})
        if system == grounded.COVERAGE_SYSTEM:
            from tests.summary_fakes import structured_reply
            return structured_reply(system, user)
        assert SOURCE in user
        return next(drafts)

    events = [event async for event in grounded.generate(
        SOURCE, lambda text: ("Skriv referat", text), "borealis-12b", call=call, count=roomy_counter,
        max_tokens=3072,
    )]
    assert events[-1]["tekst"].startswith("PVK er uavklart. Gro")
    assert "Vegard lager" not in events[-1]["tekst"]
    assert sum(system == grounded.AUDIT_SYSTEM for system, _, _ in calls) == 2
    assert not any(event["type"] == "token" for event in events)


@pytest.mark.asyncio
async def test_unsupported_owner_is_left_unassigned():
    data = evidence()
    data["items"][1]["owner"] = "Ola"

    async def call(*args, **kwargs):
        return json.dumps(data)

    summary = grounded.Summary(lambda text: ("Regler", text), None, call, roomy_counter, 3072, True)
    events = [event async for event in summary.extract(SOURCE)]
    assert events[-1]["items"][1].owner is None
    assert events[-1]["items"][1].owner_ids == []


@pytest.mark.asyncio
async def test_persistently_unsupported_draft_never_becomes_final():
    async def call(system, user, model, **kwargs):
        if system == grounded.EXTRACT_SYSTEM:
            return json.dumps(evidence())
        if system == grounded.AUDIT_SYSTEM:
            data = json.loads(user)
            return json.dumps({"checks": [{"id": claim["id"], "status": "contradicted",
                "source_ids": [1]} for claim in data["claims"]]})
        return "Testing er godkjent."

    events = []
    with pytest.raises(TranscriptProcessingError, match="kontroll"):
        async for event in grounded.generate(SOURCE, lambda text: ("Regler", text), None,
                                             call=call, count=roomy_counter):
            events.append(event)
    assert not any(event["type"] == "ferdig" for event in events)


@pytest.mark.asyncio
async def test_exact_count_avoids_unnecessary_condensation(monkeypatch):
    import frontend.long_transcript as long_text

    source = "Dette er norsk møtetekst. " * 300
    calls = []

    async def count(system, user, model):
        return 100

    async def call(system, user, model, **kwargs):
        calls.append(user)
        return "Referat"

    monkeypatch.setattr(long_text, "input_token_budget", lambda *_args: 1000)
    events = [event async for event in long_text.generate(
        source, lambda text: ("Regler", text), None, call=call, count=count,
    )]
    assert calls == [source]
    assert events[-1]["tekst"] == "Referat"


@pytest.mark.asyncio
@pytest.mark.parametrize("utterances", [512, 513, 1000])
async def test_large_fitting_transcript_uses_gateway_safe_checks_without_splitting_draft(utterances):
    from images.litellm.scoped_auth import _safe_response_format
    from tests.summary_fakes import structured_reply

    source = "Gro: Vi må avklare dette.\n" * utterances
    extracted, audited, drafted = [], [], []

    async def count(system, user, model):
        return 100  # The whole transcript fits; schema limits must still apply.

    async def call(system, user, model, **options):
        if options.get("json_mode"):
            schema = options["response_schema"]
            assert _safe_response_format({"type": "json_schema", "json_schema": {
                "name": "transkriberit_" + schema["title"], "strict": True, "schema": schema,
            }})
            payload = json.loads(user)
            if system in {grounded.EXTRACT_SYSTEM, grounded.AUDIT_SYSTEM}:
                (extracted if system == grounded.EXTRACT_SYSTEM else audited).append(payload["source"])
            return structured_reply(system, user)
        drafted.append(user)
        return "Vi må avklare dette."

    events = [event async for event in grounded.generate(
        source, lambda text: ("Skriv referat", text), "borealis-12b",
        call=call, count=count, max_tokens=3072,
    )]
    assert events[-1]["type"] == "ferdig"
    assert drafted == [source]
    assert "".join(text for group in extracted for text in group.values()) == source
    assert "".join(text for group in audited for text in group.values()) == source
    expected_ids = [str(index) for index in range(1, utterances + 1)]
    assert [index for group in extracted for index in group] == expected_ids
    assert [index for group in audited for index in group] == expected_ids


@pytest.mark.asyncio
async def test_later_source_contradiction_overrides_earlier_apparent_support(monkeypatch):
    from tests.summary_fakes import structured_reply

    monkeypatch.setattr(grounded, "input_token_budget", lambda *_args: 2000)
    source = "Taler 1: Testing er godkjent.\n" + "Møtet fortsetter om praktiske detaljer.\n" * 550
    source += "Taler 2: Rettelse: Ingen test er godkjent. PVK er uavklart."
    audited = []

    async def call(system, user, model, **kwargs):
        if system == grounded.AUDIT_SYSTEM:
            data = json.loads(user)
            part = "".join(data["source"].values())
            audited.append(part)
            status = "contradicted" if "Rettelse:" in part else "supported" if "Testing er godkjent." in part else "not_found"
            refs = [int(index) for index, text in data["source"].items() if "Rettelse:" in text or "Testing er godkjent." in text]
            return json.dumps({"checks": [{"id": claim["id"], "status": status, "source_ids": refs} for claim in data["claims"]]})
        return structured_reply(system, user) or "Testing er godkjent."

    with pytest.raises(TranscriptProcessingError, match="kontroll"):
        async for _ in grounded.generate(source, lambda text: ("Regler", text), None,
                                        call=call, count=roomy_counter):
            pass
    assert any("Rettelse:" in part for part in audited)
    assert any("Testing er godkjent." in part for part in audited)


@pytest.mark.asyncio
async def test_incomplete_audit_and_invented_audit_quotes_are_rejected():
    from tests.summary_fakes import structured_reply

    async def call(system, user, model, **kwargs):
        if system == grounded.AUDIT_SYSTEM:
            return json.dumps({"checks": [{"id": 999, "status": "supported", "source_ids": [999]}]})
        return structured_reply(system, user) or "Gro lager notatet."

    with pytest.raises(TranscriptProcessingError, match="kilde"):
        async for _ in grounded.generate(SOURCE, lambda text: ("Regler", text), None,
                                        call=call, count=roomy_counter):
            pass


@pytest.mark.asyncio
async def test_truncated_extraction_retries_smaller_source_sections():
    from tests.summary_fakes import structured_reply
    from shared.services.llm_proxy_client import IncompleteModelResponse

    source = "Møtet har åpne spørsmål om testingen.\n" * 100
    accepted = []

    async def call(system, user, model, **kwargs):
        part = json.loads(user)["source"]
        if sum(len(text) for text in part.values()) > 1000:
            raise IncompleteModelResponse("Incomplete evidence")
        accepted.append("".join(part.values()))
        return structured_reply(system, user)

    summary = grounded.Summary(lambda text: ("Regler", text), None, call, roomy_counter, 3072, True)
    events = [event async for event in summary.extract(source)]
    assert "".join(accepted) == source
    assert events[-1]["type"] == "evidence"


@pytest.mark.asyncio
async def test_minimal_summary_still_verifies_claims_without_requiring_all_topics():
    from tests.summary_fakes import structured_reply
    flags = []

    async def call(system, user, model, **kwargs):
        if system == grounded.AUDIT_SYSTEM:
            flags.append(json.loads(user)["check_coverage"])
        return structured_reply(system, user) or "PVK er uavklart."

    events = [event async for event in grounded.generate(SOURCE, lambda text: ("Maks tre punkter", text), None,
        call=call, count=roomy_counter, comprehensive=False)]
    assert flags == [False]
    assert events[-1]["type"] == "ferdig"


@pytest.mark.asyncio
async def test_independent_coverage_finds_a_topic_missing_from_extracted_outline():
    from tests.summary_fakes import structured_reply
    source = SOURCE + "\nFredrik: Veilederne trenger KI-kompetanse."
    drafts = iter(["PVK er uavklart.", "PVK er uavklart. Veilederne trenger KI-kompetanse."])

    async def call(system, user, model, **kwargs):
        if system == grounded.COVERAGE_SYSTEM:
            data = json.loads(user)
            missing = not any("KI-kompetanse" in claim["text"] for claim in data["draft"])
            return json.dumps({"checks": [{"source_id": int(index), "topics": [{"topic": "KI-kompetanse",
                "draft_ids": [] if missing else [data["draft"][0]["id"]]}]} for index in data["source"]]})
        return structured_reply(system, user) or next(drafts)

    events = [event async for event in grounded.generate(source, lambda text: ("Regler", text), None,
        call=call, count=roomy_counter)]
    assert "KI-kompetanse" in events[-1]["tekst"]


@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream", "/sammendrag/stream"])
def test_oversized_summary_routes_extract_and_verify_every_original_character(monkeypatch, endpoint):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from frontend.routes import referat
    from shared.services import llm_proxy_client
    from tests.summary_fakes import structured_reply

    monkeypatch.setitem(llm_proxy_client._OPTIONS, "num_ctx", 8192)
    source = "".join(f"Taler 1: Sak {index}: PVK er fortsatt uavklart.\n" for index in range(1000))
    extracted, audited = [], []

    async def count(system, user, model):
        return (len((system + user).encode()) + 3) // 4

    async def call(system, user, model, **kwargs):
        assert (len((system + user).encode()) + 3) // 4 <= llm_proxy_client.input_token_budget(kwargs.get("max_tokens"))
        if system in {grounded.EXTRACT_SYSTEM, grounded.AUDIT_SYSTEM}:
            target = extracted if system == grounded.EXTRACT_SYSTEM else audited
            target.append("".join(json.loads(user)["source"].values()))
        return structured_reply(system, user) or "PVK er fortsatt uavklart."

    monkeypatch.setattr(referat, "count_llm_tokens", count)
    monkeypatch.setattr(referat, "kall_llm", call)
    app = FastAPI()
    app.include_router(referat.router)
    with TestClient(app) as client:
        response = client.post(endpoint, json={"transkripsjon": source, "scenario": "utfyllende_sammendrag"})
    assert response.status_code == 200, response.text
    if endpoint.endswith("/stream"):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["type"] == "ferdig", events
        assert not any(event["type"] == "token" for event in events)
    else:
        assert response.json()["tekst"] == "PVK er fortsatt uavklart."
    assert "".join(extracted) == source
    assert "".join(audited) == source


def test_source_excerpts_preserve_unicode_whitespace_and_order():
    source = "Ærlig prat: hei!\n\n" + "Blåbær og møtereferat. " * 300 + "\nSiste ord.  "
    assert "".join(grounded._sources(source).values()) == source


@pytest.mark.asyncio
async def test_mixed_paragraph_is_checked_as_separate_norwegian_sentences():
    captured = []

    async def call(system, user, model, **kwargs):
        data = json.loads(user)
        captured.extend(claim["text"] for claim in data["claims"])
        return json.dumps({"checks": [{"id": claim["id"], "status": "not_found", "source_ids": []}
                                      for claim in data["claims"]]})

    summary = grounded.Summary(lambda text: ("Regler", text), None, call, roomy_counter, 3072, False)
    await summary.audit(SOURCE, "Vi vurderer f.eks. intern testing. Ingen test er godkjent.", [])
    assert captured == ["Vi vurderer f.eks. intern testing.", "Ingen test er godkjent."]


@pytest.mark.parametrize("value,valid", [(1, True), ("1", True), (True, False), (1.0, False), ("01", False), ("1.0", False)])
def test_source_references_accept_only_lossless_integer_ids(value, valid):
    from pydantic import ValidationError

    payload = {"id": 0, "status": "supported", "source_ids": [value]}
    if valid:
        assert grounded.ClaimCheck.model_validate(payload).source_ids == [1]
    else:
        with pytest.raises(ValidationError):
            grounded.ClaimCheck.model_validate(payload)


@pytest.mark.asyncio
async def test_unsupported_sentence_is_removed_and_filtered_draft_is_rechecked():
    from tests.summary_fakes import structured_reply
    drafts, audits = [], []

    async def call(system, user, model, **kwargs):
        if system == grounded.AUDIT_SYSTEM:
            data = json.loads(user)
            audits.append(data["draft"])
            return json.dumps({"checks": [{"id": claim["id"],
                "status": "not_found" if "Vegard lager" in claim["text"] else "supported",
                "source_ids": [] if "Vegard lager" in claim["text"] else [1]} for claim in data["claims"]]})
        reply = structured_reply(system, user)
        if reply is not None:
            return reply
        drafts.append(True)
        return "PVK er uavklart. Vegard lager ledernotatet."

    events = [event async for event in grounded.generate(SOURCE, lambda text: ("Regler", text), None,
        call=call, count=roomy_counter, comprehensive=False)]
    assert events[-1]["tekst"] == "PVK er uavklart."
    assert len(drafts) == 1
    assert audits == ["PVK er uavklart. Vegard lager ledernotatet.", "PVK er uavklart."]
