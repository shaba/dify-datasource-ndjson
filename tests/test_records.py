from __future__ import annotations

import bz2
import gzip
import json
import lzma

import pytest
import zstandard

from ndjson_source.manifest import ChecksumError
from ndjson_source.records import (
    iter_lines,
    iter_pages,
    iter_records,
    record_to_page,
)

from tests.conftest import FakeFetch, gz, ndjson_bytes, sha256_hex


def _manifest(exports: list[dict]) -> bytes:
    return json.dumps({"exports": exports}).encode("utf-8")


# --- line / record parsing -------------------------------------------------


def test_iter_lines_plain_and_gzip_equivalent():
    data = ndjson_bytes([{"a": 1}, {"a": 2}])
    assert list(iter_lines(data, "x.ndjson")) == list(iter_lines(gz(data), "x.ndjson.gz"))


@pytest.mark.parametrize(
    "compress,ext",
    [
        (gzip.compress, ".ndjson.gz"),
        (lzma.compress, ".ndjson.xz"),
        (bz2.compress, ".ndjson.bz2"),
        (lambda b: zstandard.ZstdCompressor().compress(b), ".ndjson.zst"),
    ],
)
def test_iter_lines_decompresses_all_codecs(compress, ext):
    data = ndjson_bytes([{"a": 1}, {"a": 2}])
    plain = list(iter_lines(data, "x.ndjson"))
    comp = compress(data)
    # detected by URL extension
    assert list(iter_lines(comp, "x" + ext)) == plain
    # detected by magic bytes even with a plain/misleading name (no ext hint)
    assert list(iter_lines(comp, "x.ndjson")) == plain


def test_iter_records_skips_blank_and_malformed():
    raw = ndjson_bytes(
        [{"n": 1}, {"n": 2}],
        raw_lines=["", "   ", "{not json", "[1,2,3]", '{"n": 3}'],
    )
    recs = list(iter_records(iter_lines(raw)))
    # blank/malformed skipped; a JSON array (non-dict) skipped; dicts kept
    assert [r["n"] for r in recs] == [1, 2, 3]


# --- field mapping ---------------------------------------------------------


def test_record_to_page_default_mapping():
    page = record_to_page({"url": "u", "title": "t", "text": "body"})
    assert (page.source_url, page.title, page.content) == ("u", "t", "body")


def test_record_to_page_custom_fields():
    rec = {"link": "u", "name": "t", "body": "hello"}
    page = record_to_page(
        rec, url_field="link", title_field="name", content_field="body"
    )
    assert page.source_url == "u" and page.title == "t" and page.content == "hello"


def test_record_to_page_metadata_header():
    rec = {"url": "u", "text": "B", "section": 5, "package": "systemd", "lang": "ru"}
    page = record_to_page(
        rec, metadata_fields=["section", "package", "lang", "missing"]
    )
    head, _, body = page.content.partition("\n\n")
    assert head == "> section: 5 | package: systemd | lang: ru"  # missing field omitted
    assert body == "B"


# --- direct ndjson / ndjson.gz --------------------------------------------


def test_iter_pages_direct_ndjson():
    raw = ndjson_bytes([{"url": "a", "title": "A", "text": "1"},
                        {"url": "b", "title": "B", "text": "2"}])
    fetch = FakeFetch({"https://h/dump.ndjson": raw})
    pages = list(iter_pages("https://h/dump.ndjson", fetch=fetch))
    assert [p.source_url for p in pages] == ["a", "b"]


def test_iter_pages_direct_ndjson_gz():
    raw = ndjson_bytes([{"url": "a", "text": "1"}])
    fetch = FakeFetch({"https://h/dump.ndjson.gz": gz(raw)})
    pages = list(iter_pages("https://h/dump.ndjson.gz", fetch=fetch))
    assert pages[0].source_url == "a" and pages[0].content == "1"


# --- canonical filter ------------------------------------------------------


def test_iter_pages_filter_canonical_bool():
    raw = ndjson_bytes(
        [
            {"url": "a", "text": "1", "canonical": True},
            {"url": "b", "text": "2", "canonical": False},
            {"url": "c", "text": "3", "canonical": True},
        ]
    )
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    pages = list(
        iter_pages(
            "https://h/d.ndjson", fetch=fetch,
            filter_field="canonical", filter_value="true",
        )
    )
    assert [p.source_url for p in pages] == ["a", "c"]


def test_iter_pages_no_filter_keeps_all():
    raw = ndjson_bytes([{"url": "a", "text": "1"}, {"url": "b", "text": "2"}])
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    assert len(list(iter_pages("https://h/d.ndjson", fetch=fetch))) == 2


# --- max_records -----------------------------------------------------------


def test_iter_pages_max_records_caps():
    raw = ndjson_bytes([{"url": str(i), "text": "x"} for i in range(10)])
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    pages = list(iter_pages("https://h/d.ndjson", fetch=fetch, max_records=3))
    assert [p.source_url for p in pages] == ["0", "1", "2"]


# --- manifest path ---------------------------------------------------------


def _wire_manifest(verify_ok: bool = True):
    exp1 = ndjson_bytes([{"url": "m5a", "text": "5a", "canonical": True},
                         {"url": "m5b", "text": "5b", "canonical": False}])
    exp2 = ndjson_bytes([{"url": "m1a", "text": "1a", "canonical": True}])
    exp3 = ndjson_bytes([{"url": "m8a", "text": "8a", "canonical": True}])
    g5, g1, g8 = gz(exp1), gz(exp2), gz(exp3)
    base = "https://h/exports/"
    sha5 = sha256_hex(g5) if verify_ok else "00" * 32
    manifest = _manifest(
        [
            {"url": "man5.ru.ndjson.gz", "section": "5", "lang": "ru", "sha256": sha5},
            {"url": "man1.en.ndjson.gz", "section": "1", "lang": "en", "sha256": sha256_hex(g1)},
            {"url": "man8.ru.ndjson.gz", "section": "8", "lang": "ru", "sha256": sha256_hex(g8)},
        ]
    )
    routes = {
        base + "index.json": manifest,
        base + "man5.ru.ndjson.gz": g5,
        base + "man1.en.ndjson.gz": g1,
        base + "man8.ru.ndjson.gz": g8,
    }
    return base + "index.json", FakeFetch(routes)


def test_iter_pages_manifest_filter_and_stream():
    url, fetch = _wire_manifest()
    pages = list(
        iter_pages(url, fetch=fetch, sections="5,8", langs="ru",
                   filter_field="canonical", filter_value="true")
    )
    assert [p.source_url for p in pages] == ["m5a", "m8a"]
    # man1.en was filtered out at the manifest stage -> never fetched.
    assert not any("man1" in c for c in fetch.calls)


def test_iter_pages_manifest_checksum_ok():
    url, fetch = _wire_manifest(verify_ok=True)
    pages = list(iter_pages(url, fetch=fetch, sections="1", verify_checksum=True))
    assert [p.source_url for p in pages] == ["m1a"]


def test_iter_pages_manifest_checksum_mismatch_raises():
    url, fetch = _wire_manifest(verify_ok=False)
    with pytest.raises(ChecksumError):
        list(iter_pages(url, fetch=fetch, sections="5", verify_checksum=True))


def test_iter_pages_manifest_checksum_skipped_when_disabled():
    url, fetch = _wire_manifest(verify_ok=False)
    # With verification off, a bad sha256 is ignored and records still stream.
    pages = list(iter_pages(url, fetch=fetch, sections="5", verify_checksum=False))
    assert [p.source_url for p in pages] == ["m5a", "m5b"]
