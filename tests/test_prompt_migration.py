"""Compatibility snapshots captured before extracting the Python prompt strings."""

import hashlib
from importlib import import_module
from pathlib import Path
import tomllib

import pytest

from frontend.routes.referat import _bygg_referat_prompt
from shared.services.llm_proxy_client import LlmForesporsel
from worker.prompts.versions import load_definition, load_texts
from worker.prompts import versions

DEFINITIONS = Path(__file__).parents[1] / "worker/prompts/versions"
RELEASE = tomllib.loads((Path(__file__).with_name("prompt_migration_snapshot.toml")).read_text())
SNAPSHOT = RELEASE["sha256"]


@pytest.mark.parametrize("key,expected", SNAPSHOT.items())
def test_migrated_prompts_preserve_exact_text(key, expected):
    if key.startswith("route:"):
        _, scenario, live, variant, restricted = key.split(":")
        request = LlmForesporsel(transkripsjon="Taler 1: Hei {navn}.\nVi avtaler ingenting.", scenario=scenario,
            instruksjoner="Bevar {klammer}." if scenario == "egendefinert" else None,
            veileder_variant=None if variant == "None" else variant, begrenset_referat=restricted == "True")
        system, user, _ = _bygg_referat_prompt(request, rullerende=live == "True")
        text = system + "\0" + user
    elif key.startswith("draft_rules:"):
        from frontend.grounded_summary import _draft_rules
        text = _draft_rules("ord " * int(key.split(":")[1]))
    else:
        module, name = key.split(":")
        text = getattr(import_module(module), name)
    assert hashlib.sha256(text.encode()).hexdigest() == expected


@pytest.mark.parametrize("name,expected", RELEASE["definitions"].items())
def test_released_toml_definitions_keep_their_checksum(name, expected):
    assert hashlib.sha256((DEFINITIONS / name).read_bytes()).hexdigest() == expected


def test_migrating_text_does_not_change_default_or_opt_legacy_prompts_into_basis_mode():
    from worker.prompts.handlinger import REFERAT_SCENARIER, STANDARD_REFERAT_SCENARIO
    assert STANDARD_REFERAT_SCENARIO == "basis_0_1"
    current = {"stikkord_0_1", "basis_0_1", "utfyllende_0_1"}
    assert {name for name, scenario in REFERAT_SCENARIER.items() if scenario.profile} == current
    assert all(scenario.profile is None for name, scenario in REFERAT_SCENARIER.items() if name not in current)
    assert REFERAT_SCENARIER["utfyllende_sammendrag"].max_tokens == 3072
    assert REFERAT_SCENARIER["veiledermote"].max_tokens == 2048


@pytest.mark.parametrize("path", sorted((Path(__file__).parents[1] / "worker/prompts/versions").glob("*.toml")))
def test_every_prompt_definition_loads_and_is_read_only(path):
    definition = load_definition(path.stem)
    assert definition["id"] == path.stem
    assert definition["version"]
    assert "texts" in definition or {"system_prompt", "user_prompt"} <= definition.keys()
    with pytest.raises(TypeError):
        definition["version"] = "changed"
    if "texts" in definition:
        with pytest.raises(TypeError):
            load_texts(path.stem)["new"] = "changed"


@pytest.mark.parametrize("name", ["../basis_0_1", "/basis_0_1", "basis_0_1.toml", "", None])
def test_prompt_loader_rejects_paths_and_invalid_names(name):
    with pytest.raises(ValueError, match="Invalid prompt definition name"):
        load_definition(name)


@pytest.mark.parametrize("body", [
    'system_prompt = 42\nuser_prompt = "tekst"',
    'system_prompt = "tekst"\nuser_prompt = "tekst"\nmax_tokens = -1',
    'system_prompt = "tekst"\nuser_prompt = "tekst"\nmax_tokens = true',
    '[texts]\nbroken = 42',
    '',
])
def test_prompt_loader_rejects_malformed_definitions(monkeypatch, tmp_path, body):
    name = "invalid_migration_test"
    (tmp_path / (name + ".toml")).write_text(
        f'id = "{name}"\ntitle = "Test"\nversion = "0.1"\nmode = "test"\n' + body)
    monkeypatch.setattr(versions, "__file__", str(tmp_path / "__init__.py"))
    with pytest.raises(ValueError):
        load_definition(name)
