"""Released prompt definitions are immutable; change wording in a new version."""

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
from types import MappingProxyType
import tomllib


@lru_cache(maxsize=None)
def load_definition(name):
    """Load only named, packaged versions; malformed definitions fail at startup."""
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError("Invalid prompt definition name")
    path = Path(__file__).parent / (name + ".toml")
    with path.open("rb") as source:
        data = tomllib.load(source)
    for key in ("id", "title", "version", "mode"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Invalid {key} in prompt definition {name}")
    if data["id"] != name:
        raise ValueError(f"Prompt definition ID does not match filename: {name}")
    for key in ("system_prompt", "user_prompt"):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"Invalid {key} in prompt definition {name}")
    if "max_tokens" in data and (type(data["max_tokens"]) is not int or data["max_tokens"] <= 0):
        raise ValueError(f"Invalid max_tokens in prompt definition {name}")
    if "repeat_penalty" in data and (type(data["repeat_penalty"]) not in {float, int}
            or not math.isfinite(data["repeat_penalty"]) or not 1 <= data["repeat_penalty"] <= 2):
        raise ValueError(f"Invalid repeat_penalty in prompt definition {name}")
    if "texts" not in data and not {"system_prompt", "user_prompt"} <= data.keys():
        raise ValueError(f"Missing prompt text in definition {name}")
    if "texts" in data:
        if not isinstance(data["texts"], dict) or not all(isinstance(value, str) for value in data["texts"].values()):
            raise ValueError(f"Invalid text fragments in prompt definition {name}")
        data["texts"] = MappingProxyType(data["texts"])
    return MappingProxyType(data)


def load_texts(name):
    return load_definition(name)["texts"]


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
    based_on: str | None = None
    repeat_penalty: float | None = None

    def metadata(self):
        return {"id": self.id, "title": self.title, "version": self.version, "mode": self.mode,
                **({"based_on": self.based_on} if self.based_on else {})}


BASIS_0_1 = PromptVersion(**load_definition("basis_0_1"))
STIKKORD_0_1 = PromptVersion(**load_definition("stikkord_0_1"))
UTFYLLENDE_0_1 = PromptVersion(**load_definition("utfyllende_0_1"))
BASIS_0_2 = PromptVersion(**load_definition("basis_0_2"))
STIKKORD_0_2 = PromptVersion(**load_definition("stikkord_0_2"))
UTFYLLENDE_0_2 = PromptVersion(**load_definition("utfyllende_0_2"))
CURRENT_PROMPTS = (STIKKORD_0_2, BASIS_0_2, UTFYLLENDE_0_2)
