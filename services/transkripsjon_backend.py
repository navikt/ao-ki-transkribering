from pathlib import Path
from typing import Any, Callable, Protocol

from kontrakter.transkripsjon import TranskripsjonSvar


StatusCallback = Callable[[dict[str, Any]], None]


class TranskripsjonBackend(Protocol):
    """Contract for model-backed transcription implementations."""

    modell_id: str
    enhet: str

    def transkriber(
        self,
        lydfil: Path,
        *,
        n_talere: int = 0,
        status_callback: StatusCallback | None = None,
    ) -> TranskripsjonSvar:
        """Transcribe an audio file and return text, segments and optional warnings."""
