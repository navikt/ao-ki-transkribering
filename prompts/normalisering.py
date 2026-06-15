import re

_NYNORSK_BOKMAL = [
    # Pronomen og determinativer
    (r"\bikkje\b",          "ikke"),
    (r"\bein\b",            "en"),
    (r"\beine\b",           "ene"),
    (r"\beit\b",            "et"),
    (r"\bho\b",             "hun"),
    (r"\bdei\b",            "de"),
    (r"\bdeira\b",          "deres"),
    (r"\bme\b",             "vi"),
    (r"\bkva\b",            "hva"),
    (r"\bnoko\b",           "noe"),
    (r"\bnokon\b",          "noen"),
    (r"\binga\b",           "ingen"),
    (r"\bnokre\b",          "noen"),
    # Preposisjoner og adverb
    (r"\bfrå\b",            "fra"),
    (r"\bhjå\b",            "hos"),
    (r"\bpå grunn av\b",    "på grunn av"),
    (r"\bnår\b",            "når"),
    # Verb – infinitiv
    (r"\bvere\b",           "være"),
    (r"\bgjere\b",          "gjøre"),
    (r"\bseie\b",           "si"),
    (r"\bseia\b",           "si"),
    (r"\bsjå\b",            "se"),
    (r"\bkome\b",           "komme"),
    (r"\bgje\b",            "gi"),
    (r"\bta\b",             "ta"),
    (r"\bsøkje\b",          "søke"),
    (r"\bønskje\b",         "ønske"),
    (r"\btrengje\b",        "trenge"),
    # Verb – presens
    (r"\bseier\b",          "sier"),
    (r"\bgjer\b",           "gjør"),
    (r"\bkjem\b",           "kommer"),
    (r"\bveit\b",           "vet"),
    (r"\bsegjer\b",         "sier"),
    (r"\btenkjer\b",        "tenker"),
    (r"\bsøkjer\b",         "søker"),
    (r"\bønskjer\b",        "ønsker"),
    (r"\btrengst\b",        "trengs"),
    (r"\btreng\b",          "trenger"),
    (r"\bmøtest\b",         "møtes"),
    # Verb – preteritum / perfektum
    (r"\bsnakka\b",         "snakket"),
    (r"\bjobba\b",          "jobbet"),
    (r"\barbeida\b",        "arbeidet"),
    (r"\bhandla\b",         "handlet"),
    (r"\bavtala\b",         "avtalt"),
    (r"\bopna\b",           "åpnet"),
    (r"\bbrukte\b",         "brukte"),
    # Verb – passiv / infinitiv m/a
    (r"\btrappast\b",       "trappes"),
    (r"\bbehøvast\b",       "behøves"),
    # Substantiv – bestemte former med -a ending
    (r"\bbehandlinga\b",    "behandlingen"),
    (r"\butgreiinga\b",     "utredningen"),
    (r"\bforskinga\b",      "forskningen"),
    (r"\bvurderinga\b",     "vurderingen"),
    (r"\bavtalinga\b",      "avtalen"),
    (r"\boldinga\b",        "holdingen"),
    (r"\bsamtala\b",        "samtalen"),
    (r"\btida\b",           "tiden"),
    (r"\brapporten\b",      "rapporten"),
    # Substantiv og adjektiv
    (r"\bbrukar\b",         "bruker"),
    (r"\bbrukarar\b",       "brukere"),
    (r"\bbrukaren\b",       "brukeren"),
    (r"\brettleiar\b",      "veileder"),
    (r"\brettleiarar\b",    "veiledere"),
    (r"\brettleiaren\b",    "veilederen"),
    (r"\btilskot\b",        "tilskudd"),
    (r"\bhøgare\b",         "høyere"),
    (r"\btilbodet\b",       "tilbudet"),
    (r"\bnoko å seie\b",    "noe å si"),
    (r"\bnoko\b",           "noe"),
]


def _strip_think_blokkar(tekst: str) -> str:
    """Fjernar <think>...</think>-blokkar utan regex-backtracking."""
    while True:
        start = tekst.find("<think>")
        if start == -1:
            break
        end = tekst.find("</think>", start)
        if end == -1:
            # Opa <think>-tag utan lukking — fjern frå <think> til slutten
            tekst = tekst[:start]
            break
        tekst = tekst[:start] + tekst[end + len("</think>"):]
    return tekst.strip()


def normaliser_til_bokmal(tekst: str) -> str:
    """Erstatter kjente nynorsk-former med bokmål i LLM-output.

    Striper også <think>...</think>-blokker som qwen3-modellar
    kan sende sjølv med think=False.
    """
    tekst = _strip_think_blokkar(tekst)
    for mønster, erstatning in _NYNORSK_BOKMAL:
        def _bytt(m: re.Match, repl: str = erstatning) -> str:
            s = m.group(0)
            return repl[0].upper() + repl[1:] if s[0].isupper() else repl
        tekst = re.sub(mønster, _bytt, tekst, flags=re.IGNORECASE)
    return tekst
