from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs
from importlib import import_module

from fastapi.testclient import TestClient
import pytest

frontend_app = import_module("frontend.app")


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.selects = {}
        self.assets = []
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "select":
            self.current = attrs.get("id")
            self.selects[self.current] = {"attrs": attrs, "options": []}
        elif tag == "option" and self.current:
            self.selects[self.current]["options"].append(attrs)
        if tag == "script" and attrs.get("src", "").startswith("/static/"):
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("href", "").startswith("/static/"):
            self.assets.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "select":
            self.current = None


@pytest.mark.parametrize("local", [False, True])
def test_homepage_models_are_selectable_without_a_status_request(monkeypatch, local):
    monkeypatch.delenv("NAIS_CLUSTER_NAME", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setattr(frontend_app, "TRANSKRIPSJON_BACKEND", "local" if local else "remote")
    monkeypatch.setattr(frontend_app, "TRANSKRIPSJON_MODELL", "nb-whisper-large")
    monkeypatch.setattr(frontend_app, "LLM_BACKEND", "ollama" if local else "openai")
    monkeypatch.setattr(frontend_app, "LLM_MODELL", "borealis-12b")
    # The served page must work before JS or the gateway status request runs.
    response = TestClient(frontend_app.create_app()).get("/")
    assert response.status_code == 200
    page = Page(response.text)
    for name in ("transkripsjon-modell", "referat-modell"):
        selector = page.selects[name]
        assert ("disabled" in selector["attrs"]) == local
        assert len(selector["options"]) == (1 if local else 2)
        assert selector["attrs"]["data-choices"] == ("single" if local else "multiple")
        assert any("selected" in option for option in selector["options"])
    assert page.assets
    assert all(parse_qs(urlsplit(asset).query).get("v") for asset in page.assets)
    assert response.headers["cache-control"] == "no-cache"


def test_asset_version_changes_when_javascript_changes(tmp_path):
    (tmp_path / "index.html").write_text('<script src="/static/app.js?v=$asset_version"></script>')
    (tmp_path / "styles.css").write_text("body {}")
    script = tmp_path / "app.js"
    script.write_text("const version = 1;")
    first = frontend_app._render_index(tmp_path)
    assert frontend_app._render_index(tmp_path) == first
    script.write_text("const version = 2;")
    assert frontend_app._render_index(tmp_path) != first


def test_configured_model_names_are_html_escaped(monkeypatch):
    monkeypatch.delenv("NAIS_CLUSTER_NAME", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    model = '\"><script>unsafe()</script>'
    monkeypatch.setattr(frontend_app, "TRANSKRIPSJON_BACKEND", "local")
    monkeypatch.setattr(frontend_app, "TRANSKRIPSJON_MODELL", model)
    response = TestClient(frontend_app.create_app()).get("/")
    page = Page(response.text)
    assert page.selects["transkripsjon-modell"]["options"][0]["value"] == model
    assert '<script>unsafe()' not in response.text
