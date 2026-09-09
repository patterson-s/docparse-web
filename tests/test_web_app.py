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
    # No model/provider/method SELECTBOX widgets.
    assert len(at.selectbox) == 0
    # Two radios: OCR engine, output format.
    # None may expose a raw model identifier to the user.
    raw = ("command-a", "parse-v", "ocr-latest", "medium-latest", "cohere-parse")
    for r in at.radio:
        for opt in r.options:
            assert not any(tok in opt.lower() for tok in raw)
    # OCR engine defaults to Cohere; exactly one key field, labelled Cohere.
    eng_radios = [r for r in at.radio if "Cohere (default)" in r.options]
    assert len(eng_radios) == 1
    assert eng_radios[0].value == "Cohere (default)"
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


def test_has_both_uploaders_no_toggle(app):
    """Loose files and a whole folder are each offered, always visible — no
    toggle to switch between them."""
    at = app()
    assert not at.exception, at.exception
    ups = at.file_uploader
    assert len(ups) == 2
    # Files widget: multi-file. Folder widget: directory mode.
    assert any(u.proto.multiple_files is True and not u.proto.accept_directory
               for u in ups)
    assert any(u.proto.accept_directory for u in ups)
    assert any("whole folder" in u.label for u in ups)


def test_uploader_accepts_image_formats(app):
    """JPEG (and siblings) must be draggable in BOTH uploaders — the reported
    bug was the uploader rejecting image/jpeg client-side before OCR ran."""
    at = app()
    assert not at.exception, at.exception
    for up in at.file_uploader:
        types = list(up.proto.type)
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            assert ext in types, f"uploader missing accepted type {ext}"
