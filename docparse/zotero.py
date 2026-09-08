"""Zotero Local API client: diff a collection's PDFs and write .md notes back.

Reads need only Zotero 7+ with Settings -> Advanced -> "Allow other applications
on this computer to communicate with Zotero" enabled. Writes
(attach_markdown_note) additionally need Zotero 10+ and a one-time in-app grant.

The Zotero Local API is a read/write JSON API served by the desktop app itself
at http://127.0.0.1:23119/api/ — no cloud, no plugin, no Better BibTeX.

Detection ("already parsed") has NO separate state file: an item is considered
parsed iff one of its note children carries the DOCPARSE_MARKER. The marker
lives with the item, so it survives collection renames / re-syncs. The raw .md
is stored inside that note as an HTML-escaped <pre>.
"""

from __future__ import annotations

import html as _html
import json
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

ZOTERO_API = "http://127.0.0.1:23119/api"
TIMEOUT = 5
DOCPARSE_MARKER = "docparse-md:"
_APP_NAME = "docparse"

STATE_DIR = Path.home() / ".docparse"
KEY_FILE = STATE_DIR / "zotero_key.json"

# name(lower) -> collection key, to avoid hammering the collection list.
_COLLECTIONS_CACHE: dict[str, str] = {}
_lock = threading.Lock()


def _children(item_key: str):
    """Top-level child items (PDF attachments AND notes) of a parent item."""
    r = requests.get(f"{ZOTERO_API}/users/0/items/{item_key}/children", timeout=TIMEOUT)
    return r.json()


# ── availability + collections ────────────────────────────────────────────────

def server_id() -> str | None:
    """Return the server's Zotero-Server-ID header, or None if unreachable."""
    try:
        r = requests.get(f"{ZOTERO_API}/", timeout=TIMEOUT)
        return r.headers.get("Zotero-Server-ID")
    except Exception:
        return None


def zotero_available() -> bool:
    """True if the Zotero Local API answers (Zotero open + local API enabled).

    A 403 "Local API is not enabled" (Zotero running but the setting is off)
    must count as unavailable, not as a live API.
    """
    try:
        r = requests.get(f"{ZOTERO_API}/users/0/collections?limit=1", timeout=TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def list_collections():
    """All top-level collections, sorted case-insensitively by name.

    The Local API nests the name under `data.name` (not a top-level `name`).
    """
    r = requests.get(f"{ZOTERO_API}/users/0/collections", timeout=TIMEOUT)
    colls = r.json()
    return sorted(
        colls,
        key=lambda c: str((c.get("data") or {}).get("name", "")).lower(),
    )


def find_collection(name: str) -> dict | None:
    """Find a collection by name (case-insensitive). Returns its dict or None."""
    want = (name or "").strip().lower()
    if not want:
        return None
    with _lock:
        if want in _COLLECTIONS_CACHE:
            key = _COLLECTIONS_CACHE[want]
            return {"key": key, "name": name.strip()}
    for c in list_collections():
        cname = str((c.get("data") or {}).get("name", "")).lower()
        if cname == want:
            with _lock:
                _COLLECTIONS_CACHE[cname] = c["key"]
            return c
    return None


# ── candidate detection ───────────────────────────────────────────────────────

@dataclass
class ZoteroCandidate:
    item_key: str
    pdf_path: str
    title: str = ""
    authors: str = ""
    year: int | None = None
    already_parsed: bool = False


def file_url_to_path(url: str) -> str:
    """Turn a Zotero file:// URL into a local filesystem path (Windows aware)."""
    p = urlparse(url).path
    if re.match(r"^/[A-Za-z]:", p):  # /C:/... on Windows -> C:/...
        p = p[1:]
    return unquote(p)


def _is_parsed(children) -> bool:
    """True if any child note carries the docparse marker (already parsed)."""
    for c in children:
        cdata = c.get("data") if isinstance(c.get("data"), dict) else {}
        if cdata.get("itemType") != "note":
            continue
        note = cdata.get("note") or c.get("note") or ""
        if DOCPARSE_MARKER in note:
            return True
    return False


def check_collection(collection_key: str) -> list[ZoteroCandidate]:
    """Diff a collection: one candidate per distinct PDF attachment, flagged parsed.

    Excludes top items with no PDF attachment and dedupes by resolved path.
    """
    tops = requests.get(
        f"{ZOTERO_API}/users/0/collections/{collection_key}/items/top",
        timeout=TIMEOUT,
    ).json()

    out: list[ZoteroCandidate] = []
    seen: set[str] = set()

    for t in tops:
        key = t.get("key", "")
        data = t.get("data") if isinstance(t.get("data"), dict) else {}

        children = _children(key)
        already_parsed = _is_parsed(children)

        title = str(data.get("title", "") or "")
        authors = ", ".join(
            a.get("lastName", "") for a in (data.get("creators") or [])
            if isinstance(a, dict) and a.get("lastName")
        )
        d = str(data.get("date", "") or "")
        year = int(d[:4]) if d[:4].isdigit() else None

        for child in children:
            cdata = child.get("data") if isinstance(child.get("data"), dict) else {}
            if cdata.get("contentType") != "application/pdf":
                continue
            ckey = child.get("key", "")
            try:
                body = requests.get(
                    f"{ZOTERO_API}/users/0/items/{ckey}/file/view/url",
                    timeout=TIMEOUT,
                ).text.strip()
            except Exception:
                continue
            path = file_url_to_path(body) if body else ""
            if not path or path in seen:
                continue
            seen.add(path)
            out.append(ZoteroCandidate(
                item_key=key, pdf_path=path, title=title, authors=authors,
                year=year, already_parsed=already_parsed,
            ))
    return out


# ── write path (authorize + attach .md note) ──────────────────────────────────

def _load_key() -> str | None:
    """Read the cached write key (if any)."""
    try:
        if KEY_FILE.exists():
            return json.loads(KEY_FILE.read_text(encoding="utf-8")).get("key")
    except Exception:
        pass
    return None


def _authorize() -> str | None:
    """Ask Zotero to grant a local write key (pops the in-app "Allow docparse?")."""
    sid = server_id()
    if not sid:
        return None
    r = requests.post(
        f"{ZOTERO_API}/local/authorize",
        headers={"Content-Type": "application/json", "Zotero-Server-ID": sid},
        json={"appName": _APP_NAME},
        timeout=TIMEOUT,
    )
    if r.status_code in (403, 429):  # dialog denied / too many dialogs
        return None
    r.raise_for_status()
    key = r.json().get("key")
    if key:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(
            json.dumps({"key": key, "server_id": sid}), encoding="utf-8"
        )
    return key


def attach_markdown_note(item_key: str, md_text: str) -> str:
    """POST a note child holding the raw .md under item_key. Returns child key.

    First write authorizes and caches the key to ~/.docparse/zotero_key.json;
    a consumed key (the user chose "Allow" without "Always Allow") triggers a
    401 on the write, which is retried once after re-granting.
    """
    sid = server_id()
    if not sid:
        raise RuntimeError("Zotero Local API unreachable")

    note = (
        f"<pre data-slug='docparse'>{_html.escape(DOCPARSE_MARKER)}"
        f"\n{_html.escape(md_text)}</pre>"
    )

    for attempt in range(2):
        key = _load_key() or _authorize()
        if not key:
            raise RuntimeError("Zotero write not authorized (dialog denied?)")
        r = requests.post(
            f"{ZOTERO_API}/users/0/items",
            headers={
                "Content-Type": "application/json",
                "Zotero-API-Key": key,
                "Zotero-Server-ID": sid,
                "Zotero-Write-Token": uuid.uuid4().hex[:16],
            },
            json=[{"itemType": "note", "parentItem": item_key, "note": note}],
            timeout=TIMEOUT,
        )
        if r.status_code == 401:  # key consumed -> re-grant and retry once
            if attempt:
                raise RuntimeError("Zotero write still unauthorized after re-grant")
            _authorize()
            continue
        if r.status_code in (200, 201):
            data = r.json()
            # Local API envelope: {"successful": {"0": {"key": ..., ...}, ...}}
            successful = data.get("successful") if isinstance(data, dict) else None
            if isinstance(successful, dict):
                first = next(iter(successful.values()), None)
                if isinstance(first, dict) and first.get("key"):
                    return first["key"]
            if isinstance(data, list) and data:
                return data[0]["key"]
            if isinstance(data, dict) and data.get("key"):
                return data["key"]
            return "ok"
        r.raise_for_status()
    raise RuntimeError("could not attach note")
