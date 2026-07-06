from __future__ import annotations

import bz2
import gzip
import io
import json
import lzma
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import zstandard

from .http import Fetch, default_fetch
from .manifest import (
    Export,
    is_manifest,
    load_manifest,
    select_exports,
    verify_sha256,
)
from .model import DocumentPage, Page

# A reasonable per-file read timeout; dumps are large so this is > the crawler's.
_FETCH_TIMEOUT = 60


# Compression magic bytes -> codec name. Detection is by content first (so a
# mislabeled/extension-less file still works), then by URL suffix as a fallback.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x1f\x8b", "gzip"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"BZh", "bz2"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
)
_EXT: dict[str, str] = {
    ".gz": "gzip", ".xz": "xz", ".bz2": "bz2", ".zst": "zstd", ".zstd": "zstd",
}


def _detect_codec(data: bytes, url: str) -> str | None:
    """Compression codec for ``data`` (by magic bytes, then URL ext) or None."""
    for magic, name in _MAGIC:
        if data[: len(magic)] == magic:
            return name
    low = url.lower()
    for ext, name in _EXT.items():
        if low.endswith(ext):
            return name
    return None


def _open_stream(data: bytes, codec: str | None):
    src = io.BytesIO(data)
    if codec == "gzip":
        return gzip.GzipFile(fileobj=src)
    if codec == "xz":
        return lzma.LZMAFile(src)
    if codec == "bz2":
        return bz2.BZ2File(src)
    if codec == "zstd":
        return zstandard.ZstdDecompressor().stream_reader(src)
    return src


def iter_lines(data: bytes, url: str = "") -> Iterator[str]:
    """Yield decoded text lines from ``data``, transparently decompressing.

    The codec is auto-detected by magic bytes (gzip / xz / bz2 / zstd), falling
    back to the URL suffix, else plain. Decompression is streamed on demand as
    lines are pulled, so a large compressed dump is never fully materialised as
    decompressed text in memory at once.
    """
    stream = _open_stream(data, _detect_codec(data, url))
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace")
    for line in text:
        yield line.rstrip("\n")


def _matches_filter(rec: dict[str, Any], field: str | None, value: str | None) -> bool:
    """True when ``rec[field]`` equals ``value`` (or no filter is configured).

    Booleans are compared case-insensitively against ``"true"``/``"false"`` so a
    JSON ``true`` matches a ``filter_value`` of ``true`` (the manpages
    ``canonical`` dedupe case)."""
    if not field:
        return True
    if field not in rec:
        return False
    actual = rec[field]
    want = (value or "").strip()
    if isinstance(actual, bool):
        return str(actual).lower() == want.lower()
    return str(actual) == value


def record_to_page(
    rec: dict[str, Any],
    *,
    url_field: str = "url",
    title_field: str = "title",
    content_field: str = "text",
    metadata_fields: Sequence[str] = (),
) -> Page:
    """Map one NDJSON record to a ``Page``.

    The ingested document exposes no free-form metadata field, so requested
    ``metadata_fields`` are inlined as a compact one-line header at the top of the
    content, e.g. ``> section: 5 | package: systemd | lang: ru``.
    """
    content = str(rec.get(content_field) or "")
    if metadata_fields:
        parts = [f"{f}: {rec[f]}" for f in metadata_fields if f in rec and rec[f] is not None]
        if parts:
            content = "> " + " | ".join(parts) + "\n\n" + content
    return Page(
        source_url=str(rec.get(url_field) or ""),
        title=str(rec.get(title_field) or ""),
        description="",
        content=content,
    )


def iter_records(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Parse NDJSON lines into dicts, skipping blank and malformed lines.

    A single corrupt line must never abort the whole ingest, so parse errors are
    swallowed per line."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _pages_from_lines(
    lines: Iterable[str],
    *,
    url_field: str,
    title_field: str,
    content_field: str,
    metadata_fields: Sequence[str],
    filter_field: str | None,
    filter_value: str | None,
) -> Iterator[Page]:
    for rec in iter_records(lines):
        if not _matches_filter(rec, filter_field, filter_value):
            continue
        yield record_to_page(
            rec,
            url_field=url_field,
            title_field=title_field,
            content_field=content_field,
            metadata_fields=metadata_fields,
        )


def iter_pages(
    source_url: str,
    *,
    fetch: Fetch = default_fetch,
    sections: str | None = None,
    langs: str | None = None,
    url_field: str = "url",
    title_field: str = "title",
    content_field: str = "text",
    metadata_fields: Sequence[str] = (),
    filter_field: str | None = None,
    filter_value: str | None = None,
    verify_checksum: bool = True,
    max_records: int | None = None,
) -> Iterator[Page]:
    """Lazily yield ``Page`` objects from an NDJSON dump.

    Auto-detects the source at ``source_url``:

    * a JSON **manifest** (object with an ``exports`` array) -> filter its
      exports by ``sections``/``langs``, then for each selected export download
      its ``url``, optionally verify ``sha256``, and stream its records;
    * a bare **.ndjson** or **.ndjson.gz** -> stream it directly.

    Yields at most ``max_records`` pages (``None`` = no cap). Because it is a lazy
    generator, a consumer can abandon it (e.g. on a time budget) to stop
    downloading further exports immediately.
    """
    emitted = 0

    def _emit(lines: Iterable[str]) -> Iterator[Page]:
        nonlocal emitted
        for page in _pages_from_lines(
            lines,
            url_field=url_field,
            title_field=title_field,
            content_field=content_field,
            metadata_fields=metadata_fields,
            filter_field=filter_field,
            filter_value=filter_value,
        ):
            if max_records is not None and emitted >= max_records:
                return
            yield page
            emitted += 1

    _, _, raw = fetch(source_url, _FETCH_TIMEOUT)

    if is_manifest(raw):
        exports: list[Export] = select_exports(
            load_manifest(raw, source_url), sections=sections, langs=langs
        )
        for exp in exports:
            if max_records is not None and emitted >= max_records:
                return
            _, _, data = fetch(exp.url, _FETCH_TIMEOUT)
            if verify_checksum and exp.sha256:
                verify_sha256(data, exp.sha256)
            yield from _emit(iter_lines(data, exp.url))
    else:
        yield from _emit(iter_lines(raw, source_url))


def iter_document_pages(
    source_url: str,
    *,
    fetch: Fetch = default_fetch,
    sections: str | None = None,
    langs: str | None = None,
    url_field: str = "url",
    title_field: str = "title",
    content_field: str = "text",
    metadata_fields: Sequence[str] = (),
    filter_field: str | None = None,
    filter_value: str | None = None,
    verify_checksum: bool = True,
    max_records: int | None = None,
    page_type: str = "record",
) -> Iterator[DocumentPage]:
    """Wrap :func:`iter_pages`, assigning each record a stable ``page_id``.

    The id is the record's mapped URL when present and unique; otherwise it falls
    back to ``rec:{ordinal}``. Ordinals are deterministic (same source + same
    mapping/filter -> same order), so a later cache rebuild reproduces exactly the
    same ids -- which is what lets ``_get_content`` resolve a page after the cache
    was evicted.
    """
    seen: set[str] = set()
    for ordinal, page in enumerate(
        iter_pages(
            source_url,
            fetch=fetch,
            sections=sections,
            langs=langs,
            url_field=url_field,
            title_field=title_field,
            content_field=content_field,
            metadata_fields=metadata_fields,
            filter_field=filter_field,
            filter_value=filter_value,
            verify_checksum=verify_checksum,
            max_records=max_records,
        )
    ):
        page_id = page.source_url.strip()
        if not page_id or page_id in seen:
            page_id = f"rec:{ordinal}"
        seen.add(page_id)
        yield DocumentPage(
            page_id=page_id, title=page.title, content=page.content, type=page_type
        )


def pages_from_recipe(recipe: dict[str, Any], *, fetch: Fetch = default_fetch) -> Iterator[DocumentPage]:
    """Yield ``DocumentPage`` objects for a normalised ``recipe`` (see
    :func:`ndjson_source.cache.normalize_recipe`).

    Shared by the pages listing (single pass, also populating the cache) and the
    cache-rebuild path, so both produce identical ids and content.
    """
    return iter_document_pages(
        recipe["source_url"],
        fetch=fetch,
        sections=recipe.get("sections"),
        langs=recipe.get("langs"),
        url_field=recipe.get("url_field") or "url",
        title_field=recipe.get("title_field") or "title",
        content_field=recipe.get("content_field") or "text",
        metadata_fields=recipe.get("metadata_fields") or (),
        filter_field=recipe.get("filter_field"),
        filter_value=recipe.get("filter_value"),
        verify_checksum=bool(recipe.get("verify_checksum", True)),
        max_records=recipe.get("max_records"),
    )
