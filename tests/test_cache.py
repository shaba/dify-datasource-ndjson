from __future__ import annotations

import json

import pytest

from ndjson_source.cache import (
    PageCache,
    decode_recipe,
    encode_recipe,
    normalize_recipe,
    populate,
    recipe_key,
)
from ndjson_source.records import iter_document_pages, pages_from_recipe

from tests.conftest import FakeFetch, gz, ndjson_bytes, sha256_hex


def _manifest(exports: list[dict]) -> bytes:
    return json.dumps({"exports": exports}).encode("utf-8")


# --- recipe: normalise / key / encode / decode -----------------------------


def test_normalize_recipe_defaults_and_required():
    recipe = normalize_recipe({"source_url": "  https://h/d.ndjson  "})
    assert recipe["source_url"] == "https://h/d.ndjson"
    assert recipe["url_field"] is None  # empty -> None (defaults applied downstream)
    assert recipe["metadata_fields"] == []
    assert recipe["max_records"] is None
    assert recipe["verify_checksum"] is True


def test_normalize_recipe_requires_source_url():
    with pytest.raises(ValueError):
        normalize_recipe({"source_url": "   "})


def test_normalize_recipe_metadata_and_max_records():
    recipe = normalize_recipe(
        {"source_url": "u", "metadata_fields": "section, package ,", "max_records": "5"}
    )
    assert recipe["metadata_fields"] == ["section", "package"]
    assert recipe["max_records"] == 5


def test_recipe_encode_decode_round_trip():
    recipe = normalize_recipe({"source_url": "u", "sections": "1,8", "langs": "ru"})
    token = encode_recipe(recipe)
    assert decode_recipe(token) == recipe
    # key derived from the decoded token matches the original recipe's key
    assert recipe_key(decode_recipe(token)) == recipe_key(recipe)


def test_recipe_key_differs_by_params():
    a = normalize_recipe({"source_url": "u", "sections": "1"})
    b = normalize_recipe({"source_url": "u", "sections": "8"})
    assert recipe_key(a) != recipe_key(b)


def test_decode_recipe_rejects_garbage():
    with pytest.raises(ValueError):
        decode_recipe("not-a-valid-token!!")


# --- page-id assignment ----------------------------------------------------


def test_iter_document_pages_uses_url_as_id():
    raw = ndjson_bytes([{"url": "a", "title": "A", "text": "1"},
                        {"url": "b", "title": "B", "text": "2"}])
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    docs = list(iter_document_pages("https://h/d.ndjson", fetch=fetch))
    assert [d.page_id for d in docs] == ["a", "b"]
    assert [d.title for d in docs] == ["A", "B"]


def test_iter_document_pages_dedups_and_falls_back():
    raw = ndjson_bytes([{"url": "a", "text": "1"},
                        {"url": "a", "text": "2"},   # duplicate url
                        {"url": "", "text": "3"}])   # empty url
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    ids = [d.page_id for d in iter_document_pages("https://h/d.ndjson", fetch=fetch)]
    assert ids == ["a", "rec:1", "rec:2"]
    assert len(set(ids)) == 3  # all unique -> safe as a sqlite primary key


# --- PageCache -------------------------------------------------------------


def test_page_cache_write_then_read(tmp_path):
    cache = PageCache("k1", root=tmp_path)
    assert cache.get("x") is None  # nothing written yet
    with cache.writer() as w:
        w.add("x", "Title X", "Body X")
        w.add("y", "Title Y", "Body Y")
    assert cache.exists()
    assert cache.get("x") == ("Title X", "Body X")
    assert cache.get("y") == ("Title Y", "Body Y")
    assert cache.get("missing") is None


def test_page_cache_writer_replaces_previous_contents(tmp_path):
    cache = PageCache("k1", root=tmp_path)
    with cache.writer() as w:
        w.add("old", "T", "C")
    with cache.writer() as w:
        w.add("new", "T2", "C2")
    assert cache.get("old") is None
    assert cache.get("new") == ("T2", "C2")


# --- populate: build cache from a recipe -----------------------------------


def test_populate_builds_cache_with_metadata_header(tmp_path):
    raw = ndjson_bytes([{"url": "a", "title": "A", "text": "body", "section": 8}])
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    recipe = normalize_recipe(
        {"source_url": "https://h/d.ndjson", "metadata_fields": "section"}
    )
    cache = PageCache(recipe_key(recipe), root=tmp_path)
    count = populate(cache, recipe, fetch=fetch)
    assert count == 1
    title, content = cache.get("a")
    assert title == "A"
    head, _, body = content.partition("\n\n")
    assert head == "> section: 8"
    assert body == "body"


def test_get_content_cache_miss_rebuilds_once(tmp_path):
    # Model the adapter's cache-miss path without importing the SDK: a fresh cache
    # is empty, populate() rebuilds it from the recipe, then the page reads.
    raw = ndjson_bytes([{"url": "a", "text": "1"}, {"url": "b", "text": "2"}])
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    recipe = normalize_recipe({"source_url": "https://h/d.ndjson"})
    cache = PageCache(recipe_key(recipe), root=tmp_path)

    assert cache.get("b") is None          # cold: nothing to read
    populate(cache, recipe, fetch=fetch)   # rebuild once
    assert fetch.calls == ["https://h/d.ndjson"]
    assert cache.get("b") == ("", "2")     # now resolvable from the rebuilt cache


# --- compression / filtering preserved through the recipe path -------------


def test_pages_from_recipe_direct_gzip_autodetect(tmp_path):
    raw = ndjson_bytes([{"url": "a", "text": "1"}])
    fetch = FakeFetch({"https://h/d.ndjson.gz": gz(raw)})
    recipe = normalize_recipe({"source_url": "https://h/d.ndjson.gz"})
    docs = list(pages_from_recipe(recipe, fetch=fetch))
    assert [(d.page_id, d.content) for d in docs] == [("a", "1")]


def test_pages_from_recipe_manifest_sections_langs_canonical():
    exp5 = ndjson_bytes([{"url": "m5a", "text": "5a", "canonical": True},
                         {"url": "m5b", "text": "5b", "canonical": False}])
    exp1 = ndjson_bytes([{"url": "m1a", "text": "1a", "canonical": True}])
    g5, g1 = gz(exp5), gz(exp1)
    base = "https://h/exports/"
    manifest = _manifest([
        {"url": "man5.ru.ndjson.gz", "section": "5", "lang": "ru", "sha256": sha256_hex(g5)},
        {"url": "man1.en.ndjson.gz", "section": "1", "lang": "en", "sha256": sha256_hex(g1)},
    ])
    fetch = FakeFetch({
        base + "index.json": manifest,
        base + "man5.ru.ndjson.gz": g5,
        base + "man1.en.ndjson.gz": g1,
    })
    recipe = normalize_recipe({
        "source_url": base + "index.json",
        "sections": "5",
        "langs": "ru",
        "filter_field": "canonical",
        "filter_value": "true",
    })
    docs = list(pages_from_recipe(recipe, fetch=fetch))
    assert [d.page_id for d in docs] == ["m5a"]
    # section 1 export was filtered out at the manifest stage -> never fetched
    assert not any("man1" in c for c in fetch.calls)
