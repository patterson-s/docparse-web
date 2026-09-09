"""Format-dispatching reader that delegates OCR to a pluggable OcrProvider.

PDF -> OcrProvider (default Mistral OCR). DOCX -> python-docx. MD -> passthrough.
Callers pass an OcrProvider instance so the docparsing pipeline can swap OCR
backends (Mistral / Paddle / CN cloud) without touching this code.
"""

from __future__ import annotations

from pathlib import Path

from ..providers import get_ocr_provider, DocumentSource

# Standalone images a provider's OCR can transcribe directly (as a single image),
# without a local rasterisation step like PDFs need.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def read(
    path: str | Path,
    api_key: str = "",
    ocr_provider=None,
    chat_provider=None,  # accepted for symmetry; not used by docx/md
) -> str:
    """Read a file into a markdown string, dispatching by extension.

    `ocr_provider` may be an OcrProvider instance or a string id (e.g. "mistral").
    If None, the default Mistral OCR provider is used (key from api_key/env).
    PDFs and standalone images (PNG/JPEG/GIF/WebP) are handed to the OCR provider;
    DOCX/MD/TXT are read locally without OCR.
    """
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".pdf" or ext in _IMAGE_EXTENSIONS:
        if ocr_provider is None:
            ocr_provider = get_ocr_provider(api_key=api_key)
        elif isinstance(ocr_provider, str):
            ocr_provider = get_ocr_provider(ocr_provider, api_key=api_key)
        source = DocumentSource.from_path(p)
        return ocr_provider.extract(source)

    if ext in {".docx", ".doc"}:
        from .docx_reader import docx_to_markdown

        return docx_to_markdown(p)

    if ext in {".md", ".txt"}:
        from .md_reader import md_to_markdown

        return md_to_markdown(p)

    raise ValueError(
        f"Unsupported file type: {ext!r}. "
        "Supported: .pdf, .docx, .doc, .md, .txt, .png, .jpg, .jpeg, .webp, .gif"
    )


def detect_format(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".docx", ".doc"}:
        return "docx"
    if ext in {".md", ".txt"}:
        return "md"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    return "unknown"
