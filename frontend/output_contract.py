"""Structural checks for the released 0.3 text, not factual verification."""

import re

from shared.diagnostics import event

from shared.services.llm_proxy_client import IncompleteModelResponse
from worker.prompts.versions import BASIS_0_3, STIKKORD_0_3, UTFYLLENDE_0_3

HEADINGS = (
    "1. Bakgrunn",
    "2. Det du fortalte",
    "3. Det veilederen informerte om",
    "4. Avtaler",
    "5. Ikke avtalt ennå",
)
ABSENT_AGREEMENTS = ("Ingen avtaler er oppgitt.", "Det er uklart om noe ble avtalt.")
CORRECTION = (
    "\n\nLag referatet på nytt fra kildedataene. Det forrige forsøket kunne ikke brukes. "
    "Følg de fem overskriftene og fyll alle seksjonene. Under Avtaler brukes bare "
    "«- Hvem: aktør | Hva: handling | Når: tidspunkt», én avtale per linje, "
    "eller «Ingen avtaler er oppgitt.» eller «Det er uklart om noe ble avtalt.». "
    "Ikke gjett manglende opplysninger. Fullfør alle fem seksjonene innen svargrensen."
)
_BULLET = re.compile(r"- Hvem: ([^|\n]+) \| Hva: ([^|\n]+) \| Når: ([^|\n]+)")
_HEADING = re.compile(r"(?:#{1,6}\s|[0-9]+\.\s)")
_RELEASES = (STIKKORD_0_3, BASIS_0_3, UTFYLLENDE_0_3)


class FinalOutputError(IncompleteModelResponse):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Modellen ga ikke et fullstendig referat med påkrevd struktur etter to forsøk. Prøv igjen.")


def released_contract(profile, overrides) -> bool:
    if profile not in _RELEASES:
        return False
    return overrides is None or overrides.system_prompt is None or (
        overrides.system_prompt == profile.system_prompt and overrides.user_prompt == profile.user_prompt
    )


def invalid_reason(text: str) -> str | None:
    if not text.strip():
        return "empty"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [line for line in lines if _HEADING.match(line)]
    if headings != list(HEADINGS) or lines[0] != HEADINGS[0]:
        return "headings"
    positions = [lines.index(heading) for heading in HEADINGS] + [len(lines)]
    sections = [lines[start + 1:end] for start, end in zip(positions, positions[1:])]
    if any(not section for section in sections):
        return "empty_section"
    agreements = sections[3]
    if len(agreements) == 1 and agreements[0] in ABSENT_AGREEMENTS:
        return None
    for line in agreements:
        match = _BULLET.fullmatch(line)
        if match is None or any(not field.strip() for field in match.groups()):
            return "agreement_fields"
    return None


class OutputAttempts:
    """Keep the one corrective attempt across context-window fallbacks."""

    def __init__(self):
        self.failures = 0

    async def final(self, system, user, model, call, options):
        while True:
            try:
                result = await call(system + (CORRECTION if self.failures else ""), user, model, **options)
                reason = invalid_reason(result)
            except IncompleteModelResponse:
                reason = "incomplete"
            if reason is None:
                return result
            self.failures += 1
            event("llm.output_rejected", attempt=self.failures, reason=reason, model=model)
            if self.failures >= 2:
                raise FinalOutputError(reason) from None
