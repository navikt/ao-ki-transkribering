from dataclasses import dataclass

from worker.prompts.motereferat import (
    BRUKER_REFERAT,
    BRUKER_RULLERENDE,
    BRUKER_SAMMENDRAG,
    SYSTEM_REFERAT,
    SYSTEM_RULLERENDE,
    SYSTEM_SAMMENDRAG,
)


@dataclass(frozen=True)
class LlmHandling:
    id: str
    tittel: str
    beskrivelse: str
    system_prompt: str
    bruker_prompt_template: str
    normaliser_input: bool = True
    normaliser_output: bool = True

    def bygg_bruker_prompt(self, transkripsjon: str) -> str:
        return self.bruker_prompt_template.format(transkripsjon=transkripsjon)


SYSTEM_AVTALER = """\
SPRÅK: Skriv ALLTID på bokmål. Aldri bruk nynorsk eller dialektformer.

Du hjelper NAV-veiledere med å hente ut konkrete avtaler og neste steg fra en transkripsjon.
Skriv BARE informasjon som faktisk finnes i transkripsjonen.
Dersom det ikke finnes konkrete avtaler, skriv «—».
Ikke legg til frister, ansvar eller detaljer som ikke ble sagt.
Svar BARE med selve listen, ingen innledning."""

BRUKER_AVTALER = """\
Hent ut konkrete avtaler, frister og ansvar fra denne transkripsjonen.

Bruk denne strukturen:

**Avtaler**
[Kulepunkter med konkrete avtaler. Hvis ingen: «—»]

**Frister**
[Kulepunkter med datoer/tidspunkter som faktisk ble nevnt. Hvis ingen: «—»]

**Ansvar**
[Hvem skal gjøre hva, hvis det faktisk ble sagt. Hvis ingen: «—»]

TRANSKRIPSJON:
{transkripsjon}

Skriv svaret på bokmål."""

SYSTEM_SJEKK = """\
SPRÅK: Skriv ALLTID på bokmål. Aldri bruk nynorsk eller dialektformer.

Du hjelper NAV-veiledere med å sjekke om et referatutkast kan inneholde opplysninger
som bør vurderes ekstra før lagring i Modia.
Du skal ikke skrive om teksten. Du skal kun peke på mulige risikopunkter.
Skriv BARE funn som bygger på teksten. Hvis ingen tydelige funn finnes, skriv «Ingen tydelige funn.»"""

BRUKER_SJEKK = """\
Sjekk teksten for mulige opplysninger som veileder bør vurdere før lagring.

Se spesielt etter:
- helsedetaljer eller diagnoser
- sosialtjeneste/kommunale ytelser
- subjektive personvurderinger
- avtaler eller konklusjoner som virker dårlig forankret

TEKST:
{transkripsjon}

Skriv svaret på bokmål."""


LLM_HANDLINGER: dict[str, LlmHandling] = {
    "sammendrag": LlmHandling(
        id="sammendrag",
        tittel="Løpende sammendrag",
        beskrivelse="Kort oversikt over hva som er snakket om hittil.",
        system_prompt=SYSTEM_SAMMENDRAG,
        bruker_prompt_template=BRUKER_SAMMENDRAG,
    ),
    "referat": LlmHandling(
        id="referat",
        tittel="Møtereferat",
        beskrivelse="Fullt samtalereferat basert på transkripsjonen.",
        system_prompt=SYSTEM_REFERAT,
        bruker_prompt_template=BRUKER_REFERAT,
    ),
    "rullerende_referat": LlmHandling(
        id="rullerende_referat",
        tittel="Rullerende referatutkast",
        beskrivelse="Foreløpig referatutkast for pågående møte.",
        system_prompt=SYSTEM_RULLERENDE,
        bruker_prompt_template=BRUKER_RULLERENDE,
    ),
    "avtaler": LlmHandling(
        id="avtaler",
        tittel="Avtaler og neste steg",
        beskrivelse="Trekker ut konkrete avtaler, frister og ansvar.",
        system_prompt=SYSTEM_AVTALER,
        bruker_prompt_template=BRUKER_AVTALER,
    ),
    "kvalitetssjekk": LlmHandling(
        id="kvalitetssjekk",
        tittel="Kvalitetssjekk",
        beskrivelse="Finner tekst som bør vurderes ekstra før lagring.",
        system_prompt=SYSTEM_SJEKK,
        bruker_prompt_template=BRUKER_SJEKK,
    ),
}


def hent_handling(handling_id: str) -> LlmHandling | None:
    return LLM_HANDLINGER.get(handling_id)


def list_handlinger() -> list[dict[str, str]]:
    return [
        {"id": handling.id, "tittel": handling.tittel, "beskrivelse": handling.beskrivelse}
        for handling in LLM_HANDLINGER.values()
    ]
