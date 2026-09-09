"""Cohere-only web app smoke tests (AppTest executes app.py — no network).

The app is deliberately minimal: fixed Cohere Parse (parse-v5.0) for extraction,
fixed command-a-03-2025 for metadata, one user-supplied Cohere API key. There is
no provider selector, no model selector, and no OCR-method radio.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(monkeypatch):
    """Run app.py under a controlled environment (no ambient keys)."""
    for key in ("COHERE_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def run(**env) -> AppTest:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        return at

    return run


def test_no_provider_or_model_selector(app):
    at = app()
    assert not at.exception, at.exception
    # No provider radio, no OCR-method radio, no model selectbox.
    assert len(at.radio) == 0
    assert len(at.selectbox) == 0
    # Exactly one API-key field, labelled Cohere.
    fields = [w for w in at.text_input if "API key" in w.label]
    assert len(fields) == 1
    assert "Cohere" in fields[0].label


def test_api_key_field_never_prefilled(app):
    at = app(COHERE_API_KEY="env-secret")
    assert not at.exception, at.exception
    fields = [w for w in at.text_input if "API key" in w.label]
    assert len(fields) == 1 and fields[0].value == ""


def test_parse_without_input_explains_instead_of_greying_out(app):
    at = app()
    assert not at.exception, at.exception
    btn = [b for b in at.button if b.label == "Parse documents"][0]
    assert not btn.disabled
    at = btn.click().run()
    assert not at.exception, at.exception
    warnings = [w.value for w in at.warning]
    assert any("at least one document" in w for w in warnings)
    assert any("API key" in w for w in warnings)


def test_has_document_uploader(app):
    at = app()
    assert not at.exception, at.exception
    assert any("Drag and drop" in u.label for u in at.file_uploader)
    # Accepts multiple files (the batch entry point needs a list).
    assert at.file_uploader[0].proto.multiple_files is True


def test_uploader_accepts_image_formats(app):
    """JPEG (and siblings) must be draggable — the reported bug was the uploader
    rejecting image/jpeg client-side before OCR ever ran."""
    at = app()
    assert not at.exception, at.exception
    types = list(at.file_uploader[0].proto.type)
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        assert ext in types, f"uploader missing accepted type {ext}"
