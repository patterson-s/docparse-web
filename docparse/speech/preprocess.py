"""docparse speech preprocessing (Phase 0).

Turns parsed academic markdown into a *spoken script* — a ``*_for_voice.md``
file that a TTS stage can read aloud: in-text citations stripped, boilerplate
removed, headings normalized as cue points, markdown cleaned for speech.

Deterministic and network-free (pure functions on strings), mirroring the
``noise_filter`` conventions of this repo. Originals are never modified; the
output is always a new file.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SUPERSCRIPT_DIGITS = "¹²³⁴⁵⁶⁷⁸⁹⁰"
_CITE_YEAR = r"(?:19|20)\d{2}"

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")

# Line-level boilerplate (noise_filter style, extended for book front matter).
BOILERPLATE_LINES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # Journal metadata / publisher boilerplate
        r"^DOI:?\s*10\.\d{4,}/\S+$",
        r"^doi:\s*10\.\d{4,}/\S+$",
        r"^ISSN[:.]?\s*\S+$",
        r"^e-ISSN[:.]?\s*\S+$",
        r"^ISBN[- ]?\d[\d\-Xx]{9,}$",
        r"^A CIP catalog record.*$",
        r"^COPYRIGHT\s*©\s*\d{4}.*$",
        r"^©\s*\d{4}.*$",
        r"^All rights reserved$",
        r"^This book may not be reproduced.*$",
        r"^Printed on acid-free paper$",
        r"^Manufactured in .*$",
        r"^Published in the United States of America by the$",
        r"^\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\s+\d+\s+\d+\s+\d+\s+\d+$",  # "2018 2017 2016 2015 4 3 2 1"
        r"^Titles in the series:$",
        r"^Patrick Thaddeus Jackson, series editor$",
        r"^\*?To my family:.*$",
        r"^PUBLISHED\s+\d{1,2}\s+\w+\s+\d{4}$",
        r"^RECEIVED\s+\d{1,2}\s+\w+\s+\d{4}$",
        r"^ACCEPTED\s+\d{1,2}\s+\w+\s+\d{4}$",
        r"^EDITED BY\s+.+$",
        r"^REVIEWED BY\s+.+$",
        r"^OPEN ACCESS$",
        r"^Check for updates$",
        r"^Volume\s+\d+,\s*No\.\s*\d+.*\(\d+-\d+\)$",
        r"^Issue\s+\d+/\d+\s*[•·]\s*\w+\s+\d{4}$",
        # URLs / emails
        r"^https?://\S+$",
        r"^[\w.-]+@[\w.-]+\.\w+$",
        r"^www\.\S+\.\S+$",
        # Page artifacts
        r"^---\s*Page\s+\d+\s*---$",
        r"^Page\s+\d+(\s+of\s+\d+)?$",
        r"^p\.\s*\d+$",
        r"^Lone page numbers$",  # placeholder, never matches; kept for clarity
        r"^\d{1,4}$",
        r"^\d+\s*/\s*\d+$",
        # Article header / journal furniture
        r"^E-mail addresses:.*$",
        r"^Funding\b.*$",
        r"^ARTICLE INFO$",
        r"^Keywords[:]?\s*.*$",
        r"^Received\b.*$",
        r"^Revised\b.*$",
        r"^Accepted\b.*$",
        r"^Published online\b.*$",
        r"^Available online\b.*$",
        r"^ABSTRACT\s*:?$",
        r"^Springer$",
        r"^Contents lists available at \w+$",
        r"^[A-Z][\w \-']+ \d{1,2} \(\d{4}\) \d+$",  # "Telecommunications Policy 45 (2021) 102149"
        r"^[A-ZÀ-Ý][\w ’'\-]+ \(\d{4}\) \d+:\d+[–-]\d+$",  # "Studies in ... (2023) 58:347–368"
        r"^[A-ZÀ-Ý][\w ’'&:.,\-]{2,60}\s+\d{1,3}$",  # running head + page number: "Introduction 7"
        r"^\d{1,3}\s+[A-ZÀ-Ý][\w ’'&:.,\-]{2,60}$",  # page number + running head: "4 Politics of Expertise"
        r"^.*homepage:?\s*https?://\S+$",
        r"^.*https?://\S+\s*$",
        r"^---\s+.+$",  # running-head fragments: "--- 124 Politics of Expertise"
    ]
]

# Section headings whose whole block is dropped. Front matter = drop until the
# next heading; back matter (end-of-document sections) = sticky: drop to EOF.
FRONT_MATTER_DROP = {
    "contents",
    "foreword",
    "preface",
    "acknowledgments",
    "acknowledgements",
    "dedication",
}
BACK_MATTER_DROP = {
    "references",
    "bibliography",
    "notes",
    "endnotes",
    "works cited",
    "declarations",
    "declaration of competing interest",
    "competing interests",
    "funding",
    "index",
    "about the author",
    "about the authors",
}
SECTION_DROP = FRONT_MATTER_DROP | BACK_MATTER_DROP

# Inline citation / footnote markers.
_FN_MARKER = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]|\[\d{1,3}\]")
_FN_BLOCK_START = re.compile(r"^[¹²³⁴⁵⁶⁷⁸⁹⁰]\s*")
_RUNNING_HEAD = re.compile(r"^\*[^*\n]{3,90}\*\s+\d{1,3}$")
_HEADING_EMPHASIS = re.compile(r"[*_`]+")


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------

_FM_START = re.compile(r"^---\s*$")


def strip_frontmatter(text: str) -> tuple[str, dict]:
    """Return (text_without_yaml, meta_dict). Tolerant, regex-based (no yaml dep)."""
    lines = text.splitlines()
    if not lines or not _FM_START.match(lines[0]):
        return text, {}
    end = None
    for i in range(1, len(lines)):
        if _FM_START.match(lines[i]):
            end = i
            break
    if end is None:
        return text, {}
    meta: dict = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        if key in ("title", "authors", "year", "journal", "doi", "slug", "source_pdf"):
            meta[key] = value
    return "\n".join(lines[end + 1 :]), meta


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------

_PAREN = re.compile(r"\(([^()]*)\)")

_CITE_FIRST_TOKENS = {"see", "see also", "cf.", "cf", "e.g.", "e.g.,", "eg", "viz."}


def _looks_like_citation(inner: str) -> bool:
    """Gate: is this parenthetical a citation rather than content?

    Must contain a 19xx/20xx year AND look like an author-date reference:
    a capitalized first token (name / acronym / org), or an explicit
    citation lead-in (see / cf. / e.g.), or citation furniture
    (p./pp. page numbers, et al, semicolon-separated cites).
    """
    s = inner.strip()
    if not s or len(s) > 100:
        return False
    if not re.search(_CITE_YEAR, s):
        return False
    first = s.split()[0].rstrip(",") if s.split() else ""
    if first.lower() in _CITE_FIRST_TOKENS:
        return True
    if re.search(r"p{1,2}\.?\s*\d", s) or "et al" in s.lower() or ";" in s or " & " in s:
        return True
    if re.search(r"\b(?:quoted|cited) in\s+[A-ZÀ-Ý]", s):
        return True
    if re.search(r"\bsee\s+(?:[A-ZÀ-Ý]|[a-zà-ÿ]+\s+[A-ZÀ-Ý])", s):  # "see Haas" / "see de Graaff"
        return True
    if re.match(r"^\d{4}(?:/\d{4})?,?\s+\d+", s):  # "(1933, 310)" page refs
        return True
    if not re.search(r"[A-ZÀ-Ý]", s):
        return False
    return bool(re.match(r"^[A-ZÀ-Ý][\w’'.\-&]*", first))


_NESTED_CITE = re.compile(
    r"\([A-ZÀ-Ý][\w’'\-&.]*(?:\s+et al\.?)?\s*\(\s*(?:19|20)\d{2}[^()]*?\)"
)
_TRAILING_SEE = re.compile(
    r"[,;]\s*(?:see|cf\.?)\s+[A-ZÀ-Ý][\w’'\-&.]*(?:\s+(?:and|et al\.?)\s+[A-ZÀ-Ý][\w’'\-&.]*)?"
    r"\s*,?\s*[12]\d{3}[^()]*$"
)
_ARTICLE_NO = re.compile(r"\(#\d+\)")


def _drop_citations(text: str) -> str:
    text = _NESTED_CITE.sub("", text)  # "(Carayannis et al. (2012 132)" (OCR-loose)
    text = _ARTICLE_NO.sub("", text)  # "(#101094394)"

    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        if _looks_like_citation(inner):
            return ""
        trimmed = _TRAILING_SEE.sub("", inner)
        if trimmed != inner:
            return f"({trimmed})"  # long aside: keep content, drop trailing "see X 2016"
        return m.group(0)

    out = _PAREN.sub(_repl, text)
    return out


_NARRATIVE_YEAR = re.compile(
    r"(\b[A-ZÀ-Ý][\w’'\-]*(?:\s+(?:and|&)\s+[A-ZÀ-Ý][\w’'\-]*|\s+et al\.?)?)"
    r"\s*\((?:19|20)\d{2}[^()]*?\)"
)


def _drop_narrative_years(text: str) -> str:
    return _NARRATIVE_YEAR.sub(r"\1", text)


def _drop_footnote_markers(text: str) -> str:
    return _FN_MARKER.sub("", text)


def strip_citations(text: str) -> str:
    """Remove author-date citations, narrative years, footnote markers."""
    text = _drop_citations(text)
    text = _drop_narrative_years(text)
    text = _drop_footnote_markers(text)
    # Clean up whitespace/punctuation artifacts left by removals.
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# --------------------------------------------------------------------------
# Markdown cleaning (speech-friendly)
# --------------------------------------------------------------------------

_INLINE_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_EMPH = re.compile(r"(\*\*|__|\*|_|`)(?=\S)(.*?)(?<=\S)\1")
_BLOCKQUOTE = re.compile(r"^>\s?", re.MULTILINE)
_LIST_MARK = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUM_LIST_MARK = re.compile(r"^\s*\d{1,3}[.)]\s+", re.MULTILINE)
_HR = re.compile(r"^\s*(---+|\*\*\*+)\s*$", re.MULTILINE)
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

_MATH_TOKENS = {
    "²": " squared",
    "³": " cubed",
    "≈": " approximately ",
    "×": " times ",
    "±": " plus or minus ",
    "°C": " degrees Celsius",
    "½": " one half ",
    "¼": " one quarter ",
    "→": " to ",
    "⇒": " implies ",
}


def clean_markdown(text: str) -> str:
    text = _HTML_COMMENT.sub("", text)
    text = _HR.sub("", text)
    text = _TABLE_LINE.sub("", text)
    # Images: keep the alt text as a figure cue; drop the URL.
    text = _IMAGE.sub(lambda m: f"[figure: {m.group(1)}]" if m.group(1) else "[figure omitted]", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _INLINE_URL.sub("", text)
    text = _EMAIL.sub("", text)
    text = _EMPH.sub(r"\2", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_MARK.sub("", text)
    text = _NUM_LIST_MARK.sub("", text)
    for k, v in _MATH_TOKENS.items():
        text = text.replace(k, v)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# --------------------------------------------------------------------------
# Headings
# --------------------------------------------------------------------------

_SHORT_WORDS = {"of", "the", "and", "in", "on", "for", "to", "a", "an", "or", "vs", "et"}


def _title_case(heading: str) -> str:
    """Convert ALL-CAPS headings to Title Case (TTS-friendly)."""
    if not heading.isupper() or len(heading) < 6:
        return heading
    words = heading.split()
    if len(words) == 1:
        return heading if len(words[0]) <= 4 else words[0].capitalize()
    out = []
    for i, w in enumerate(words):
        w = w.strip(":;,.!?")
        if i > 0 and w.lower() in _SHORT_WORDS:
            out.append(w.lower())
        elif w.isupper() and (len(w) <= 3 or re.fullmatch(r"[IVXLCDM]+", w)):
            out.append(w)  # keep acronyms / roman numerals: "II", "HLEG"
        else:
            out.append(w[0].upper() + w[1:].lower() if w else w)
    return " ".join(out)


def _norm_heading(text: str) -> str:
    t = _HEADING_EMPHASIS.sub("", text)
    t = t.strip().strip(":;").lower()
    return re.sub(r"\s+", " ", t)


def _fuzzy_title_match(heading_norm: str, title_norm: str) -> bool:
    if not title_norm:
        return False
    if heading_norm == title_norm:
        return True
    if len(title_norm) >= 8 and (title_norm.startswith(heading_norm) or heading_norm.startswith(title_norm)):
        return True
    hs, ts = set(heading_norm.split()), set(title_norm.split())
    if hs and len(hs & ts) / len(hs) >= 0.8:
        return True
    return False


def _is_series_junk(text: str) -> bool:
    """ALL-CAPS heading that is not a short acronym — likely a series/page title."""
    stripped = text.strip()
    if not stripped or len(stripped) < 6:
        return False
    if re.match(r"^(part|chapter|section)\s+[ivxlcdm\d]+", stripped, re.IGNORECASE):
        return False  # "PART I" / "CHAPTER 2" are real content headings
    return stripped.isupper() and any(c.isalpha() for c in stripped) and len(stripped.split()) > 1


def _is_repeat_title(norm: str, title_norm: str) -> bool:
    """Strict check: only exact/prefix title repeats are boilerplate (not shared words)."""
    if not title_norm:
        return False
    if norm == title_norm:
        return True
    return len(norm) >= 10 and title_norm.startswith(norm)


_BARE_SECTION_WORDS = {
    "introduction",
    "conclusion",
    "conclusions",
    "methods",
    "methodology",
    "results",
    "discussion",
    "abstract",
    "background",
    "findings",
}


def _is_byline(line: str) -> bool:
    """Author byline / affiliation line (name-shaped, no sentence punctuation)."""
    s = line.strip()
    if not s or len(s) < 6 or len(s) > 130:
        return False
    if re.search(r"[.!?;:—]$", s):
        return False
    if re.search(r"\d", s) and not re.search(
        r"university|school of|institute|department|college|centre|center", s, re.IGNORECASE
    ):
        return False
    clean = re.sub(r"[·,]", "", s)
    if re.match(r"^[A-ZÀ-Ý][\w’'\-.]*(?:\s+[A-ZÀ-Ý][\w’'\-.]*){1,8}$", clean):
        return True
    return bool(
        re.search(r"university|school of|institute|department|college", s, re.IGNORECASE)
        and len(s.split()) <= 14
    )


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------


def to_voice_script(text: str, meta: dict | None = None, source_note: str | None = None) -> str:
    """Convert parsed academic markdown into a spoken script."""
    meta = dict(meta or {})
    body, fm_meta = strip_frontmatter(text)
    if not meta:
        meta = fm_meta

    lines = body.splitlines()
    title_norm = _norm_heading(meta.get("title", ""))
    anchor = _find_anchor(lines, title_norm)

    kept: list[str] = []
    skip_until_heading = False
    sticky_drop = False
    in_keywords = False
    seen_content_heading = False
    for i, line in enumerate(lines):
        if i < anchor:
            continue
        if sticky_drop:
            continue
        h = HEADING_RE.match(line)
        if h:
            in_keywords = False
            level = len(h.group(1))
            raw_text = h.group(2).strip()
            norm = _norm_heading(raw_text)
            is_drop = (
                norm in SECTION_DROP
                or _is_series_junk(raw_text)
                or (title_norm and _is_repeat_title(norm, title_norm) and seen_content_heading)
            )
            if is_drop:
                skip_until_heading = True
                if norm in BACK_MATTER_DROP:
                    sticky_drop = True
                continue
            skip_until_heading = False
            seen_content_heading = True
            kept.append(f"{'#' * level} {_title_case(_HEADING_EMPHASIS.sub('', raw_text).strip())}")
            continue
        if skip_until_heading:
            continue
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _FN_BLOCK_START.match(stripped):
            continue  # footnote block line
        if _RUNNING_HEAD.match(stripped):
            continue  # italic running head + page number
        clean_line = _HEADING_EMPHASIS.sub("", stripped)  # "**Keywords** X" -> "Keywords X"
        if clean_line.upper() == "ARTICLE INFO":
            continue
        if re.match(r"^Keywords[:]?\s*", clean_line, re.IGNORECASE):
            in_keywords = True
            continue
        if in_keywords:
            # Keyword items are short capitalised phrases without sentence
            # punctuation; stop dropping at the first real sentence.
            if re.search(r"[.!?]\s*$", clean_line) or len(clean_line) > 120:
                in_keywords = False
            else:
                continue
        if any(p.match(clean_line) for p in BOILERPLATE_LINES):
            continue
        if _is_byline(clean_line):
            continue
        m = re.match(r"^(\d{1,3})[.)]\s+([A-ZÀ-Ý][\w ’'&:.\-]{1,50})$", clean_line)
        if m and len(clean_line) <= 60:
            kept.append(f"## {m.group(2)}")  # numbered section header: "1. Introduction"
            continue
        if clean_line.lower() in _BARE_SECTION_WORDS and len(clean_line) < 30:
            kept.append(f"## {clean_line}")
            continue
        kept.append(clean_line)

    script = "\n".join(kept)
    script = strip_citations(script)
    script = clean_markdown(script)

    # Assemble the final spoken-script file.
    title = meta.get("title") or "Untitled"
    header = [f"# {title} — for voice", "<!-- docparse speech preprocessor (Phase 0). Do not edit; regenerate. -->"]
    if source_note:
        header.append(f"<!-- source: {source_note} -->")
    header.append("<!-- Headings are cue points for the TTS stage (chunk boundaries + pauses). -->")
    intro = _build_intro(meta)
    if intro:
        header.append("")
        header.append(f"> **Audio intro:** {intro}")
    header.append("")
    return "\n".join(header) + script + "\n"


def _fmt_authors(authors: str) -> str:
    """'["A", "B"]' (yaml list string) -> 'A, and B'; plain strings pass through."""
    a = (authors or "").strip()
    if not a:
        return ""
    if a.startswith("["):
        items = [x.strip().strip("\"'") for x in a[1:-1].split(",") if x.strip()]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + ", and " + items[-1]
    return a


def _build_intro(meta: dict) -> str:
    parts = []
    title = meta.get("title")
    authors = _fmt_authors(str(meta.get("authors", "")))
    year = meta.get("year")
    journal = meta.get("journal") or meta.get("source")
    if title:
        parts.append(f'"{title}"')
    if authors:
        parts.append(f"by {authors}")
    if year:
        parts.append(str(year))
    if journal:
        parts.append(journal)
    return ", ".join(parts)


def _find_anchor(lines: list[str], title_norm: str) -> int:
    """Index of the first content heading; front matter before it is dropped."""
    seen_title = False
    for i, line in enumerate(lines):
        h = HEADING_RE.match(line)
        if not h:
            continue
        level = len(h.group(1))
        raw = h.group(2).strip()
        norm = _norm_heading(raw)
        if norm in SECTION_DROP or _is_series_junk(raw):
            continue
        if title_norm and _fuzzy_title_match(norm, title_norm):
            seen_title = True
            return i
        if level == 1 and re.match(r"^(part|chapter|introduction|abstract)\b", norm):
            return i
        if level == 1:
            return i
    return 0


# --------------------------------------------------------------------------
# File-level helpers (used by the CLI)
# --------------------------------------------------------------------------


def process_file(src: Path, out_dir: Path | None = None) -> Path:
    """Preprocess one md file -> <stem>_for_voice.md (never modifies the source)."""
    from pathlib import Path as _P

    src = _P(src)
    out = out_dir or src.parent
    out.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    script = to_voice_script(text, source_note=src.name)
    dest = out / f"{src.stem}_for_voice.md"
    dest.write_text(script, encoding="utf-8")
    return dest


def process_case(case_dir: Path, out_dir: Path | None = None) -> list[Path]:
    """Preprocess an article case folder (abstract.md + body.md + main md).

    Composes the spoken script from the split files when present; falls back
    to the main <dirname>.md. Never touches references.md, bibliographic.md,
    notes_*.md, or any *_for_voice.md.
    """
    from pathlib import Path as _P

    case_dir = _P(case_dir)
    out = out_dir or case_dir
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    main_md = case_dir / f"{case_dir.name}.md"
    if not main_md.exists():
        cands = [p for p in case_dir.glob("*.md") if p.name not in _NON_CASE_FILES]
        main_md = cands[0] if cands else None
    meta: dict = {}
    if main_md and main_md.exists():
        _, meta = strip_frontmatter(main_md.read_text(encoding="utf-8"))

    parts: list[str] = []
    source = ""
    abstract = case_dir / "abstract.md"
    body = case_dir / "body.md"
    if abstract.exists() and body.exists():
        abs_text = abstract.read_text(encoding="utf-8").strip()
        abs_text = re.sub(r"^#{1,3}\s*Abstract\s*\n+", "", abs_text, flags=re.IGNORECASE)
        if abs_text:
            parts.append(f"# Abstract\n\n{abs_text}")
            source = f"{abstract.name} + {body.name}"
        parts.append(body.read_text(encoding="utf-8"))
        source = source or body.name
    elif main_md and main_md.exists():
        parts.append(main_md.read_text(encoding="utf-8"))
        source = main_md.name
    else:
        return written

    slug = meta.get("slug") or case_dir.name
    script = to_voice_script("\n\n".join(parts), meta=meta, source_note=source)
    dest = out / f"{slug}_for_voice.md"
    dest.write_text(script, encoding="utf-8")
    written.append(dest)
    return written


_NON_CASE_FILES = {
    "abstract.md",
    "body.md",
    "references.md",
    "bibliographic.md",
}


def process_dir(directory: Path, out_dir: Path | None = None) -> list[Path]:
    """Process a case folder, or every eligible md in a generic directory."""
    from pathlib import Path as _P

    directory = _P(directory)
    written: list[Path] = []
    if (directory / "abstract.md").exists() or (directory / "body.md").exists() or (directory / f"{directory.name}.md").exists():
        return process_case(directory, out_dir)
    for md in sorted(directory.glob("*.md")):
        name = md.name
        if name in _NON_CASE_FILES or name.endswith("_for_voice.md") or name.startswith("notes_") or "notes_" in name:
            continue
        written.append(process_file(md, out_dir))
    return written
