"""Disk-backed page cache and the ``recipe`` that keys it.

Why disk (not memory): the ``online_document`` datasource is invoked as two
separate calls -- ``_get_pages`` (light listing) then, per selected page,
``_get_content``. The plugin process can be recycled between them (idle restart),
so an in-memory map built during ``_get_pages`` would be gone by ``_get_content``.
Rendered page content therefore lives in an on-disk SQLite file that survives a
process restart.

The **recipe** is the normalised set of datasource parameters (source URL + field
mapping + filters, no credentials). It is:

* hashed into the cache key (so unrelated datasources never share a cache), and
* encoded into the ``workspace_id`` that round-trips through Dify, so
  ``_get_content`` can fully rebuild the cache from scratch even if the SQLite
  file was evicted -- it never receives the datasource parameters directly, only
  ``workspace_id`` / ``page_id`` / ``type``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .http import Fetch, default_fetch

# Cache directory name under the system temp dir. One SQLite file per recipe key.
_CACHE_DIRNAME = "dify-datasource-ndjson-cache"

# Recipe string fields, normalised to a stripped value or None (empty -> None).
_STR_FIELDS = (
    "source_url",
    "sections",
    "langs",
    "url_field",
    "title_field",
    "content_field",
    "filter_field",
    "filter_value",
)


def normalize_recipe(params: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalise datasource parameters into a stable recipe dict.

    Deterministic for a given logical config (stripped strings, an ordered
    ``metadata_fields`` list, an int-or-None ``max_records``, a bool
    ``verify_checksum``) so ``recipe_key`` is stable across calls. ``source_url``
    is required.
    """
    recipe: dict[str, Any] = {}
    for name in _STR_FIELDS:
        value = str(params.get(name) or "").strip()
        recipe[name] = value or None
    if not recipe["source_url"]:
        raise ValueError("source_url is required")

    raw_meta = params.get("metadata_fields")
    if isinstance(raw_meta, str):
        meta = [p.strip() for p in raw_meta.split(",") if p.strip()]
    elif isinstance(raw_meta, (list, tuple)):
        meta = [str(p).strip() for p in raw_meta if str(p).strip()]
    else:
        meta = []
    recipe["metadata_fields"] = meta

    raw_max = params.get("max_records")
    recipe["max_records"] = int(raw_max) if raw_max else None
    recipe["verify_checksum"] = bool(params.get("verify_checksum", True))
    return recipe


def _canonical_bytes(recipe: Mapping[str, Any]) -> bytes:
    return json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")


def recipe_key(recipe: Mapping[str, Any]) -> str:
    """Stable short hex key for a normalised recipe (namespaces the cache file)."""
    return hashlib.sha256(_canonical_bytes(recipe)).hexdigest()[:24]


def encode_recipe(recipe: Mapping[str, Any]) -> str:
    """Encode a normalised recipe into an opaque ``workspace_id`` token."""
    return base64.urlsafe_b64encode(_canonical_bytes(recipe)).decode("ascii")


def decode_recipe(token: str) -> dict[str, Any]:
    """Inverse of :func:`encode_recipe`. Raises ``ValueError`` on a bad token."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        recipe = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid workspace token: {exc}") from exc
    if not isinstance(recipe, dict) or not recipe.get("source_url"):
        raise ValueError("invalid workspace token: missing source_url")
    return recipe


class _Writer:
    """Insert handle yielded by :meth:`PageCache.writer`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.count = 0

    def add(self, page_id: str, title: str, content: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pages (page_id, title, content) VALUES (?, ?, ?)",
            (page_id, title, content),
        )
        self.count += 1


class PageCache:
    """A SQLite-backed ``page_id -> (title, content)`` store for one recipe."""

    def __init__(self, key: str, *, root: Path | None = None) -> None:
        self.key = key
        self.root = root or Path(tempfile.gettempdir()) / _CACHE_DIRNAME
        self.path = self.root / f"{key}.sqlite"

    def exists(self) -> bool:
        return self.path.exists()

    @contextmanager
    def writer(self) -> Iterator[_Writer]:
        """Open a write transaction, replacing any previous contents.

        The ``DELETE`` + inserts commit atomically only on clean exit, so a crash
        mid-build never leaves a partially-populated cache visible to readers.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pages "
                "(page_id TEXT PRIMARY KEY, title TEXT, content TEXT)"
            )
            conn.execute("DELETE FROM pages")
            writer = _Writer(conn)
            yield writer
            conn.commit()
        finally:
            conn.close()

    def get(self, page_id: str) -> tuple[str, str] | None:
        """Return ``(title, content)`` for ``page_id`` or ``None`` on a miss."""
        if not self.path.exists():
            return None
        conn = sqlite3.connect(self.path)
        try:
            row = conn.execute(
                "SELECT title, content FROM pages WHERE page_id = ?", (page_id,)
            ).fetchone()
        finally:
            conn.close()
        return (row[0], row[1]) if row else None


def populate(cache: PageCache, recipe: Mapping[str, Any], *, fetch: Fetch = default_fetch) -> int:
    """(Re)build ``cache`` from ``recipe`` by re-fetching and parsing the source.

    Used by the ``_get_content`` cache-miss path: re-download the dump once,
    repopulate the cache, and let subsequent page reads hit the ready cache.
    Returns the number of pages written.
    """
    from .records import pages_from_recipe

    with cache.writer() as writer:
        for page in pages_from_recipe(dict(recipe), fetch=fetch):
            writer.add(page.page_id, page.title, page.content)
        return writer.count
