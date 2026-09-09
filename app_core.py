"""Streamlit-free processing helpers for the docparse web app.

Kept import-light (no streamlit) so the parse loops are unit-testable with a
stub OCR provider, mirroring how `docparse` providers are faked elsewhere.

Two entry points, matching the app's two output modes:
- academic article  -> `build_vault` in `docparse.vault_builder` (OCR + metadata
  + abstract/body/references split). Not re-implemented here.
- free form         -> `run_freeform` below: OCR/read each file to markdown and
  write ONE plain `<source>.md` per input — no metadata, no chat, no split.
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Chat/metadata model per OCR vendor (academic mode only).
COHERE_CHAT_MODEL = "command-a-03-2025"
MISTRAL_CHAT_MODEL = "mistral-medium-latest"

# OCR-engine selector options shown by the app (Cohere is the default).
ENGINE_OPTIONS = ["Cohere (default)", "Mistral"]


def engine(engine_label: str) -> dict:
    """Registry ids + metadata chat model for a vendor label.

    The metadata/structure chat follows the OCR choice so each vendor is
    self-contained (Cohere Parse + command-a, or Mistral OCR + mistral-medium).
    """
    if engine_label == "Mistral":
        return {
            "ocr": "mistral",
            "chat": "mistral",
            "chat_model": MISTRAL_CHAT_MODEL,
            "vendor": "Mistral",
            "key_label": "Your Mistral API key",
            "key_help": "Get one at https://console.mistral.ai/api-keys/. "
                        "Sent only to Mistral.",
        }
    return {
        "ocr": "cohere-parse",
        "chat": "cohere",
        "chat_model": COHERE_CHAT_MODEL,
        "vendor": "Cohere",
        "key_label": "Your Cohere API key",
        "key_help": "Get one at https://dashboard.cohere.com/api-keys. "
                    "Sent only to Cohere.",
    }


def _unique_name(stem: str, used: set[str]) -> str:
    """Return `stem` if free, else `stem_2`, `stem_3`, … (deterministic)."""
    name = stem
    n = 2
    while name in used:
        name = f"{stem}_{n}"
        n += 1
    used.add(name)
    return name


def run_freeform(
    docs: list[Path],
    out_dir: Path,
    ocr_provider,
    workers: int = 4,
    progress_cb=None,
) -> list[dict]:
    """OCR/read each input to markdown and write one `<stem>.md` per document.

    Outputs are flattened into `out_dir`. If two inputs share a basename the
    second gets a `_2` suffix so no file is silently overwritten. Returns one
    result dict per input in completion order, shaped like `build_vault`'s so
    the UI can render done/failed uniformly:
        {"source": name, "output": "<name>.md" | None, "error": None | str}
    """
    from docparse import readers

    out_dir.mkdir(parents=True, exist_ok=True)
    print_lock = threading.Lock()
    name_lock = threading.Lock()
    results: list[dict] = []
    completed = 0
    total = len(docs)
    used_names: set[str] = set()

    def process_one(doc: Path) -> dict:
        nonlocal completed
        try:
            markdown = readers.read(doc, ocr_provider=ocr_provider)
            with name_lock:
                md_name = _unique_name(doc.stem or "document", used_names) + ".md"
            target = out_dir / md_name
            target.write_text(markdown, encoding="utf-8")
            with print_lock:
                print(f"  OK  {doc.name} -> {md_name}")
            result = {"source": doc.name, "output": md_name, "error": None}
        except Exception as exc:  # noqa: BLE001 - surfaced per-doc, like build_vault
            with print_lock:
                print(f"  FAIL {doc.name}: {exc!r}", file=sys.stderr)
            message = str(exc) or repr(exc)
            result = {"source": doc.name, "output": None, "error": message}
        completed += 1
        if progress_cb is not None:
            progress_cb(doc.name, completed, total)
        return result

    if not docs:
        return results
    if workers > 1 and total > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_one, doc) for doc in docs]
            for future in as_completed(futures):
                results.append(future.result())
    else:
        for doc in docs:
            results.append(process_one(doc))

    ok = sum(1 for r in results if r.get("output"))
    print(f"\nFree-form done. {ok}/{total} documents transcribed to {out_dir}")
    return results
