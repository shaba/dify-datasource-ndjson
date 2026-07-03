from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin


class ChecksumError(ValueError):
    """Raised when a downloaded export's sha256 does not match the manifest."""


@dataclass
class Export:
    """One export entry from a dump manifest."""

    url: str
    section: str | None = None
    lang: str | None = None
    sha256: str | None = None
    raw: dict[str, Any] | None = None


def is_manifest(raw: bytes) -> bool:
    """Heuristically decide whether ``raw`` is a JSON dump manifest.

    A manifest is a JSON object with a top-level ``exports`` array. A gzip stream
    (magic ``1f 8b``) or line-delimited NDJSON never parses as a single JSON
    object, so both are correctly rejected here.
    """
    if raw[:2] == b"\x1f\x8b":  # gzip -> definitely a data file, not a manifest
        return False
    head = raw.lstrip()[:1]
    if head not in (b"{", b"["):
        return False
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(doc, dict) and isinstance(doc.get("exports"), list)


def load_manifest(raw: bytes, base_url: str) -> list[Export]:
    """Parse a manifest's ``exports`` into ``Export`` objects with absolute URLs."""
    doc = json.loads(raw)
    exports: list[Export] = []
    for entry in doc.get("exports", []):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        section = entry.get("section")
        lang = entry.get("lang")
        exports.append(
            Export(
                url=urljoin(base_url, url),
                section=None if section is None else str(section),
                lang=None if lang is None else str(lang),
                sha256=(str(entry["sha256"]).strip().lower() if entry.get("sha256") else None),
                raw=entry,
            )
        )
    return exports


def _csv_set(value: str | None) -> set[str]:
    return {p.strip() for p in (value or "").split(",") if p.strip()}


def select_exports(
    exports: Iterable[Export],
    *,
    sections: str | None = None,
    langs: str | None = None,
) -> list[Export]:
    """Filter exports by CSV ``sections`` and/or ``langs``.

    An empty filter matches everything. Filtering only excludes an export when
    the corresponding attribute is present *and* not in the requested set, so a
    manifest that omits ``section``/``lang`` is never silently dropped.
    """
    want_sections = _csv_set(sections)
    want_langs = _csv_set(langs)
    selected = []
    for exp in exports:
        if want_sections and exp.section is not None and exp.section not in want_sections:
            continue
        if want_langs and exp.lang is not None and exp.lang not in want_langs:
            continue
        selected.append(exp)
    return selected


def verify_sha256(data: bytes, expected: str) -> None:
    """Raise ``ChecksumError`` if sha256(data) != expected (hex, case-insensitive)."""
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected.strip().lower():
        raise ChecksumError(f"sha256 mismatch: expected {expected}, got {actual}")
