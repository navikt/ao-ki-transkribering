"""Released prompt definitions are immutable; change wording in a new version."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class PromptVersion:
    id: str
    title: str
    version: str
    mode: str
    model: str
    cloud_model: str
    temperature: float
    top_p: float
    num_ctx: int
    max_tokens: int
    system_prompt: str
    user_prompt: str

    def metadata(self):
        return {"id": self.id, "title": self.title, "version": self.version, "mode": self.mode}


BASIS_0_1 = PromptVersion(**tomllib.loads((Path(__file__).parent / "basis_0_1.toml").read_text()))
