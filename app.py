"""docparse web app — Cohere-only.

Upload PDF / DOCX / MD / TXT / images (or a whole folder of them), supply your
own Cohere API key, and download a zip of the parsed output.

Two output modes:
- Academic article (default): the SciDiplo "source library" layout — a folder
  per document with <Slug>.md, abstract.md, body.md, references.md,
  bibliographic.md, notes/ (Cohere Parse extraction + command-a metadata).
- Free form (.md): OCR/read each input to ONE plain <source>.md — no metadata,
  no abstract/body/references split, no chat call.

Inputs may be picked one-by-one/many-at-once, or as an entire folder
(recursively; only supported types are taken).

Fixed pipeline for academic mode: Cohere Parse (parse-v5.0) for extraction,
command-a-03-2025 for metadata. No provider/model selector.

The API key is never stored server-side; it lives only in this request's
in-process Cohere clients and is sent only to Cohere.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="docparse · web", page_icon="📄", layout="wide")

CHAT_MODEL = "command-a-03-2025"   # metadata / structure-plan model (command-a)
MAX_FILES = 100

# Formats the uploader accepts. When a whole folder is dropped, only files
# matching these are taken (recursively). Single images route to the OCR
# provider directly.
SUPPORTED_TYPES = ["pdf", "docx", "md", "txt", "png", "jpg", "jpeg", "webp", "gif"]

OUTPUT_MODES = ["Academic article", "Free form (.md)"]
INPUT_MODES = ["Individual files", "A folder"]


def _ocr_provider(api_key: str):
    from docparse.providers.cohere import CohereParseOcrProvider

    return CohereParseOcrProvider(api_key=api_key)


def _providers(api_key: str):
    """OCR + chat providers for academic mode (chat = metadata model)."""
    from docparse import providers

    ocr = _ocr_provider(api_key)
    chat = providers.get_chat_provider("cohere", api_key=api_key)
    return ocr, chat


def _save_uploads(uploads, dest: Path) -> list[Path]:
    """Write uploaded files to `dest`, preserving any folder sub-paths.

    In folder mode Streamlit reports each file with a relative path (e.g.
    `reports/2024/paper.pdf`); parent directories are created so nested
    structure survives and same-named files in different folders don't clobber.
    """
    saved: list[Path] = []
    for up in uploads:
        target = dest / up.name
        target.parent.mkdir(parents=True, exist_ok=True)
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
    "PDF / DOCX / MD / images → structured Markdown. Your own Cohere key; "
    "nothing is stored server-side."
)

api_key = st.text_input(
    "Your Cohere API key",
    type="password",
    help="Get one at https://dashboard.cohere.com/api-keys. Sent only to Cohere.",
)

mode = st.radio("Output format", OUTPUT_MODES, index=0, horizontal=True,
                help="Academic = folder per doc with abstract/body/references split "
                     "(uses a metadata step). Free form = one plain .md transcription "
                     "per file, no split, no metadata call.")
input_style = st.radio("Add documents", INPUT_MODES, index=0, horizontal=True,
                       help="Pick files individually, or select one folder and have "
                            "every supported file inside it (recursively) taken.")

if "upload_dir" not in st.session_state:
    st.session_state.upload_dir = tempfile.mkdtemp(prefix="docparse_in_")

_ext = ", ".join(f".{t}" for t in SUPPORTED_TYPES)
if input_style == "A folder":
    uploads = st.file_uploader(
        "Select a folder of documents (all supported files inside, recursively)",
        type=SUPPORTED_TYPES,
        accept_multiple_files="directory",
        help=f"Picks every file matching: {_ext}.",
    )
else:
    uploads = st.file_uploader(
        "Drag and drop documents here",
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
        help=f"Accepted types: {_ext}.",
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

    is_freeform = mode == "Free form (.md)"

    docs = _save_uploads(uploads, Path(st.session_state.upload_dir))
    output_dir = Path(tempfile.mkdtemp(prefix="docparse_out_"))

    bar = st.progress(0.0, text="Starting…")

    try:
        if is_freeform:
            ocr = _ocr_provider(api_key)
        else:
            ocr, chat = _providers(api_key)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user
        st.error(f"Could not initialise Cohere: {exc}")
        st.stop()

    label = (
        f"Transcribing {len(docs)} document(s) with Cohere…"
        if is_freeform else
        f"Parsing {len(docs)} document(s) with Cohere…"
    )
    with st.status(label, expanded=True) as status:
        try:
            if is_freeform:
                from app_core import run_freeform

                results = run_freeform(
                    docs,
                    output_dir,
                    ocr_provider=ocr,
                    workers=2,
                    progress_cb=lambda name, done, total: bar.progress(
                        done / total, text=f"{done}/{total} — {name}"
                    ),
                )
            else:
                from docparse.vault_builder import build_vault

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
        done = [r for r in results if (r.get("slug") if not is_freeform else r.get("output"))]
        failed = [r for r in results if r not in done]
        status.update(
            label=f"Done — {len(done)} done, {len(failed)} failed",
            state="complete" if not failed else "error",
        )
    bar.empty()
    st.session_state.results = results
    st.session_state.output_dir = str(output_dir)
    st.session_state.mode = mode

results = st.session_state.get("results")
if results:
    output_dir = Path(st.session_state.output_dir)
    done_mode = st.session_state.get("mode", "Academic article")
    is_freeform = done_mode == "Free form (.md)"
    ok = [r for r in results if (r.get("slug") if not is_freeform else r.get("output"))]
    failed = [r for r in results if r not in ok]
    if ok:
        if is_freeform:
            st.subheader(f"{len(ok)} transcription(s)")
            for r in ok:
                st.write(f"- `{r['output']}` — from `{r['source']}`")
        else:
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
