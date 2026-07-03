from __future__ import annotations

import json

from ndjson_source.manifest import (
    is_manifest,
    load_manifest,
    select_exports,
)

from tests.conftest import gz, ndjson_bytes


def _manifest(exports: list[dict]) -> bytes:
    return json.dumps({"version": 1, "exports": exports}).encode("utf-8")


def test_is_manifest_detects_json_with_exports():
    raw = _manifest([{"url": "man1.en.ndjson.gz"}])
    assert is_manifest(raw) is True


def test_is_manifest_rejects_plain_ndjson():
    raw = ndjson_bytes([{"a": 1}, {"b": 2}])
    assert is_manifest(raw) is False


def test_is_manifest_rejects_single_json_object_without_exports():
    # A one-line NDJSON file that is a single object must not be taken as a manifest.
    raw = ndjson_bytes([{"url": "x", "text": "hi"}])
    assert is_manifest(raw) is False


def test_is_manifest_rejects_gzip_stream():
    raw = gz(ndjson_bytes([{"a": 1}]))
    assert is_manifest(raw) is False


def test_load_manifest_resolves_relative_urls_and_fields():
    raw = _manifest(
        [
            {"url": "man5.ru.ndjson.gz", "section": 5, "lang": "ru", "sha256": "ABCD"},
            {"url": "https://cdn.example/man1.en.ndjson.gz", "section": "1", "lang": "en"},
        ]
    )
    exports = load_manifest(raw, "https://host.example/exports/index.json")
    assert exports[0].url == "https://host.example/exports/man5.ru.ndjson.gz"
    assert exports[0].section == "5" and exports[0].lang == "ru"
    assert exports[0].sha256 == "abcd"  # normalised to lowercase
    assert exports[1].url == "https://cdn.example/man1.en.ndjson.gz"
    assert exports[1].sha256 is None


def test_select_exports_filters_by_sections_and_langs():
    raw = _manifest(
        [
            {"url": "a", "section": "1", "lang": "en"},
            {"url": "b", "section": "5", "lang": "ru"},
            {"url": "c", "section": "8", "lang": "ru"},
            {"url": "d", "section": "5", "lang": "en"},
        ]
    )
    exports = load_manifest(raw, "https://h/exports/i.json")

    got = select_exports(exports, sections="5,8", langs="ru")
    urls = [e.url.rsplit("/", 1)[-1] for e in got]
    assert urls == ["b", "c"]


def test_select_exports_empty_filter_matches_all():
    raw = _manifest([{"url": "a", "section": "1"}, {"url": "b", "section": "5"}])
    exports = load_manifest(raw, "https://h/i.json")
    assert len(select_exports(exports)) == 2


def test_select_exports_keeps_entries_missing_attribute():
    # An export without a section must not be dropped by a sections filter.
    raw = _manifest([{"url": "a"}, {"url": "b", "section": "5"}])
    exports = load_manifest(raw, "https://h/i.json")
    got = select_exports(exports, sections="5")
    urls = [e.url.rsplit("/", 1)[-1] for e in got]
    assert urls == ["a", "b"]
