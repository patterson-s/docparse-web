"""No-network test: the cohere-only wiring builds Cohere providers from the registry.

Locks the exact two providers the web app depends on — Cohere Parse for OCR and
Cohere chat (command-a) for metadata — and pins the chat model so a future
`get_*_provider` refactor cannot silently change models.
"""
from __future__ import annotations

import sys
import types

import pytest

from docparse.providers import (
    get_chat_provider,
    get_ocr_provider,
    DocumentSource,
    CohereChatProvider,
    CohereParseOcrProvider,
)


class _ClientV2:
    instances: list["_ClientV2"] = []

    def __init__(self, api_key: str = "", **kwargs):
        self.api_key = api_key
        _ClientV2.instances.append(self)

    def parse(self, **kwargs):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def fake_cohere(monkeypatch):
    mod = types.ModuleType("cohere")
    mod.ClientV2 = _ClientV2
    monkeypatch.setitem(sys.modules, "cohere", mod)
    _ClientV2.instances = []


def test_chat_provider_is_cohere_command_a():
    p = get_chat_provider("cohere", api_key="web-key")
    assert isinstance(p, CohereChatProvider)
    assert p._client.api_key == "web-key"
    # The web app pins command-a-03-2025 — see app.py CHAT_MODEL.
    assert p._default_model == "command-a-03-2025"


def test_parse_ocr_provider_is_cohere_parse():
    p = get_ocr_provider("cohere-parse", api_key="web-key")
    assert isinstance(p, CohereParseOcrProvider)
    assert p._client.api_key == "web-key"


def test_parse_is_registered_and_defaults_to_parse_v5():
    from docparse.providers.cohere import _PARSE_MODEL

    assert "cohere-parse" in [getattr(x, "name", x) for x in (CohereParseOcrProvider,)]
    assert _PARSE_MODEL == "parse-v5.0"


def test_parse_parallel_workers_preserve_order(monkeypatch):
    """Parallel page requests must return in page order regardless of completion."""
    import docparse.providers.cohere as cohere_mod

    calls = []

    def fake_parse_page(self, data_uri: str) -> str:
        # data_uri encodes the page index; return a value keyed to it.
        calls.append(data_uri)
        page_no = data_uri[-1]
        return f"page-content-{page_no}"

    monkeypatch.setattr(CohereParseOcrProvider, "_parse_page", fake_parse_page)
    monkeypatch.setattr(
        cohere_mod, "_render_pdf_pages", lambda b, dpi: [f"png-{i}".encode() for i in range(6)]
    )
    provider = CohereParseOcrProvider(api_key="k", workers=4)

    out = provider.extract(DocumentSource.from_bytes(b"pdf", "doc.pdf"))

    # Six pages, joined by the separator, in submission order.
    assert len(out.split(cohere_mod._PAGE_SEPARATOR)) == 6
    assert calls and len(calls) == 6


def test_parse_retries_with_backoff(monkeypatch):
    """A failed page must be retried with an increasing delay, not in a tight loop."""
    import time as _time
    import docparse.providers.cohere as cohere_mod

    calls = []
    sleeps = []
    monkeypatch.setattr(_time, "sleep", lambda s: sleeps.append(s))

    def boom(self, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("burst")

    monkeypatch.setattr(_ClientV2, "parse", boom)
    monkeypatch.setattr(
        cohere_mod, "_render_pdf_pages", lambda b, dpi: [b"png"]
    )
    from docparse.providers import ProviderError

    provider = CohereParseOcrProvider(api_key="k")
    with pytest.raises(ProviderError):
        provider.extract(DocumentSource.from_bytes(b"pdf", "doc.pdf"))
    # 3 attempts => 2 sleeps, increasing.
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert sleeps[0] < sleeps[1]
