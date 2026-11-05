"""Compatibility exports; prompt wording lives in the versioned TOML files."""

from worker.prompts.versions import load_definition, load_texts

SYSTEM_VEILEDER_BASIS = load_texts("meeting_fragments_0_1")["SYSTEM_VEILEDER_BASIS"]

SYSTEM_VEILEDERSAMTALE = load_definition("veiledermote_0_1")["system_prompt"]

VEILEDER_KARTLEGGING = load_texts("meeting_fragments_0_1")["VEILEDER_KARTLEGGING"]

VEILEDER_OPPFOLGING = load_texts("meeting_fragments_0_1")["VEILEDER_OPPFOLGING"]

BRUKER_VEILEDERSAMTALE = load_definition("veiledermote_0_1")["user_prompt"]

SYSTEM_REFERAT = load_definition("referat_14a_0_1")["system_prompt"]

BRUKER_REFERAT = load_definition("referat_14a_0_1")["user_prompt"]

SYSTEM_GENERELT_MOTE = load_definition("generelt_mote_0_1")["system_prompt"]

BRUKER_GENERELT_MOTE = load_definition("generelt_mote_0_1")["user_prompt"]

SAMMENDRAG_STIL = load_texts("meeting_fragments_0_1")["SAMMENDRAG_STIL"]

SYSTEM_MINIMALT_SAMMENDRAG = load_definition("minimalt_sammendrag_0_1")["system_prompt"]

BRUKER_MINIMALT_SAMMENDRAG = load_definition("minimalt_sammendrag_0_1")["user_prompt"]

SYSTEM_UTFYLLENDE_SAMMENDRAG = load_definition("utfyllende_sammendrag_0_1")["system_prompt"]

BRUKER_UTFYLLENDE_SAMMENDRAG = load_definition("utfyllende_sammendrag_0_1")["user_prompt"]

SYSTEM_SAMARBEIDSMOTE = load_definition("samarbeidsmote_0_1")["system_prompt"]

BRUKER_SAMARBEIDSMOTE = load_definition("samarbeidsmote_0_1")["user_prompt"]

SYSTEM_SAMMENDRAG = load_definition("sammendrag_0_1")["system_prompt"]

BRUKER_SAMMENDRAG = load_definition("sammendrag_0_1")["user_prompt"]

SYSTEM_RULLERENDE = load_definition("rullerende_referat_0_1")["system_prompt"]

BRUKER_RULLERENDE = load_definition("rullerende_referat_0_1")["user_prompt"]
