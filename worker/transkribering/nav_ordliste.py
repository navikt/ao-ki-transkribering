import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NavOrdgruppe:
    navn: str
    ord: tuple[str, ...]


NAV_ORDGRUPPER: tuple[NavOrdgruppe, ...] = (
    NavOrdgruppe(
        "ytelser",
        (
            "AAP",
            "arbeidsavklaringspenger",
            "dagpenger",
            "sykepenger",
            "uføretrygd",
            "tiltakspenger",
            "sosialhjelp",
            "økonomisk sosialhjelp",
        ),
    ),
    NavOrdgruppe(
        "systemer_og_tjenester",
        (
            "NAV",
            "nav.no",
            "Arbeidsplassen",
            "arbeidsplassen.no",
            "Aktivitetsplanen",
            "Modia",
            "meldekort",
            "dialogboksen",
        ),
    ),
    NavOrdgruppe(
        "oppfolging_og_tiltak",
        (
            "arbeidsevnevurdering",
            "aktivitetsplan",
            "oppfølgingsvedtak",
            "arbeidsrettet oppfølging",
            "arbeidsrettede tiltak",
            "lønnstilskudd",
            "arbeidspraksis",
            "kvalifiseringsprogrammet",
        ),
    ),
    NavOrdgruppe(
        "regelverk",
        (
            "nav-loven §14a",
            "nav-loven §15",
            "sosialtjenesteloven",
            "folketrygdloven",
        ),
    ),
)


_KORREKSJONER: tuple[tuple[str, str], ...] = (
    (r"\bnavn\.no\b", "nav.no"),
    (r"\bnav punktum no\b", "nav.no"),
    (r"\barbeidsplassen punktum no\b", "arbeidsplassen.no"),
    (r"\ba\s*a\s*p\b", "AAP"),
    (r"\barbeidsavklarings penger\b", "arbeidsavklaringspenger"),
    (r"\barbeidsavklaring penger\b", "arbeidsavklaringspenger"),
    (r"\barbeidstakingspenger\b", "arbeidsavklaringspenger"),
    (r"\barbeidstakernes penger\b", "arbeidsavklaringspenger"),
    (r"\barbeidsdagskjøningspenger\b", "arbeidsavklaringspenger"),
    (r"\baktivitets planen\b", "Aktivitetsplanen"),
    (r"\bmodia\b", "Modia"),
    (r"\bnokut\b", "NOKUT"),
)


def _erstatt_med_case(match: re.Match, erstatning: str) -> str:
    funn = match.group(0)
    if erstatning.isupper() or any(tegn.isupper() for tegn in erstatning[1:]):
        return erstatning
    if funn[:1].isupper():
        return erstatning[:1].upper() + erstatning[1:]
    return erstatning


def normaliser_nav_ord(tekst: str) -> str:
    """Retter kjente NAV-termer og vanlige ASR-feil uten fuzzy matching."""
    for monster, erstatning in _KORREKSJONER:
        tekst = re.sub(
            monster,
            lambda match, repl=erstatning: _erstatt_med_case(match, repl),
            tekst,
            flags=re.IGNORECASE,
        )
    return tekst


def nav_ordliste_prompt() -> str:
    """Kort, promptvennlig ordliste for ASR/LLM-kontekst."""
    linjer = []
    for gruppe in NAV_ORDGRUPPER:
        linjer.append(f"{gruppe.navn}: {', '.join(gruppe.ord)}")
    return "\n".join(linjer)
