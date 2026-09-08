"""Cohere-only docparse for the web.

Upload PDF/DOCX/MD/TXT, supply your own Cohere API key, and download a zip of the
parsed entries — one folder per document in the SciDiplo "source library" layout:

    <Slug>/  <Slug>.md  abstract.md  body.md  references.md  bibliographic.md  notes/

Fixed pipeline: Cohere Parse (parse-v5.0) for extraction, command-a-03-2025 for
metadata. No provider selector, no model selector — two Cohere models, that's it.

The API key is never stored server-side; it lives only in this request's
in-process Cohere clients and is sent only to Cohere.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="docparse · web", page_icon="📄", layout="wide")

CHAT_MODEL = "command-a-03-2025"   # metadata / structure-plan model (command-a)
PARSE_MODEL_ID = "cohere-parse"    # OCR model registry id (parse-v5.0)
MAX_FILES = 10


def _cohere_providers(api_key: str):
    """The only two providers the app builds — both Cohere."""
    from docparse import providers

    ocr = providers.get_ocr_provider(PARSE_MODEL_ID, api_key=api_key)
    chat = providers.get_chat_provider("cohere", api_key=api_key)
    return ocr, chat


def _save_uploads(uploads, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for up in uploads:
        target = dest / up.name
        data = up.getvalue()
        if not target.exists() or target.stat().st_size != len(data):
            target.write_bytes(data)
        saved.append(target)
    return saved


def _zip_vault(vault_dir: Path) -> bytes:
    from docparse.vault_builder import zip_vault

    return zip_vault(vault_dir)


st.title("📄 docparse · web")
st.caption(
    "PDF / DOCX / MD → structured Markdown. Your own Cohere key; "
    "nothing is stored server-side."
)

api_key = st.text_input(
    "Your Cohere API key",
    type="password",
    help="Get one at https://dashboard.cohere.com/api-keys. Sent only to Cohere.",
)

if "upload_dir" not in st.session_state:
    st.session_state.upload_dir = tempfile.mkdtemp(prefix="docparse_in_")

uploads = st.file_uploader(
    "Drag and drop documents here",
    type=["pdf", "docx", "md", "txt"],
    accept_multiple_files=True,
)
if uploads and len(uploads) > MAX_FILES:
    st.error(f"{len(uploads)} files — the limit is {MAX_FILES} per run.")
    uploads = uploads[:MAX_FILES]

if st.button("Parse documents", type="primary"):
    problems = []
    if not uploads:
        problems.append("Add at least one document — drag files in above.")
    if not api_key:
        problems.append("Enter your Cohere API key above.")
    if problems:
        for p in problems:
            st.warning(p)
        st.stop()

    from docparse.vault_builder import build_vault

    docs = _save_uploads(uploads, Path(st.session_state.upload_dir))
    output_dir = Path(tempfile.mkdtemp(prefix="docparse_out_"))

    try:
        ocr, chat = _cohere_providers(api_key)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user
        st.error(f"Could not initialise Cohere: {exc}")
        st.stop()

    bar = st.progress(0.0, text="Starting…")
    with st.status(
        f"Parsing {len(docs)} document(s) with Cohere…", expanded=True
    ) as status:
        try:
            results = build_vault(
                input_dir=Path(st.session_state.upload_dir),
                output_dir=output_dir,
                api_key=api_key,
                model=CHAT_MODEL,
                ocr_provider=ocr,
                chat_provider=chat,
                files=docs,
                progress_cb=lambda name, done, total: bar.progress(
                    done / total, text=f"{done}/{total} — {name}"
                ),
                write_progress=False,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            status.update(label=f"Failed: {exc}", state="error")
            st.exception(exc)
            st.stop()
        ok = [r for r in results if r.get("slug")]
        failed = [r for r in results if not r.get("slug")]
        status.update(
            label=f"Done — {len(ok)} parsed, {len(failed)} failed",
            state="complete" if not failed else "error",
        )
    bar.empty()
    st.session_state.results = results
    st.session_state.output_dir = str(output_dir)

results = st.session_state.get("results")
if results:
    output_dir = Path(st.session_state.output_dir)
    ok = [r for r in results if r.get("slug")]
    failed = [r for r in results if not r.get("slug")]
    if ok:
        st.subheader(f"{len(ok)} entry(ies)")
        for r in ok:
            st.write(
                f"- **{r['slug']}** — {r.get('title') or '—'} ({r.get('year') or '—'})"
            )
    if failed:
        st.error(f"{len(failed)} document(s) failed")
        for r in failed:
            st.write(f"- `{r.get('source') or r.get('filename')}` — {r.get('error')}")
    if ok:
        st.download_button(
            "⬇ Download parsed entries (.zip)",
            data=_zip_vault(output_dir),
            file_name="docparse-output.zip",
            mime="application/zip",
            type="primary",
        )
