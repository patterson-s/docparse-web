# docparse_web — Cohere-only web version of docparse, deployed as a Render blueprint

## Goal
A new standalone Streamlit web app (`C:\Users\spatt\Desktop\docparse_web`) that parses
uploaded PDF/DOCX/MD/TXT into the exact SciDiplo_AIGOV `source/` entry layout using
**only Cohere** (user supplies their own API key on arrival), shippable as a Render
blueprint (`render.yaml`).

## Current context / assumptions (verified 2026-09-08)
- `docparse_web/` is an empty, non-git folder — it is the fresh workspace for this project.
- The core `docparse` package at `C:\Users\spatt\Desktop\docparse` ALREADY produces the
  required output layout. `docparse/genres/academic.py` → `AcademicArticleGenre.build_vault`
  writes, per document: `<Slug>/<Slug>.md  abstract.md  body.md  references.md  bibliographic.md  notes/`
  — byte-for-byte the SciDiplo_AIGOV/source shape (confirmed against
  `AlapietilaSmuha_2021_FrameworkGlobalCooperation/`).
- The batch entry point `docparse.vault_builder.build_vault(...)` is the single integration
  point. It takes `api_key`, `model`, `ocr_provider`, `chat_provider`, `files`, `write_progress`.
- Cohere providers already exist and are registered:
  - `get_chat_provider("cohere", api_key)` → `CohereChatProvider`, default chat model
    `command-a-03-2025` (`docparse/providers/cohere.py`).
  - `get_ocr_provider("cohere-parse", api_key)` → `CohereParseOcrProvider` (`parse-v5.0`,
    purpose-built PDF parser, rasterises pages locally via pypdfium2). Preferred OCR.
  - `get_ocr_provider("cohere", api_key)` → `CohereVisionOcrProvider`
    (`command-a-vision-07-2025`). Fallback OCR.
- Do NOT offer `command-a-plus-05-2026` (hard-429s on Scott's key) — fix the chat model to
  `command-a-03-2025`.
- The package cannot import `readers` (used by `build_vault`) without `mistralai` installed:
  `docparse/readers/pdf.py` does a top-level `from mistralai import Mistral`. So `mistralai`
  stays a requirement even though the web app never constructs a Mistral provider
  ("cohere-only" = only Cohere is offered/used, not that mistral is removed from the dep tree).
- `providers/__init__.py` imports `.mistral`, `.cohere`, `.openai_compatible` at module top;
  none of those import their vendor SDK at top level (verified), so `import docparse` needs
  only stdlib. No `typer`/`pandas`/`openai` are imported by core package modules the web app
  touches (verified).
- The GitHub remote `https://github.com/patterson-s/docparse` returns HTTP 404 → it is NOT a
  reliably cloneable dependency. So the web repo **vendors** the `docparse/` package
  (guaranteed Render build) rather than declaring a git/PyPI dependency.

## Architecture / proposed approach
A standalone Streamlit app (`app.py`) in `docparse_web/` that imports the vendored
`docparse` package for all parsing logic (providers, genre layout, `build_vault`, `zip_vault`)
and adds only: a Cohere-only UI (single API-key field, never prefilled server-side), drag-and-drop
upload, and a zip download of the resulting source-layout entries. A `render.yaml` blueprint
deploys it on Render's free web service; a pytest suite (AppTest smoke + fake-SDK provider wiring,
no network) guards the UI, plus one real end-to-end run validates the output shape against the
SciDiplo_AIGOV gold layout before deploy.

## Step-by-step tasks
Work in `C:\Users\spatt\Desktop\docparse_web`. Commit after every task.

### Phase 0 — Scaffold + vendored package imports
**T0.1 Vendored package.** Copy the core package in (excluding caches):
```bash
cd "C:\Users\spatt\Desktop\docparse_web"
cp -r "C:\Users\spatt\Desktop\docparse\docparse" ./docparse
find ./docparse -name __pycache__ -type d -prune -exec rm -rf {} +
find ./docparse -name "*.pyc" -delete
```
Verify import:
```bash
python -c "import docparse; from docparse import providers; from docparse.vault_builder import build_vault, zip_vault; print('ok')"
```
Expected: `ok` (uses the venv Python that has cohere installed — see T0.2). If `import cohere`
fails, run `pip install cohere mistralai` first.

**T0.2 requirements.txt.** Create `C:\Users\spatt\Desktop\docparse_web\requirements.txt`:
```
cohere>=7.1.0
mistralai>=1.0.0
streamlit==1.57.0
pypdfium2>=4.30.0
pillow>=10.0.0
python-docx>=1.1.0
requests>=2.31.0
```
Install into a throwaway venv for local test runs:
```bash
cd "C:\Users\spatt\Desktop\docparse_web" && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```
Expected: pip reports `Successfully installed ...` with no errors (no typer/pandas — web-only).

**T0.3 Config, env, gitignore.** Copy `.streamlit/config.toml` from docparse (maxUploadSize=200,
no usage stats); create `.env.example` documenting `COHERE_API_KEY` is user-supplied (never
server-side); create `.gitignore` (`.venv/`, `.env`, `*.py[cod]`, `__pycache__/`, `*.egg-info/`,
`.streamlit/secrets.toml`, `out/`).

**T0.4 First commit.**
```bash
git init && git add -A && git commit -m "scaffold: vendored docparse package + web deps"
```
Expected: commit succeeds; `git status` clean.

### Phase 1 — Cohere-only Streamlit app (TDD)
**T1.1 (RED) Write failing AppTest smoke tests.** Create
`C:\Users\spatt\Desktop\docparse_web\tests\test_web_app.py`:
```python
"""Cohere-only web app smoke tests (AppTest executes app.py — no network)."""
from pathlib import Path
import pytest
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent / "app.py")

@pytest.fixture
def app(monkeypatch):
    for key in ("COHERE_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    def run(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        return at
    return run

def test_no_provider_or_model_selector(app):
    at = app()
    assert not at.exception, at.exception
    assert len(at.radio) == 1          # OCR-method radio only — no provider selector
    assert len(at.selectbox) == 0      # no model selector
    fields = [w for w in at.text_input if "API key" in w.label]
    assert len(fields) == 1            # one Cohere key field
    assert "Cohere" in fields[0].label

def test_api_key_field_never_prefilled(app):
    at = app(COHERE_API_KEY="env-secret")
    assert not at.exception, at.exception
    fields = [w for w in at.text_input if "API key" in w.label]
    assert len(fields) == 1 and fields[0].value == ""

def test_parse_without_input_explains_instead_of_greying_out(app):
    at = app()
    btn = [b for b in at.button if b.label == "Parse documents"][0]
    assert not btn.disabled
    at = btn.click().run()
    assert not at.exception, at.exception
    warnings = [w.value for w in at.warning]
    assert any("at least one document" in w for w in warnings)
    assert any("API key" in w for w in warnings)

def test_has_uploader(app):
    at = app()
    assert not at.exception, at.exception
    assert any("Drag and drop" in u.label for u in at.file_uploader)
```
Run, expect FAILURE (no `app.py`):
```bash
cd "C:\Users\spatt\Desktop\docparse_web" && .venv/Scripts/python -m pytest tests/test_web_app.py -q
```
Expected: collection error (`ModuleNotFoundError: No module named 'app'`).

**T1.2 (GREEN) Write `app.py`.** Create `C:\Users\spatt\Desktop\docparse_web\app.py`:
```python
"""Cohere-only docparse for the web.

Upload PDF/DOCX/MD/TXT, supply your own Cohere API key, and download a zip of the
parsed entries — one folder per document in the SciDiplo "source library" layout:

    <Slug>/  <Slug>.md  abstract.md  body.md  references.md  bibliographic.md  notes/

Hosted-only (Render blueprint). The API key is never stored server-side; it is used
only to construct the Cohere providers for this request.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="docparse · web", page_icon="📄", layout="wide")

CHAT_MODEL = "command-a-03-2025"   # survey / structure-plan / metadata model
OCR_CHOICES = {
    "Cohere Parse (parse-v5.0)": "cohere-parse",      # default, purpose-built
    "Cohere Vision (command-a-vision)": "cohere",     # fallback
}
MAX_FILES = 10


def _cohere_providers(api_key: str, ocr_id: str):
    """The only providers the app builds — both Cohere."""
    from docparse import providers
    ocr = providers.get_ocr_provider(ocr_id, api_key=api_key)
    chat = providers.get_chat_provider("cohere", api_key=api_key)
    return ocr, chat


def _save_uploads(uploads, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
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
st.caption("PDF / DOCX / MD → structured Markdown. Your own Cohere key; nothing is stored server-side.")

api_key = st.text_input(
    "Your Cohere API key",
    type="password",
    help="Get one at https://dashboard.cohere.com/api-keys. Sent only to Cohere, never saved.",
)

ocr_label = st.radio("Extraction method (both use your Cohere key)", list(OCR_CHOICES), horizontal=True)
ocr_id = OCR_CHOICES[ocr_label]

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

    docs = _save_uploads(uploads, Path(st.session_state.upload_dir))
    output_dir = Path(tempfile.mkdtemp(prefix="docparse_out_"))

    try:
        ocr, chat = _cohere_providers(api_key, ocr_id)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user
        st.error(f"Could not initialise Cohere: {exc}")
        st.stop()

    bar = st.progress(0.0, text="Starting…")
    with st.status(f"Parsing {len(docs)} document(s) with Cohere…", expanded=True) as status:
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
            st.write(f"- **{r['slug']}** — {r.get('title') or '—'} ({r.get('year') or '—'})")
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
```
IMPORTANT: `build_vault` must be imported before use. Add right after the providers import block
(top of the button handler) — if you forget, add `from docparse.vault_builder import build_vault`
as the first line of the button handler (lazy import, mirrors the vendored pattern). Choose the
lazy import so AppTest renders don't pull `readers`/`mistralai` at page load.

Then run the tests — expected PASS:
```bash
.venv/Scripts/python -m pytest tests/test_web_app.py -q
```
Expected: `4 passed`.

**T1.3 (RED→GREEN) Fake-SDK provider wiring test.** Create
`C:\Users\spatt\Desktop\docparse_web\tests\test_cohere_wiring.py`:
```python
"""No-network test: the cohere-only wiring builds Cohere providers from the registry."""
import sys, types
import pytest
from docparse.providers import get_chat_provider, get_ocr_provider

class _ClientV2:
    instances = []
    def __init__(self, api_key="", **kwargs):
        self.api_key = api_key
        _ClientV2.instances.append(self)

@pytest.fixture(autouse=True)
def fake_cohere(monkeypatch):
    mod = types.ModuleType("cohere"); mod.ClientV2 = _ClientV2
    monkeypatch.setitem(sys.modules, "cohere", mod)
    _ClientV2.instances = []

def test_chat_provider_is_cohere():
    p = get_chat_provider("cohere", api_key="web-key")
    assert p._client.api_key == "web-key"

def test_parse_ocr_provider_is_cohere():
    p = get_ocr_provider("cohere-parse", api_key="web-key")
    assert p._client.api_key == "web-key"

def test_vision_ocr_provider_is_cohere():
    p = get_ocr_provider("cohere", api_key="web-key")
    assert p._client.api_key == "web-key"
```
Run RED first (file absent → collection error), then create it, then GREEN:
```bash
.venv/Scripts/python -m pytest tests/ -q
```
Expected: `7 passed` (4 + 3).

**T1.4 Commit.**
```bash
git add -A && git commit -m "feat: cohere-only Streamlit web app with AppTest + wiring tests"
```

### Phase 2 — End-to-end + gold validation (REAL key, ONE run)
**T2.1 Gold-shape script.** Create `C:\Users\spatt\Desktop\docparse_web\scripts\e2e_check.py`:
```python
"""One real end-to-end run against a Cohere key; asserts the SciDiplo source layout."""
import sys, tempfile
from pathlib import Path
from docparse.vault_builder import build_vault

PDF = Path(r"C:\Users\spatt\Desktop\docparse\tests\Haas_1992_IntroductionEpistemicCommunities\Haas - Introduction epistemic communities and international policy coordination.pdf")
GOLD = Path(r"C:\Users\spatt\Desktop\SciDiplo_AIGOV\source\AlapietilaSmuha_2021_FrameworkGlobalCooperation")
EXPECTED = {"abstract.md", "body.md", "references.md", "bibliographic.md", "notes"}

def main():
    key = input("Paste a Cohere API key: ").strip()
    out = Path(tempfile.mkdtemp(prefix="docparse_e2e_"))
    from docparse import providers
    ocr = providers.get_ocr_provider("cohere-parse", api_key=key)
    chat = providers.get_chat_provider("cohere", api_key=key)
    results = build_vault(
        input_dir=PDF.parent, output_dir=out, api_key=key,
        model="command-a-03-2025", ocr_provider=ocr, chat_provider=chat,
        files=[PDF], write_progress=False,
    )
    r = results[0]
    assert r.get("slug"), f"parse failed: {r.get('error')}"
    entry = out / r["slug"]
    files = {p.name for p in entry.iterdir()} | {p.name for p in entry.rglob("notes*")}
    missing = EXPECTED - files
    assert not missing, f"entry missing files: {missing}"
    print("E2E OK:", r["slug"])
    print("  files:", sorted(p.name for p in entry.iterdir()))
    print("  gold-shape matches:", EXPECTED <= files)

if __name__ == "__main__":
    main()
```
Run it:
```bash
.venv/Scripts/python scripts/e2e_check.py
```
Expected stdout: `E2E OK: Haas_1992_IntroductionEpistemicCommunities` then
`  files: [<Slug>.md, abstract.md, bibliographic.md, body.md, references.md, notes]` and
`  gold-shape matches: True`. Compare the emitted file set against `GOLD/` — the names must be
identical (contents are document-specific and will differ; that is expected). This is the
"prove it works before expanding" gate — do not proceed to Phase 3 until it prints `E2E OK`.

**T2.2 Commit.**
```bash
git add -A && git commit -m "validation: e2e gold-shape check script (real Cohere run)"
```

### Phase 3 — Render blueprint + deploy
**T3.1 `render.yaml`.** Create `C:\Users\spatt\Desktop\docparse_web\render.yaml`:
```yaml
services:
  - type: web
    name: docparse-web
    runtime: python
    plan: free
    autoDeploy: true
    buildCommand: pip install -r requirements.txt
    startCommand: streamlit run app.py --server.port $PORT --server.address 0.0.0.0
    healthCheckPath: /_stcore/health
    envVars:
      - key: PYTHON_VERSION
        value: "3.11"
```
Note: Render python runtime auto-detects `pip install -r requirements.txt`; the explicit
`buildCommand` is harmless and self-documenting. `healthCheckPath` is honoured only on paid
plans — harmless on free (Render simply won't enforce it).

**T3.2 Local smoke of the deployed command.**
```bash
cd "C:\Users\spatt\Desktop\docparse_web"
.venv/Scripts/python -m streamlit run app.py --server.port 8511 --server.address 127.0.0.1
```
Then `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8511/_stcore/health`
Expected: `200`. Kill the process.

**T3.3 README + final commit.** Write `README.md` (what it is, layout contract, how to run
locally, how to deploy on Render from the GitHub repo). Add a `COHERE_API_KEY` note under
"Security": the key never touches the server's disk/env — it lives only in the browser session's
request and the in-process provider objects. Commit.
```bash
git add -A && git commit -m "docs+deploy: Render blueprint, README, final polish"
```

**T3.4 Deploy (requires GitHub push — defer to Scott's sign-off).** Push `docparse_web` to a new
GitHub repo (e.g. `patterson-s/docparse-web`), then in Render: New → Blueprint → select the repo.
Verify the live site loads and `/health` (or `/_stcore/health`) returns 200. NOT part of the
implementer's local tasks — flag to Scott.

## Tests / validation summary
- AppTest smoke (no network): 4 tests in `tests/test_web_app.py` — no provider/model selector,
  single never-prefilled Cohere key field, explain-why on empty build, uploader present.
- Fake-SDK wiring (no network): 3 tests in `tests/test_cohere_wiring.py` — the three Cohere
  provider registrations resolve with the user-supplied key.
- Real end-to-end (one run, real key): `scripts/e2e_check.py` on the Haas PDF asserts the
  emitted entry's file set matches the SciDiplo `source/` gold shape exactly.
- Full gate: `.venv/Scripts/python -m pytest tests/ -q` → `7 passed`.

## Risks, tradeoffs, open questions
- **Vendored package drift.** `docparse_web/docparse/` is a snapshot copy of the core package;
  upstream fixes won't arrive automatically. Mitigation: keep the copy task (T0.1) in README as
  the documented refresh step. Alternative (cleaner long-term, more setup): publish docparse to
  PyPI or make the GitHub repo public and switch to a `git+https` dependency.
- **mistralai is still installed** because `docparse/readers/pdf.py` imports it at module top,
  even though the web app only ever uses Cohere. Harmless (no Mistral key, no calls). Removing it
  would require patching the vendored `readers/pdf.py` — out of scope for this first cut.
- **Security / key handling.** The key is only held in `st.session_state` request objects and the
  in-process Cohere provider clients; never written to disk, logs, or env. Ensure nothing logs
  `api_key` (grep the vendored providers — they only pass it into `cohere.ClientV2`).
- **Render free tier.** Free web service sleeps after inactivity; `healthCheckPath` not enforced.
  PDF rasterisation (pypdfium2) happens in the container — a very large PDF may hit the 512 MB
  free memory limit. Consider a document-size cap in a later iteration.
- **Cohere costs are the user's own** — the app is bring-your-own-key, so there is no server
  token cost; long PDFs are the only real per-user cost driver.
- **Open question for Scott:** repo visibility/URL for `docparse_web` (needed for T3.4), and
  whether to keep the two OCR methods (Parse/Vision) or hardcode Parse only.
- **Interpretation note:** "cohere exclusively" = the web app offers and uses only Cohere
  providers. The vendored package still contains Mistral/OpenAI-compatible code; it is simply
  never invoked. Removing that dead code is possible but risks breaking the core package — not
  done here.
