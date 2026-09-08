"""Real end-to-end run against a Cohere key; asserts the SciDiplo source layout.

Usage (from repo root):
    COHERE_API_KEY=<key> .venv/Scripts/python scripts/e2e_check.py

This is the "prove it works before expanding" gate. It parses the Haas PDF with
Cohere Parse + command-a-03-2025, then asserts the emitted entry contains exactly
the source-library file set (the same names the SciDiplo_AIGOV/source/ entries use).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docparse.vault_builder import build_vault  # noqa: E402

PDF = Path(
    r"C:/Users/spatt/Desktop/docparse/tests/Haas_1992_IntroductionEpistemicCommunities/"
    "Haas - Introduction epistemic communities and international policy coordination.pdf"
)
GOLD = Path(
    r"C:\Users\spatt\Desktop\SciDiplo_AIGOV\source\AlapietilaSmuha_2021_FrameworkGlobalCooperation"
)
EXPECTED = {"abstract.md", "body.md", "references.md", "bibliographic.md", "notes"}


def main() -> int:
    key = os.environ.get("COHERE_API_KEY", "").strip()
    if not key:
        print("No COHERE_API_KEY set.")
        return 1

    out = Path(tempfile.mkdtemp(prefix="docparse_e2e_"))
    from docparse import providers

    ocr = providers.get_ocr_provider("cohere-parse", api_key=key)
    chat = providers.get_chat_provider("cohere", api_key=key)

    results = build_vault(
        input_dir=PDF.parent,
        output_dir=out,
        api_key=key,
        model="command-a-03-2025",
        ocr_provider=ocr,
        chat_provider=chat,
        files=[PDF],
        write_progress=False,
    )
    r = results[0]
    if not r.get("slug"):
        print(f"parse failed: {r.get('error')}")
        return 1

    entry = out / r["slug"]
    files = {p.name for p in entry.iterdir()}
    files |= {p.name for p in entry.rglob("notes*")}
    missing = EXPECTED - files
    if missing:
        print(f"entry missing files: {sorted(missing)}")
        return 1

    print("E2E OK:", r["slug"])
    print("  files:", sorted(p.name for p in entry.iterdir()))
    print("  gold-shape matches:", EXPECTED <= files)
    print("  gold reference dir has same file set:", EXPECTED == {
        p.name for p in GOLD.iterdir()
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
