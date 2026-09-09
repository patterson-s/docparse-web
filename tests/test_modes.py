"""No-network tests for the two new web features: output modes (academic /
free-form) and folder upload.

`run_freeform` lives in app_core (streamlit-free) so it is unit-testable with a
stub OCR; the AppTest block locks the widget wiring (radios + folder uploader).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")


class _StubOcr:
    """OCR double returning a fixed transcription for whatever it is handed."""

    def __init__(self, text: str = "# stub transcription"):
        self.text = text
        self.calls: list = []

    def extract(self, source) -> str:
        self.calls.append(source)
        return self.text


# ── run_freeform (app_core) ────────────────────────────────────────────────

def test_freeform_writes_one_md_per_input(tmp_path):
    from app_core import run_freeform

    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# Alpha body", encoding="utf-8")
    b.write_text("# Beta body", encoding="utf-8")
    out = tmp_path / "out"

    results = run_freeform([a, b], out, ocr_provider=_StubOcr(), workers=1)

    assert {r["output"] for r in results} == {"a.md", "b.md"}
    assert all(r["error"] is None for r in results)
    assert (out / "a.md").read_text(encoding="utf-8") == "# Alpha body"
    assert (out / "b.md").read_text(encoding="utf-8") == "# Beta body"


def test_freeform_routes_pdf_image_to_ocr(tmp_path):
    from app_core import run_freeform

    pdf = tmp_path / "paper.pdf"
    jpg = tmp_path / "scan.jpeg"
    pdf.write_bytes(b"%PDF fake")
    jpg.write_bytes(b"\xff\xd8\xff\xe0 fake")
    out = tmp_path / "out"

    results = run_freeform([pdf, jpg], out, ocr_provider=_StubOcr("# done"))

    assert {r["output"] for r in results} == {"paper.md", "scan.md"}
    assert (out / "paper.md").read_text(encoding="utf-8") == "# done"
    assert (out / "scan.md").read_text(encoding="utf-8") == "# done"


def test_freeform_disambiguates_same_stem(tmp_path):
    """a.md and a.pdf share a stem — neither output may overwrite the other."""
    from app_core import run_freeform

    m = tmp_path / "a.md"
    p = tmp_path / "a.pdf"
    m.write_text("md content", encoding="utf-8")
    p.write_bytes(b"%PDF fake")
    out = tmp_path / "out"

    results = run_freeform([m, p], out, ocr_provider=_StubOcr("pdf ocr"), workers=1)

    outputs = sorted(r["output"] for r in results)
    assert outputs == ["a.md", "a_2.md"]
    assert (out / "a.md").read_text(encoding="utf-8") == "md content"
    assert (out / "a_2.md").read_text(encoding="utf-8") == "pdf ocr"


def test_freeform_records_ocr_failure_without_stopping(tmp_path):
    from app_core import run_freeform

    class Boom(_StubOcr):
        def extract(self, source):
            raise RuntimeError("ocr boom")

    bad = tmp_path / "bad.pdf"
    good = tmp_path / "note.md"
    bad.write_bytes(b"%PDF")
    good.write_text("# note", encoding="utf-8")
    out = tmp_path / "out"

    results = run_freeform([bad, good], out, ocr_provider=Boom(), workers=1)

    by_src = {r["source"]: r for r in results}
    assert by_src["bad.pdf"]["error"]
    assert by_src["note.md"]["output"] == "note.md" and by_src["note.md"]["error"] is None
    # The good file still landed despite the sibling failure.
    assert (out / "note.md").read_text(encoding="utf-8") == "# note"


# ── widget wiring (AppTest) ────────────────────────────────────────────────

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    for key in ("COHERE_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def run(**env) -> AppTest:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        return at

    return run


def test_output_modes_default_to_academic(app):
    at = app()
    assert not at.exception, at.exception
    assert len(at.radio) == 2  # OCR engine + Output format (no input toggle)
    labels = [r.options for r in at.radio]
    assert any("Academic article" in opts for opts in labels)
    assert any("Free form (.md)" in opts for opts in labels)
    # Output-format radio defaults to Academic article.
    out_radio = [r for r in at.radio if "Academic article" in r.options][0]
    assert out_radio.value == "Academic article"


def test_no_manual_toggle_both_uploaders_offered(app):
    """The individual-file / folder choice is auto-detected: both widgets are
    always shown (no radio), so whichever the user fills is used."""
    at = app()
    assert not at.exception, at.exception
    # No 'Add documents' radio toggling a single widget.
    assert not any("Individual files" in r.options for r in at.radio)
    assert not any("A folder" in r.options for r in at.radio)
    # Both uploaders present; one is the folder/directory widget.
    assert len(at.file_uploader) == 2
    assert any(u.proto.accept_directory for u in at.file_uploader)


# ── merge_uploads (auto-merge / dedupe across the two widgets) ─────────────

class _FakeUp:
    """UploadedFile-like stand-in for testing merge_uploads."""

    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data


def test_merge_uploads_combines_both_and_dedupes():
    from app_core import merge_uploads

    a = _FakeUp("a.pdf", b"%PDF-A")
    b = _FakeUp("b.pdf", b"%PDF-B")
    folder_a = _FakeUp("a.pdf", b"%PDF-A")  # same name+size as `a`
    c = _FakeUp("c.pdf", b"%PDF-C")

    merged = merge_uploads([a, b], [folder_a, c])

    names = [m.name for m in merged]
    assert names == ["a.pdf", "b.pdf", "c.pdf"]  # folder duplicate dropped


def test_merge_uploads_tolerates_empty_widgets():
    from app_core import merge_uploads

    assert merge_uploads(None, None) == []
    x = _FakeUp("x.md", b"# x")
    assert [m.name for m in merge_uploads([x], None)] == ["x.md"]
