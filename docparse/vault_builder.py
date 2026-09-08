"""Build an Obsidian-compatible vault from a folder of documents.

Each document becomes a {slug}/ subfolder containing:
  {slug}.md         — YAML frontmatter + full OCR content
  abstract.md       — extracted abstract
  body.md           — main body (minus abstract and references)
  references.md     — references section
  bibliographic.md  — YAML frontmatter + APA-formatted citation
  notes/            — reading notes, written by hand; created empty, never touched

This layout is the contract with the downstream Obsidian vaults (see
ScopingReview/source), so the frontmatter key sets are deliberately fixed:
seven keys in {slug}.md, ten in bibliographic.md.

Usage via CLI:
  docparse build-vault <input_dir> <output_dir> [--workers 4]
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Slug generation ───────────────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "as", "that", "this", "it", "its", "than", "how", "what", "when",
    "where", "why", "which", "who",
}


def make_slug(authors: list[str], year: int | None, title: str) -> str:
    """Deterministic article slug: Author_Year_TitleKeywords."""
    last_names = [_last_name(a) for a in authors if a and str(a).strip()]
    if not last_names:
        author_part = "Unknown"
    elif len(last_names) == 1:
        author_part = last_names[0]
    elif len(last_names) == 2:
        author_part = last_names[0] + last_names[1]
    else:
        author_part = last_names[0] + "Etal"

    year_part = str(year) if year else "XXXX"
    keyword_part = _title_keywords(title, n=3)
    return f"{author_part}_{year_part}_{keyword_part}"


def _last_name(full_name: str) -> str:
    if "," in full_name:
        last = full_name.split(",")[0]
    else:
        parts = full_name.strip().split()
        last = parts[-1] if parts else full_name
    return _to_ascii_title(last)


def _to_ascii_title(text: str) -> str:
    normalised = unicodedata.normalize("NFD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-zA-Z0-9]", "", ascii_text)
    return clean.title()


def _title_keywords(title: str, n: int = 3) -> str:
    words = re.findall(r"[a-zA-Z]+", title)
    keywords = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]
    selected = keywords[:n]
    return "".join(w.title() for w in selected) if selected else "Article"


# ── Content splitting ─────────────────────────────────────────────────────────

_HEADING_PAT = re.compile(r"^#{1,6}\s")
_ABST_HEADING = re.compile(
    r"^#{1,3}\s+(abstract|résumé|résumé|abstrak|summary)\b", re.IGNORECASE
)
_ABST_PLAIN_LINE = re.compile(
    r"^(abstract|résumé|résumé|abstrak|summary)\s*$", re.IGNORECASE
)
_ABST_BOLD_INLINE = re.compile(
    r"^\*{1,2}(abstract|résumé|résumé|abstrak|summary)[:\.\*]*\*{1,2}\s*(.*)$",
    re.IGNORECASE,
)
_KEYWORDS_BLOCK = re.compile(
    r"^(?:\*{0,2})(?:keywords?|key\s+words?|mots[\s\-]cl[eé]s?|palavras[\s\-]chave)"
    r"(?:\*{0,2})[\s:]*$",
    re.IGNORECASE,
)
_KEYWORDS_INLINE = re.compile(
    r"^(?:\*{0,2})(?:keywords?|key\s+words?|mots[\s\-]cl[eé]s?)\s*[:\*]{1,3}\s*\S",
    re.IGNORECASE,
)
_INTRO_HEADING = re.compile(
    r"^#{1,3}\s+(?:\d+[\.\s\|]+)?(?:introduction|background|overview|context|"
    r"introductory|mise\s+en\s+contexte)\b",
    re.IGNORECASE,
)
_REFS_PAT = re.compile(
    r"^#{1,3}\s+(references|bibliography|works\s+cited|bibliographie|références|"
    r"bibliografía|referências)\b",
    re.IGNORECASE,
)
# Appendices usually follow the bibliography. Without this, references.md
# swallows every appendix to the end of the document.
_APPENDIX_PAT = re.compile(
    r"^#{1,3}\s*(?:appendix|appendices|annexe?s?|supplementary|supporting\s+information)\b",
    re.IGNORECASE,
)
# Section headings OCR failed to mark up: a short, bare, numbered line such as
# "1. Introduction" or "3.2 Competition Requirements". Preprints and author
# manuscripts routinely come out of OCR with no `#` markup at all, and without
# this the abstract detector has nothing to stop at.
_BARE_SECTION_HEADING = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z]")

# An abstract is a paragraph, not a paper. When the marker is a bare line and
# the next heading is far away (or absent), this cap is what stops the abstract
# from swallowing the body. Generous: structured abstracts run long.
_ABSTRACT_MAX_WORDS = 400
_META_SIGNALS = re.compile(
    r"@|\bdoi\s*:|https?://|www\.|^received\b|^accepted\b|^published\b|"
    r"^correspondence\b|^email\b|^funding\b|^edited\s+by\b|^reviewed\s+by\b|"
    r"\bvol\b\.?\s+\d|\bno\b\.?\s+\d|\bpp\b\.?\s+\d|issn|eissn|orcid",
    re.IGNORECASE,
)


def _is_metadata_line(text: str) -> bool:
    return bool(_META_SIGNALS.search(text)) or len(text.strip()) < 40


def _is_substantive(text: str, min_chars: int = 150) -> bool:
    stripped = text.strip()
    return len(stripped) >= min_chars and not _is_metadata_line(stripped)


def _is_bare_section_heading(line: str) -> bool:
    """A numbered section title OCR left unmarked, e.g. "1. Introduction".

    Length-bounded so enumerated sentences ("1. We argue that ...") and numbered
    reference entries do not qualify.
    """
    stripped = line.strip()
    return bool(stripped) and len(stripped) <= 60 and bool(_BARE_SECTION_HEADING.match(stripped))


def _section_end_after(lines: list[str], start: int, n: int) -> int:
    for i in range(start, n):
        if _HEADING_PAT.match(lines[i]) or _is_bare_section_heading(lines[i]):
            return i
    return n


def _section_end_keywords(lines: list[str], start: int, n: int) -> int:
    for i in range(start, n):
        stripped = lines[i].strip()
        if (_HEADING_PAT.match(lines[i])
                or _is_bare_section_heading(lines[i])
                or _KEYWORDS_BLOCK.match(stripped)
                or _KEYWORDS_INLINE.match(stripped)):
            return i
    return n


def _cap_abstract_end(lines: list[str], start: int, end: int) -> int:
    """Shrink an abstract range to whole paragraphs within the word budget.

    The marker line ("Abstract") plus the first paragraph is always kept, however
    long — better a slightly long abstract than an empty one. Everything past the
    budget is left to the body.
    """
    words = 0
    para_start = start
    last_good = end
    for i in range(start, end):
        words += len(lines[i].split())
        at_break = not lines[i].strip()
        if at_break:
            # A paragraph just closed: it is the last safe cut point.
            if words <= _ABSTRACT_MAX_WORDS:
                last_good = i
            elif para_start > start:
                return last_good
            else:
                return i  # first paragraph alone already over budget — keep it
            para_start = i + 1
    return end if words <= _ABSTRACT_MAX_WORDS else last_good


def _preceding_paragraph(lines: list[str], before: int) -> tuple[int, int] | None:
    end = before - 1
    while end >= 0 and not lines[end].strip():
        end -= 1
    if end < 0:
        return None
    start = end
    while start > 0 and lines[start - 1].strip() and not _HEADING_PAT.match(lines[start - 1]):
        start -= 1
    return (start, end + 1)


def _find_abstract_range(lines: list[str]) -> tuple[int, int] | None:
    n = len(lines)

    for i, line in enumerate(lines):
        if _ABST_HEADING.match(line.rstrip()):
            return (i, _section_end_after(lines, i + 1, n))

    for i, line in enumerate(lines):
        if _ABST_PLAIN_LINE.match(line.strip()):
            return (i, _section_end_keywords(lines, i + 1, n))

    for i, line in enumerate(lines):
        if _ABST_BOLD_INLINE.match(line.rstrip()):
            return (i, _section_end_keywords(lines, i + 1, n))

    for i, line in enumerate(lines):
        stripped = line.strip()
        if _KEYWORDS_BLOCK.match(stripped) or _KEYWORDS_INLINE.match(stripped):
            para = _preceding_paragraph(lines, i)
            if para:
                para_text = " ".join(lines[para[0]:para[1]])
                if _is_substantive(para_text, min_chars=100):
                    return (para[0], i)
            break

    for i, line in enumerate(lines):
        if _INTRO_HEADING.match(line.rstrip()):
            if i > 120:
                break
            para = _preceding_paragraph(lines, i)
            if para:
                para_text = " ".join(lines[para[0]:para[1]])
                if _is_substantive(para_text, min_chars=200):
                    return (para[0], i)
            break

    return None


def split_content(markdown: str) -> tuple[str, str, str]:
    """Split article markdown into (abstract, body, references).

    Returns empty string for abstract or references when undetectable.
    """
    lines = markdown.splitlines(keepends=True)
    n = len(lines)

    abst_range = _find_abstract_range(lines)
    abstract_start = abst_range[0] if abst_range else None
    abstract_end = abst_range[1] if abst_range else None
    if abstract_start is not None and abstract_end is not None:
        abstract_end = _cap_abstract_end(lines, abstract_start, abstract_end)

    refs_start: int | None = None
    for i, line in enumerate(lines):
        if _REFS_PAT.match(line.rstrip()):
            refs_start = i

    # References run to the first appendix, not to the end of the document.
    refs_end = n
    if refs_start is not None:
        for i in range(refs_start + 1, n):
            if _APPENDIX_PAT.match(lines[i].rstrip()):
                refs_end = i
                break

    abstract = ""
    if abstract_start is not None and abstract_end is not None:
        abstract = "".join(lines[abstract_start:abstract_end]).strip()

    references = ""
    if refs_start is not None:
        references = "".join(lines[refs_start:refs_end]).strip()

    body_parts: list[str] = []
    for i, line in enumerate(lines):
        in_abstract = (
            abstract_start is not None
            and abstract_end is not None
            and abstract_start <= i < abstract_end
        )
        in_refs = refs_start is not None and refs_start <= i < refs_end
        if not in_abstract and not in_refs:
            body_parts.append(line)
    body = "".join(body_parts).strip()

    return abstract, body, references


# ── Serper enrichment ─────────────────────────────────────────────────────────

def enrich_via_serper(
    doi: str | None,
    title: str,
    authors: list[str],
    serper_key: str,
) -> dict:
    """Fetch citation count and publication info from Google Scholar via Serper.

    Returns {} on any error or when serper_key is empty.
    """
    if not serper_key:
        return {}

    if doi:
        query = doi
    else:
        first_last = authors[0].split()[-1] if authors else ""
        query = f'"{title}"'
        if first_last:
            query += f" {first_last}"

    try:
        resp = requests.post(
            "https://google.serper.dev/scholar",
            json={"q": query, "num": 3},
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        papers = resp.json().get("organic", [])
        if not papers:
            return {}
        top = papers[0]
        return {
            "publication_info": top.get("publicationInfo", ""),
            "cited_by": top.get("citedBy"),
            "serper_url": top.get("link", ""),
            "year": _year_from_serper(top),
        }
    except Exception as exc:
        print(f"  WARNING: Serper failed for '{title[:50]}': {exc}")
        return {}


def _year_from_serper(paper: dict) -> int | None:
    """Recover a publication year from a Scholar hit.

    Preprints often carry no year in the PDF itself (`Data & Policy (YEAR)`),
    which otherwise leaves the slug as `Author_XXXX_Title`. Scholar knows it.
    """
    raw = paper.get("year")
    if isinstance(raw, int) and 1500 < raw < 2200:
        return raw
    for field in (raw, paper.get("publicationInfo", "")):
        if not field:
            continue
        years = [int(y) for y in re.findall(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", str(field))]
        if years:
            return years[-1]
    return None


# ── YAML / APA helpers ────────────────────────────────────────────────────────

def _yaml_str(value: object) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(value)


def _fmt_author(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        last = parts[-1]
        initials = " ".join(p[0] + "." for p in parts[:-1])
        return f"{last}, {initials}"
    return name


# ── File writers ──────────────────────────────────────────────────────────────

def write_vault_entry(
    output_dir: Path,
    slug: str,
    title: str,
    authors: list[str],
    year: int | None,
    journal: str | None,
    doi: str | None,
    source_pdf: str,
    full_markdown: str,
    serper: dict,
    content_source: str = "",
) -> Path:
    """Write the full vault folder for one article. Returns the folder path."""
    folder = output_dir / slug
    folder.mkdir(parents=True, exist_ok=True)

    _write_main_md(folder, slug, title, authors, year, journal, doi,
                   source_pdf, full_markdown)

    abstract, body, references = split_content(full_markdown)
    (folder / "abstract.md").write_text(abstract + "\n" if abstract else "", encoding="utf-8")
    (folder / "body.md").write_text(body + "\n" if body else "", encoding="utf-8")
    (folder / "references.md").write_text(references + "\n" if references else "", encoding="utf-8")

    _write_bibliographic_md(folder, title, authors, year, journal or "", doi or "",
                            slug, source_pdf, serper, content_source)

    # Reading notes are written by hand. Create the folder so the entry has the
    # expected shape, but never write into it — a re-run must not clobber notes.
    (folder / "notes").mkdir(exist_ok=True)
    return folder


def _write_main_md(
    folder: Path,
    slug: str,
    title: str,
    authors: list[str],
    year: int | None,
    journal: str | None,
    doi: str | None,
    source_pdf: str,
    full_markdown: str,
) -> None:
    """Hub file: exactly seven frontmatter keys, a blank line, then the raw text.

    Serper fields (publication_info / cited_by) live in bibliographic.md only —
    the hub carries bibliographic identity, not lookup results.
    """
    authors_yaml = "[" + ", ".join(_yaml_str(a) for a in authors) + "]"

    fm_lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"authors: {authors_yaml}",
        f"year: {year if year is not None else 'null'}",
        f"journal: {_yaml_str(journal or '')}",
        f"doi: {_yaml_str(doi or '')}",
        f"slug: {_yaml_str(slug)}",
        f"source_pdf: {_yaml_str(source_pdf)}",
        "---",
        "",
        "",
    ]
    content = "\n".join(fm_lines) + full_markdown
    (folder / f"{slug}.md").write_text(content, encoding="utf-8")


def _write_bibliographic_md(
    folder: Path,
    title: str,
    authors: list[str],
    year: int | None,
    journal: str,
    doi: str,
    slug: str,
    source_pdf: str,
    serper: dict,
    content_source: str = "",
) -> None:
    publication_info = serper.get("publication_info") or ""
    cited_by = serper.get("cited_by")
    authors_yaml = "[" + ", ".join(_yaml_str(a) for a in authors) + "]"

    fm_lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"authors: {authors_yaml}",
        f"year: {year if year is not None else 'null'}",
        f"journal: {_yaml_str(journal)}",
        f"doi: {_yaml_str(doi)}",
        f"slug: {_yaml_str(slug)}",
        f"source_pdf: {_yaml_str(source_pdf)}",
        f"content_source: {_yaml_str(content_source)}",
        f"publication_info: {_yaml_str(publication_info)}",
        f"cited_by: {cited_by if cited_by is not None else 'null'}",
        "---",
        "",
    ]

    if authors:
        formatted = [_fmt_author(a) for a in authors]
        if len(formatted) == 1:
            author_str = formatted[0]
        elif len(formatted) == 2:
            author_str = f"{formatted[0]}, & {formatted[1]}"
        else:
            author_str = ", ".join(formatted[:-1]) + f", & {formatted[-1]}"
    else:
        author_str = ""

    year_str = f"({year})" if year else "(n.d.)"
    journal_part = f"*{journal}*" if journal else ""
    doi_part = f" https://doi.org/{doi}" if doi else ""

    parts = [p for p in [author_str, year_str, f"{title}.", journal_part] if p]
    citation = " ".join(parts) + doi_part

    content = "\n".join(fm_lines) + "## Citation\n\n" + citation + "\n"
    (folder / "bibliographic.md").write_text(content, encoding="utf-8")


# ── Progress tracking ─────────────────────────────────────────────────────────

_progress_lock = threading.RLock()


def load_progress(output_dir: Path) -> set[str]:
    p = output_dir / ".progress.json"
    if p.exists():
        return set(json.loads(p.read_text(encoding="utf-8")))
    return set()


def save_progress(output_dir: Path, done: set[str]) -> None:
    with _progress_lock:
        p = output_dir / ".progress.json"
        p.write_text(json.dumps(sorted(done), indent=2), encoding="utf-8")


# ── Input discovery ───────────────────────────────────────────────────────────

# Everything readers.read can handle. `.doc` is deliberately excluded: the
# reader claims it, but python-docx cannot open the legacy binary format, so it
# would fail deep inside the pipeline instead of being reported up front.
SUPPORTED_SUFFIXES = (".pdf", ".docx", ".md", ".txt")


def collect_inputs(path: Path, recursive: bool = False) -> list[Path]:
    """Resolve a file or folder into the list of documents to process."""
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_SUFFIXES else []
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in path.glob(pattern)
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def zip_vault(vault_dir: Path) -> bytes:
    """Zip a built vault, preserving empty directories.

    `notes/` is empty in a freshly built entry, and a file-only zip would drop
    it — so directory entries are written explicitly.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(vault_dir.rglob("*")):
            rel = p.relative_to(vault_dir).as_posix()
            if p.is_dir():
                info = zipfile.ZipInfo(rel + "/")
                info.external_attr = (0o40755 << 16) | 0x10
                zf.writestr(info, b"")
            else:
                zf.write(p, rel)
    return buf.getvalue()


# ── Main batch function ───────────────────────────────────────────────────────

def build_vault(
    input_dir: Path,
    output_dir: Path,
    api_key: str,
    serper_key: str = "",
    workers: int = 4,
    model: str = "mistral-large-latest",
    ocr_provider=None,
    chat_provider=None,
    files: list[Path] | None = None,
    progress_cb: Callable[[str, int, int], None] | None = None,
    write_progress: bool = True,
    content_source: str = "",
) -> list[dict]:
    """Process documents and write vault entries to output_dir.

    `files` overrides discovery in `input_dir` (used by the web UI, which hands
    over uploaded temp files). `progress_cb(filename, done, total)` is called
    after each document. `write_progress=False` suppresses `.progress.json`, so
    the output folder contains nothing but the {slug}/ entries.

    Returns one result dict per document: slug/title/year/authors/error.
    """
    from . import readers
    from . import metadata_extractor

    output_dir.mkdir(parents=True, exist_ok=True)
    done = load_progress(output_dir) if write_progress else set()

    docs = files if files is not None else collect_inputs(input_dir)
    if not docs:
        print(f"No supported documents found in {input_dir}")
        return []

    pending = [p for p in docs if p.name not in done]
    print(f"Found {len(docs)} document(s); {len(pending)} to process, {len(done)} already done.")

    print_lock = threading.Lock()
    results: list[dict] = []
    completed = 0
    total = len(pending)

    def process_one(doc: Path) -> dict:
        try:
            with print_lock:
                print(f"  [{doc.name}] OCR...")
            markdown = readers.read(doc, api_key=api_key, ocr_provider=ocr_provider)

            with print_lock:
                print(f"  [{doc.name}] Metadata...")
            meta = metadata_extractor.extract(
                markdown, model=model, api_key=api_key, chat_provider=chat_provider
            )

            title = meta.get("title") or doc.stem
            authors: list[str] = meta.get("authors") or []
            year: int | None = meta.get("year")
            journal: str | None = meta.get("source")
            doi: str | None = meta.get("doi")

            # Enrich before slugging: Scholar can supply a year the PDF lacks,
            # which is the difference between Author_2025_ and Author_XXXX_.
            serper: dict = {}
            if serper_key:
                serper = enrich_via_serper(doi, title, authors, serper_key)
            if year is None:
                year = serper.get("year")

            slug = make_slug(authors, year, title)

            folder = write_vault_entry(
                output_dir=output_dir,
                slug=slug,
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                doi=doi,
                source_pdf=doc.name,
                full_markdown=markdown,
                serper=serper,
                content_source=content_source,
            )

            if write_progress:
                with _progress_lock:
                    done.add(doc.name)
                    save_progress(output_dir, done)

            with print_lock:
                print(f"  OK  {slug}")
            abstract, _, references = split_content(markdown)
            return {
                "source": doc.name,
                "slug": slug,
                "folder": str(folder),
                "title": title,
                "authors": authors,
                "year": year,
                "has_abstract": bool(abstract),
                "has_references": bool(references),
                "error": None,
            }

        except Exception as exc:
            with print_lock:
                print(f"  FAIL {doc.name}: {exc}", file=sys.stderr)
            return {"source": doc.name, "slug": None, "error": str(exc)}

    def record(result: dict) -> None:
        nonlocal completed
        results.append(result)
        completed += 1
        if progress_cb is not None:
            progress_cb(result["source"], completed, total)

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_one, p): p for p in pending}
            for future in as_completed(futures):
                record(future.result())
    else:
        for doc in pending:
            record(process_one(doc))

    succeeded = sum(1 for r in results if r.get("slug"))
    print(f"\nDone. {succeeded}/{len(pending)} articles in vault at {output_dir}")
    return results
