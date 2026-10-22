"""Conservative assignment guard: only unchanged source excerpts carry follow-up."""

import re

from sentence_splitter import SentenceSplitter

from shared.diagnostics import event

_SENTENCES = SentenceSplitter(language="no")
_NAME = r"[A-ZÆØÅ][a-zæøåéèäöü-]+"
_ACTOR = rf"(?:{_NAME})(?:\s+(?:og|{_NAME}))*"
_ACTION = (
    r"skal|må|bør|vil|lager|lag|utarbeider|utarbeid|oppdaterer|oppdater|"
    r"undersøker|undersøk|avklarer|avklar|oppsummerer|oppsummer|"
    r"forbereder|forbered|presenterer|presenter|følger\s+opp|følg\s+opp|"
    r"ser\s+på|se\s+på|tar\s+ansvar|påtar\s+seg|jobber\s+med"
)
_ASSIGNMENT = re.compile(
    rf"\b(?P<actor>{_ACTOR})(?:\s*\([^\n)]{{1,80}}\))?\s*[:,]?\s+(?P<action>(?i:{_ACTION}))\b"
)
_RESPONSIBILITY = re.compile(r"\bansvar\w*\b", re.I)
_TASK_VERB = re.compile(
    r"\s+(?:(?:også|bare|eventuelt)\s+)?(?:lage|utarbeide|oppdatere|undersøke|"
    r"avklare|oppsummere|forberede|presentere|skrive|følge\s+opp|se\s+på)\b", re.I
)
_ROLE = re.compile(
    rf"\b{_NAME}\s+(?:(?:er|som)\s+|\()(?:prosjektleder|jurist|økonomi\w*|teknisk\s+ansvarlig)\b"
)
_TASK_TITLE = re.compile(
    r"^(?:oppgaver?|oppfølging|arbeidsfordeling|ansvarsfordeling|ansvar|ansvarlige|roller|"
    r"action items|tasks|follow.up)(?:\b|\s*[-–:])", re.I
)
_SOURCE_TITLE = "Oppfølging – kildeutsagn"
_SPEAKER = re.compile(r"^[^:\n]{1,100}:\s*")
_PLAIN_TITLE = re.compile(
    r"(?:kort oppsummering|status|bakgrunn|konklusjon|overordnet konklusjon|"
    r"åpne spørsmål|viktigste uavklarte spørsmål)", re.I
)
_GENERIC_ACTORS = set((
    "jeg vi du dere han hun de det den dem man alle noen gruppen teamet prosjektet "
    "deltakerne veilederne dette disse denne her der som så og men for nå nei ja da ikke "
    "hva hvem hvordan hvilken hvilke et en alt kanskje derfor ellers sånn testen "
    "simuleringen løsningen kostnadene samtykke piloten"
).split())

# Instructions supplement a code boundary; they are not treated as validation.
DRAFT_RULES = """\nANSVAR OG OPPFØLGING: Ikke skriv oppgaveliste, ansvarstabell eller tilordninger i friteksten.
Ikke gjett navn, roller eller taleridentiteter.
Beskriv temaer og uavklarte forhold uten å gi personer oppgaver. Ikke gjenta tilordninger i konklusjonen.
Kildeutsagn er ikke en bekreftelse på at oppgaven er vedtatt, fortsatt gjelder eller er gjennomført."""


def _heading(line):
    stripped = line.strip()
    marked = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
    if marked:
        return len(marked[1]), re.sub(r"^\d+[.)]\s*", "", marked[2])
    plain = re.sub(r"^\d+[.)]\s*", "", stripped).strip("*_ ")
    if (plain and len(plain) <= 100 and not re.search(r"[.!?]$", plain)
            and (_TASK_TITLE.match(plain) or _PLAIN_TITLE.fullmatch(plain)
                 or stripped.startswith("**") and stripped.endswith("**")
                 or re.match(r"^\d+[.)]\s", stripped))):
        return 2, plain
    return None


def _assignment_like(text):
    text = re.sub(r"[*_`]", "", text)
    return bool(_ASSIGNMENT.search(text) or _RESPONSIBILITY.search(text) or _ROLE.search(text))


def source_follow_up(source):
    """Return whole utterances, never inferred owners or paraphrased task text.

    This is deliberately a conservative candidate selector, not semantic proof of
    an accepted task. Questions and oversized utterances are omitted, not trimmed
    into apparently unconditional assignments. Source order and speaker IDs stay.
    """
    candidates, seen = [], set()
    for line in source.splitlines():
        quote = line.strip()
        utterance = _SPEAKER.sub("", quote)
        matches = list(_ASSIGNMENT.finditer(utterance))
        direct = [match for match in matches
                  if any(word.casefold() not in _GENERIC_ACTORS for word in match["actor"].split())
                  or ("jeg" in match["actor"].casefold().split()
                      and (match["action"].casefold() not in {"skal", "må", "bør", "vil"}
                           or _TASK_VERB.match(utterance, match.end())))]
        if quote and len(quote) <= 1200 and "?" not in quote and direct and quote not in seen:
            seen.add(quote)
            named = any(word.casefold() not in _GENERIC_ACTORS for match in direct for word in match["actor"].split())
            candidates.append((not named, len(candidates), quote))
    # Prefer explicit named source statements over generic "Vi skal ...", without
    # inventing a name for anonymous first-person speech or changing source order.
    selected = sorted(candidates)[:16]
    return [quote for _, _, quote in sorted(selected, key=lambda item: item[1])]


def _quote_markdown(text):
    # Render source text as text, not source-supplied links, HTML or formatting.
    return re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", text)


def _paragraphs(draft):
    pending = []
    for line in draft.splitlines():
        if not line.strip() or _heading(line):
            if pending:
                yield " ".join(pending)
                pending = []
            yield line
        else:
            if re.match(r"^\s*(?:[-*>]|\d+[.)])\s", line) and pending:
                yield " ".join(pending)
                pending = []
            pending.append(line.strip())
    if pending:
        yield " ".join(pending)


def guard_assignments(source, draft, *, comprehensive, include_source_quotes=True):
    kept, removed, blocked = [], 0, None
    for line in _paragraphs(draft):
        heading = _heading(line)
        if heading and _TASK_TITLE.match(heading[1]):
            blocked = heading[0]
            removed += 1
            continue
        if blocked is not None:
            if heading and heading[0] <= blocked:
                blocked = None
            else:
                removed += bool(line.strip())
                continue
        if heading:
            if not _assignment_like(heading[1]):
                kept.append(line)
            else:
                removed += 1
            continue
        sentences = _SENTENCES.split(line)
        retained = [sentence for sentence in sentences if not _assignment_like(sentence)]
        removed += len(sentences) - len(retained)
        kept.append(" ".join(retained))
    body = "\n".join(kept).strip()
    if not any(line.strip() and _heading(line) is None for line in kept):
        body = ""
    quotes = source_follow_up(source) if comprehensive and include_source_quotes else []
    if not body and not comprehensive and include_source_quotes:
        quotes = [quote for quote in source_follow_up(source) if len(quote.split()) <= 60][:1]
    if quotes:
        if comprehensive:
            body += ("\n\n" if body else "") + "## " + _SOURCE_TITLE + "\n\n"
        body += "\n\n".join("> " + _quote_markdown(quote) for quote in quotes)
    event("llm.assignment_guard", removed=removed, source_excerpts=len(quotes))
    return body
