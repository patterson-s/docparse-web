"""No-network tests: standalone images (JPEG/PNG/…) flow through the whole input
path — uploader allow-list, reader dispatch to OCR, input discovery, and the
data-URI MIME labelling that lets Cohere Parse decode a non-PNG upload.

Regression for: the web uploader rejected image/jpeg client-side, and even had
it passed, the reader had no branch for standalone images.
"""
from __future__ import annotations

import pytest


class _StubOcr:
    """OcrProvider double that records the single image source handed to it."""

    def __init__(self):
        self.calls: list = []
        self.name = "stub"

    def extract(self, source) -> str:
        self.calls.append(source)
        return "# stub transcription"


@pytest.fixture
def image_file(tmp_path):
    """A fake JPEG on disk (content irrelevant — a stub OCR reads no bytes)."""
    p = tmp_path / "scan.jpeg"
    p.write_bytes(b"\xff\xd8\xff\xe0 not-a-real-jpeg")
    return p


def test_reader_dispatches_image_to_ocr(image_file):
    from docparse.readers import read

    ocr = _StubOcr()
    out = read(image_file, ocr_provider=ocr)
    assert out == "# stub transcription"
    # Exactly one DocumentSource, carrying the uploaded filename.
    assert len(ocr.calls) == 1
    assert ocr.calls[0].filename == "scan.jpeg"


def test_reader_image_does_not_need_local_text_reader(image_file):
    """An image must not fall through to the md/txt branch or raise ValueError."""
    from docparse.readers import detect_format, read

    ocr = _StubOcr()
    assert read(image_file, ocr_provider=ocr)  # no ValueError
    assert detect_format(image_file) == "image"


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp", ".gif"])
def test_collect_inputs_finds_image(tmp_path, ext):
    from docparse.vault_builder import SUPPORTED_SUFFIXES, collect_inputs

    f = tmp_path / f"doc{ext}"
    f.write_bytes(b"x")
    assert ext in SUPPORTED_SUFFIXES
    assert collect_inputs(tmp_path) == [f]


def test_jpeg_data_uri_is_labelled_jpeg():
    """A standalone JPEG upload must not be sent to Cohere as image/png."""
    import base64
    from docparse.providers.cohere import _data_uri

    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    uri = _data_uri(jpeg)
    assert uri.startswith("data:image/jpeg;base64,")
    # Round-trips to the same bytes.
    assert base64.b64decode(uri.split(",", 1)[1]) == jpeg


def test_pdf_page_png_stays_png():
    from docparse.providers.cohere import _data_uri

    assert _data_uri(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8).startswith(
        "data:image/png;base64,"
    )
