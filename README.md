# docparse · web

Web version of [docparse](https://github.com/patterson-s/docparse).
Upload PDF / DOCX / MD / TXT / images (PNG, JPEG, WebP, GIF) — picked
individually or as a **whole folder** (recursively filtered to supported
types) — supply your own API key for the OCR engine you choose, and download a
zip of the output.

## Two OCR engines (bring your own key)

- **Cohere (default):** Cohere Parse (`parse-v5.0`) — `CohereParseOcrProvider`.
  PDFs are rasterised locally (pypdfium2) and each page sent as an image to
  Parse; standalone images go straight to Parse.
- **Mistral:** Mistral OCR (`mistral-ocr-latest`) — `MistralOcrProvider`.
  Files are uploaded to Mistral, OCR'd via a signed URL, then deleted.

The key field follows your engine choice; each is sent only to its vendor and
never stored server-side.

## Two output modes

- **Academic article** (default): a folder per document in the SciDiplo "source
  library" layout —

  ```
  <Slug>/  <Slug>.md  abstract.md  body.md  references.md  bibliographic.md  notes/
  ```

  The output is byte-compatible with the folders in
  `SciDiplo_AIGOV/source/` (e.g. `AlapietilaSmuha_2021_FrameworkGlobalCooperation/`).
  The metadata step uses the SAME vendor's chat model as the OCR engine:
  Cohere `command-a-03-2025`, or Mistral `mistral-medium-latest`.

- **Free form (.md)**: OCR/read each input to ONE plain `<source>.md` — no
  metadata step, no abstract/body/references split. Outputs are flattened; a
  basename collision gets a `_2` suffix.

No model selector and no selectable chat engine: OCR vendor fixes the metadata
chat, and each vendor's model is fixed. That's it.

## Security

The API key is entered in the browser and **never stored server-side**. It lives
only in this request's in-process provider clients, is sent only to the chosen
vendor, and is never written to disk, logs, or environment. No server-side key
exists — every user brings their own and is billed by that vendor directly.

## Local development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip install pytest          # dev-only, not in requirements
.venv/Scripts/python -m pytest tests/ -q            # 30 tests, no network
.venv/Scripts/python -m streamlit run app.py        # http://localhost:8501
```

## End-to-end validation (real Cohere run)

Proves the emitted entry matches the gold source-library shape:

```bash
COHERE_API_KEY=<key> .venv/Scripts/python scripts/e2e_check.py
```

Expect `E2E OK: Haas_1992_...` and `gold-shape matches: True`.

## Deploy (Render blueprint)

1. Push this repo to GitHub (e.g. `patterson-s/docparse-web`).
2. Render → **New** → **Blueprint** → pick the repo.
3. `render.yaml` starts `streamlit run app.py` on `$PORT`; free tier.

## Refreshing the vendored core

`docparse/` here is a snapshot of the `docparse` package at
`C:\Users\spatt\Desktop\docparse`. To pull upstream fixes:

```bash
rm -rf docparse
cp -r "C:\Users\spatt\Desktop\docparse\docparse" ./docparse
find docparse -name __pycache__ -type d -prune -exec rm -rf {} +
find docparse -name "*.pyc" -delete
```
