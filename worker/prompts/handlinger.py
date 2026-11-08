from dataclasses import dataclass
from worker.prompts.versions import (
    BASIS_0_1, STIKKORD_0_1, UTFYLLENDE_0_1, BASIS_0_2, CURRENT_PROMPTS, PromptVersion, load_definition,
)

from worker.prompts.motereferat import (
    BRUKER_GENERELT_MOTE,
    BRUKER_MINIMALT_SAMMENDRAG,
    BRUKER_UTFYLLENDE_SAMMENDRAG,
    BRUKER_REFERAT,
    BRUKER_RULLERENDE,
    BRUKER_SAMMENDRAG,
    BRUKER_SAMARBEIDSMOTE,
    SYSTEM_GENERELT_MOTE,
    SYSTEM_MINIMALT_SAMMENDRAG,
    SYSTEM_UTFYLLENDE_SAMMENDRAG,
    SYSTEM_REFERAT,
    SYSTEM_RULLERENDE,
    SYSTEM_SAMMENDRAG,
    SYSTEM_SAMARBEIDSMOTE,
    SYSTEM_VEILEDERSAMTALE,
    BRUKER_VEILEDERSAMTALE,
    VEILEDER_KARTLEGGING,
    VEILEDER_OPPFOLGING,
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


@dataclass(frozen=True)
class ReferatScenario:
    id: str
    tittel: str
    beskrivelse: str
    system_prompt: str
    bruker_prompt_template: str
    max_tokens: int | None = None
    profile: PromptVersion | None = None

    def bygg_bruker_prompt(self, transkripsjon: str) -> str:
        return self.bruker_prompt_template.format(transkripsjon=transkripsjon)


STANDARD_REFERAT_SCENARIO = BASIS_0_2.id
STANDARD_VEILEDER_VARIANT = "oppfolging"
VEILEDER_VARIANTER = {
    "kartlegging": {
        "tittel": "Kartlegging",
        "beskrivelse": "Situasjon, erfaring, interesser, ønsker og videre utforsking.",
        "instruksjoner": VEILEDER_KARTLEGGING,
    },
    "oppfolging": {
        "tittel": "Oppfølging",
        "beskrivelse": "Status, erfaringer, utvikling og videre oppfølging.",
        "instruksjoner": VEILEDER_OPPFOLGING,
    },
}

REFERAT_SCENARIER: dict[str, ReferatScenario] = {
    **{profile.id: ReferatScenario(
        id=profile.id, tittel=profile.title,
        beskrivelse={"stikkord_0_2": "Korte kulepunkter og stikkord.",
                     "basis_0_2": "Kort prosa med kulepunkter for avtaler.",
                     "utfyllende_0_2": "Utfyllende prosa med kulepunkter for avtaler."}[profile.id],
        system_prompt=profile.system_prompt, bruker_prompt_template=profile.user_prompt,
        max_tokens=profile.max_tokens, profile=profile,
    ) for profile in CURRENT_PROMPTS},
    STIKKORD_0_1.id: ReferatScenario(
        id=STIKKORD_0_1.id, tittel=STIKKORD_0_1.title,
        beskrivelse="Korte stikkord under de fem overskriftene, inntil 120 ord.",
        system_prompt=STIKKORD_0_1.system_prompt, bruker_prompt_template=STIKKORD_0_1.user_prompt,
        max_tokens=STIKKORD_0_1.max_tokens, profile=STIKKORD_0_1,
    ),
    BASIS_0_1.id: ReferatScenario(
        id=BASIS_0_1.id, tittel=BASIS_0_1.title,
        beskrivelse="Kort samtalesammendrag som kan leses opp for brukeren.",
        system_prompt=BASIS_0_1.system_prompt, bruker_prompt_template=BASIS_0_1.user_prompt,
        max_tokens=BASIS_0_1.max_tokens, profile=BASIS_0_1,
    ),
    UTFYLLENDE_0_1.id: ReferatScenario(
        id=UTFYLLENDE_0_1.id, tittel=UTFYLLENDE_0_1.title,
        beskrivelse="Utfyllende prosa med relevant kontekst, detaljer, forbehold og oppfølging.",
        system_prompt=UTFYLLENDE_0_1.system_prompt, bruker_prompt_template=UTFYLLENDE_0_1.user_prompt,
        max_tokens=UTFYLLENDE_0_1.max_tokens, profile=UTFYLLENDE_0_1,
    ),
    "minimalt_sammendrag": ReferatScenario(
        id="minimalt_sammendrag",
        tittel="Minimalt sammendrag",
        beskrivelse="Maks tre korte punkter og 60 ord med hovedpoeng, beslutning og neste steg.",
        system_prompt=SYSTEM_MINIMALT_SAMMENDRAG,
        bruker_prompt_template=BRUKER_MINIMALT_SAMMENDRAG,
    ),
    "utfyllende_sammendrag": ReferatScenario(
        id="utfyllende_sammendrag",
        tittel="Utfyllende sammendrag",
        beskrivelse="Tematisk møtereferat med kort oversikt, detaljerte temaer, oppgaver, åpne spørsmål og konklusjon.",
        system_prompt=SYSTEM_UTFYLLENDE_SAMMENDRAG,
        bruker_prompt_template=BRUKER_UTFYLLENDE_SAMMENDRAG,
        max_tokens=load_definition("utfyllende_sammendrag_0_1")["max_tokens"],
    ),
    "veiledermote": ReferatScenario(
        id="veiledermote",
        tittel="Veiledermøte",
        beskrivelse="Samtalereferat i vi/du-form med situasjon, ønsker, usikkerhet og oppfølging.",
        system_prompt=SYSTEM_VEILEDERSAMTALE,
        bruker_prompt_template=BRUKER_VEILEDERSAMTALE,
        max_tokens=load_definition("veiledermote_0_1")["max_tokens"],
    ),
    "generelt_mote": ReferatScenario(
        id="generelt_mote",
        tittel="Generelt møte",
        beskrivelse="Nøytralt møtereferat med tema, punkter, beslutninger og oppfølging.",
        system_prompt=SYSTEM_GENERELT_MOTE,
        bruker_prompt_template=BRUKER_GENERELT_MOTE,
    ),
    "samarbeidsmote": ReferatScenario(
        id="samarbeidsmote",
        tittel="Samarbeidsmøte",
        beskrivelse="Referat for møter med flere deltakere, roller, ansvar og oppfølging.",
        system_prompt=SYSTEM_SAMARBEIDSMOTE,
        bruker_prompt_template=BRUKER_SAMARBEIDSMOTE,
    ),
    "egendefinert": ReferatScenario(
        id="egendefinert",
        tittel="Egendefinert",
        beskrivelse="Egne instruksjoner for struktur og vektlegging av referatet.",
        system_prompt=SYSTEM_GENERELT_MOTE,
        bruker_prompt_template=BRUKER_GENERELT_MOTE,
    ),
}


SYSTEM_AVTALER = load_definition("avtaler_0_1")["system_prompt"]

BRUKER_AVTALER = load_definition("avtaler_0_1")["user_prompt"]

SYSTEM_SJEKK = load_definition("kvalitetssjekk_0_1")["system_prompt"]

BRUKER_SJEKK = load_definition("kvalitetssjekk_0_1")["user_prompt"]


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


def hent_referat_scenario(scenario_id: str | None) -> ReferatScenario | None:
    return REFERAT_SCENARIER.get(scenario_id or STANDARD_REFERAT_SCENARIO)


def list_referat_scenarier() -> list[dict]:
    return [
        {
            "id": scenario.id,
            "tittel": scenario.tittel,
            "beskrivelse": scenario.beskrivelse,
            "standard": scenario.id == STANDARD_REFERAT_SCENARIO,
            "current": scenario.id in {profile.id for profile in CURRENT_PROMPTS},
            **({"prompt": scenario.profile.metadata()} if scenario.profile else {}),
            **({"varianter": [
                {"id": key, "tittel": value["tittel"], "beskrivelse": value["beskrivelse"],
                 "standard": key == STANDARD_VEILEDER_VARIANT}
                for key, value in VEILEDER_VARIANTER.items()
            ]} if scenario.id == "veiledermote" else {}),
        }
        for scenario in REFERAT_SCENARIER.values()
    ]
