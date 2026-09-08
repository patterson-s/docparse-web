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
    CohereChatProvider,
    CohereParseOcrProvider,
)


class _ClientV2:
    instances: list["_ClientV2"] = []

    def __init__(self, api_key: str = "", **kwargs):
        self.api_key = api_key
        _ClientV2.instances.append(self)


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
