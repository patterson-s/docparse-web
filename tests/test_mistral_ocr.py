"""No-network tests for the Mistral OCR option in the web app.

Locks: (1) the pure engine->ids/model mapping (Cohere default, Mistral option,
vendor-coupled metadata chat) in app_core, and (2) the AppTest key-field
switching — the visible API-key field must follow the chosen engine so the
right key is sent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

APP = str(Path(__file__).resolve().parents[1] / "app.py")


# ── pure engine mapping (app_core, streamlit-free) ─────────────────────────

def test_engine_mapping_cohere_default():
    from app_core import engine

    eng = engine("Cohere (default)")
    assert eng["ocr"] == "cohere-parse"
    assert eng["chat"] == "cohere"
    assert eng["chat_model"] == "command-a-03-2025"
    assert eng["vendor"] == "Cohere"


def test_engine_mapping_mistral():
    from app_core import engine

    eng = engine("Mistral")
    assert eng["ocr"] == "mistral"
    assert eng["chat"] == "mistral"
    assert eng["chat_model"] == "mistral-medium-latest"
    assert eng["vendor"] == "Mistral"


# ── widget wiring (AppTest) ────────────────────────────────────────────────

@pytest.fixture
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    for key in ("COHERE_API_KEY", "MISTRAL_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def run(**env) -> AppTest:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        return at

    return run


def test_default_engine_shows_only_cohere_key(app):
    at = app()
    assert not at.exception, at.exception
    fields = [w for w in at.text_input if "API key" in w.label]
    assert len(fields) == 1
    assert "Cohere" in fields[0].label
    assert "Mistral" not in fields[0].label


def test_selecting_mistral_shows_only_mistral_key(app):
    at = app()
    assert not at.exception, at.exception
    engine = [r for r in at.radio if "Cohere (default)" in r.options][0]
    engine.set_value("Mistral").run()
    assert not at.exception, at.exception
    fields = [w for w in at.text_input if "API key" in w.label]
    assert len(fields) == 1
    assert "Mistral" in fields[0].label
    assert "Cohere" not in fields[0].label


def test_mistral_parse_without_key_warns_mistral(app):
    at = app()
    assert not at.exception, at.exception
    engine = [r for r in at.radio if "Cohere (default)" in r.options][0]
    engine.set_value("Mistral").run()
    btn = [b for b in at.button if b.label == "Parse documents"][0]
    at = btn.click().run()
    assert not at.exception, at.exception
    assert any("Enter your Mistral API key" in w.value for w in at.warning)
