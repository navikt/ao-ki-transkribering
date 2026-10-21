"""Deterministic model replies for routing tests, not a factual-quality oracle."""

import json

from frontend.grounded_summary import AUDIT_SYSTEM, COVERAGE_SYSTEM, EXTRACT_SYSTEM
from frontend.compact_summary import REVIEW_SYSTEM


def structured_reply(system, user):
    if system == REVIEW_SYSTEM:
        data = json.loads(user)
        return json.dumps({"accepted": [True for claim in data["claims"]], "conflicts": [], "missing_topics": []})
    if system == EXTRACT_SYSTEM:
        source = json.loads(user)["source"]
        first = next(iter(source))
        return json.dumps({"items": [{"topic": "Møtet", "kind": "discussion", "statement": source[first][:100],
                                      "source_ids": [int(first)]}]})
    if system == AUDIT_SYSTEM:
        data = json.loads(user)
        return json.dumps({"checks": [{"id": claim["id"], "status": "supported", "source_ids": [int(next(iter(data["source"]))) ]}
                                      for claim in data["claims"]]})
    if system == COVERAGE_SYSTEM:
        data = json.loads(user)
        return json.dumps({"checks": [{"source_id": int(index), "topics": [{"topic": "Møtet", "draft_ids": [data["draft"][0]["id"]]}]}
                                      for index in data["source"]]})
    return None


def checked_model(monkeypatch, call):
    from frontend.routes import referat

    async def model(system, user, selected, **kwargs):
        reply = structured_reply(system, user)
        if reply is not None:
            return reply
        return await call(system, user, selected, **kwargs)

    monkeypatch.setattr(referat, "kall_llm", model)
