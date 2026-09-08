"""Pluggable provider layer.

Two provider families:

  ChatProvider - structured JSON completion (survey / structure plan /
                 metadata / heading detection). OpenAI-compatible backends
                 (DeepSeek, Qwen, GLM, Kimi...) share one implementation.
  OcrProvider  - document -> markdown. Mistral OCR is the default; Paddle
                 (local) and cloud CN OCR backends can be registered later.

The registry functions (get_chat_provider / get_ocr_provider) keep Mistral as
the zero-config default, so the rest of docparse is unchanged when no provider
is passed. Swapping a provider is then a one-word override at the API boundary.
"""

from __future__ import annotations

import os
from typing import Optional

from .base import (
    ChatProvider,
    OcrProvider,
    DocumentSource,
    ProviderError,
)
from .mistral import MistralChatProvider, MistralOcrProvider
from .cohere import (
    CohereChatProvider,
    CohereVisionOcrProvider,
    CohereParseOcrProvider,
)
from .openai_compatible import OpenAICompatibleChatProvider


__all__ = [
    "ChatProvider",
    "OcrProvider",
    "DocumentSource",
    "ProviderError",
    "get_chat_provider",
    "get_ocr_provider",
    "list_chat_providers",
    "list_ocr_providers",
    "register_chat_provider",
    "register_ocr_provider",
    "MistralChatProvider",
    "MistralOcrProvider",
    "CohereChatProvider",
    "CohereVisionOcrProvider",
    "CohereParseOcrProvider",
    "OpenAICompatibleChatProvider",
]


# ── Registry ─────────────────────────────────────────────────────────────────
# Registered lazily to avoid importing vendored SDKs until a provider is used.

_CHAT_REGISTRY: dict[str, tuple[type, dict]] = {
    "mistral": (MistralChatProvider, {"env_key": "MISTRAL_API_KEY"}),
    "cohere": (CohereChatProvider, {"env_key": "COHERE_API_KEY"}),
    "deepseek": (
        OpenAICompatibleChatProvider,
        {
            "base_url": os.environ.get(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
            ),
            "env_key": "DEEPSEEK_API_KEY",
            "default_model": "deepseek-chat",
        },
    ),
    "qwen": (
        OpenAICompatibleChatProvider,
        {
            "base_url": os.environ.get(
                "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "env_key": "QWEN_API_KEY",
            "default_model": "qwen-plus",
        },
    ),
}

_OCR_REGISTRY: dict[str, tuple[type, dict]] = {
    "mistral": (MistralOcrProvider, {"env_key": "MISTRAL_API_KEY"}),
    "cohere": (CohereVisionOcrProvider, {"env_key": "COHERE_API_KEY"}),
    "cohere-parse": (CohereParseOcrProvider, {"env_key": "COHERE_API_KEY"}),
}


def register_chat_provider(name: str, cls: type, cfg: dict | None = None) -> None:
    """Register a new chat provider at runtime (used by experiments/plugins)."""
    _CHAT_REGISTRY[name] = (cls, cfg or {})


def register_ocr_provider(name: str, cls: type, cfg: dict | None = None) -> None:
    _OCR_REGISTRY[name] = (cls, cfg or {})


def list_chat_providers() -> list[str]:
    return sorted(_CHAT_REGISTRY)


def list_ocr_providers() -> list[str]:
    return sorted(_OCR_REGISTRY)


def _resolve_key(cfg: dict, api_key: Optional[str]) -> str:
    """An explicit key always wins — including an empty one, so a caller can
    deliberately run keyless. Only `None` falls back to the environment."""
    if api_key is not None:
        return api_key
    env_key = cfg.get("env_key")
    return os.environ.get(env_key, "") if env_key else ""


def _instantiate(cls: type, cfg: dict, key: str, name: str):
    if "base_url" in cfg:
        # OpenAI-compatible backend: needs base_url/key/default_model.
        return cls(
            base_url=cfg["base_url"],
            api_key=key,
            default_model=cfg["default_model"],
            display_name=name,
        )
    try:
        return cls(api_key=key)
    except TypeError:
        if cfg:
            raise
        # Custom-registered provider whose constructor takes no api_key.
        return cls()


def get_chat_provider(name: str = "mistral", api_key: Optional[str] = None) -> ChatProvider:
    if name not in _CHAT_REGISTRY:
        raise ProviderError(
            f"Unknown chat provider {name!r}. Known: {list_chat_providers()}"
        )
    cls, cfg = _CHAT_REGISTRY[name]
    return _instantiate(cls, cfg, _resolve_key(cfg, api_key), name)


def get_ocr_provider(name: str = "mistral", api_key: Optional[str] = None) -> OcrProvider:
    if name not in _OCR_REGISTRY:
        raise ProviderError(
            f"Unknown OCR provider {name!r}. Known: {list_ocr_providers()}"
        )
    cls, cfg = _OCR_REGISTRY[name]
    return _instantiate(cls, cfg, _resolve_key(cfg, api_key), name)
