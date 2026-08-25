from worker.prompts.motereferat import (
    SYSTEM_REFERAT,
    BRUKER_REFERAT,
    SYSTEM_SAMMENDRAG,
    BRUKER_SAMMENDRAG,
    SYSTEM_RULLERENDE,
    BRUKER_RULLERENDE,
)
from worker.prompts.normalisering import normaliser_til_bokmal
from worker.prompts.estimat import beregn_llm_estimat

__all__ = [
    "SYSTEM_REFERAT",
    "BRUKER_REFERAT",
    "SYSTEM_SAMMENDRAG",
    "BRUKER_SAMMENDRAG",
    "SYSTEM_RULLERENDE",
    "BRUKER_RULLERENDE",
    "normaliser_til_bokmal",
    "beregn_llm_estimat",
]
