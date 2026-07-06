# dify-datasource-ndjson

A Dify datasource plugin that loads a **bulk NDJSON (JSON Lines) dump** into a
Dify Knowledge base — one document per record — instead of crawling a live site.

It is **generic**: any HTTP-served NDJSON dump works — a bare `.ndjson` /
`.ndjson.gz`, or a JSON manifest listing per-file compressed exports — via
configurable field mapping. See [Sources](#sources) for examples.

## How it works

Give it a **Source URL**. The plugin auto-detects what it points at:

- **A JSON manifest** — a JSON object with an `exports` array. Each entry has a
  `url` (relative or absolute) and optionally `section`, `lang`, `sha256`. The
  plugin filters `exports` by your `sections` / `langs`, downloads each selected
  export (gzip), optionally verifies its `sha256`, decompresses it, and streams
  its records.
- **A bare `.ndjson` or a compressed `.ndjson.{gz,xz,bz2,zst}`** — streamed
  directly. Compression is auto-detected (gzip / xz / bz2 / zstd) by magic bytes,
  falling back to the URL suffix.

Each NDJSON line is parsed as one JSON object and mapped to a document via the
`*_field` parameters. Blank and malformed lines are skipped, never fatal. The
stream is decompressed **lazily** line-by-line, so a ~45 MB compressed export is
never fully materialised as decompressed text in memory.

### Two-phase model (`online_document`)

This is an `online_document`-type datasource, which fits large bulk dumps far
better than `website_crawl` (which would stream every record's full content into
the browser preview and time out on big sections). It works in two phases:

- **List pages** (`_get_pages`) — download and parse the source once, render each
  record, and return a **light listing** (page id + title, **no content**). Even
  20k records is a small message. The rendered content is written to a
  **disk cache** (SQLite, under the system temp dir) keyed by a hash of the
  datasource parameters.
- **Get content** (`_get_content`) — Dify pulls content **one page at a time**
  during indexing; each call reads a single record from the disk cache.

The cache is on **disk** (not memory) because the plugin process may be recycled
between the two phases. The `workspace_id` returned to Dify encodes the datasource
parameters, so on a cold cache (evicted / temp wiped) `_get_content` rebuilds the
cache **once** from the source, then serves every page from it — the dump is not
re-downloaded per page.

### Metadata limitation

The ingested document exposes only a title and content — there is **no free-form
metadata field**. Any fields you list in `metadata_fields` are therefore inlined
as a compact one-line header at the top of the document content, e.g.:

```
> category: tutorial | product: acme-cli | lang: en | version: 2.3

<record body...>
```

This keeps the key metadata searchable/visible inside each document. A future
Dify SDK with a real metadata field would let this move out of the body.

## Parameters

- `source_url` (required) — URL of a manifest or an `.ndjson` / `.ndjson.gz`.
- `sections` — manifest only: CSV of section values to keep (e.g. `1,5,8`).
- `langs` — manifest only: CSV of language values to keep (e.g. `ru,en`).
- `url_field` (default `url`) — record field → document source URL.
- `title_field` (default `title`) — record field → document title.
- `content_field` (default `text`) — record field → document body.
- `metadata_fields` — CSV of fields inlined as a header in content.
- `filter_field` + `filter_value` — ingest only records where the field equals
  the value (e.g. `canonical` = `true`, for deduping to canonical records).
  Booleans match `true` / `false` case-insensitively. Empty = no filter.
- `max_records` — stop after N records. Empty = no cap.
- `verify_checksum` (default `true`) — manifest only: verify each export's
  `sha256` before ingesting; a mismatch aborts with an error.

## Credentials (all optional)

- `user_agent` — custom User-Agent header.
- `token` — a Bearer token for a private dump (sent as `Authorization: Bearer …`).
- `auth_header` — a full `Authorization` header value (overrides `token`).

The dump URL is a datasource parameter, not a credential, so credential
validation performs no network calls.

## Sources

Anything that serves newline-delimited JSON over HTTP works. A few public examples:

- **GH Archive** — hourly gzipped NDJSON of every public GitHub event
  (`https://data.gharchive.org/2024-01-01-0.json.gz`); a bare `.json.gz`, no manifest.
- **OpenAlex** — the scholarly-works snapshot ships as gzipped JSON Lines partitions
  listed by a manifest — the manifest mode below.
- **Hugging Face datasets** — many are published as `.jsonl` / `.jsonl.gz` files at
  stable `resolve/main/...` URLs.
- **Your own exports** — `mongoexport`, Elasticsearch/OpenSearch `_bulk` /
  `elasticdump`, BigQuery *newline-delimited JSON*, ClickHouse `JSONEachRow`, or
  anything piped through `jq -c` all emit NDJSON.

Point `source_url` at the file (or a manifest) and map its fields with the
`*_field` parameters.

## Example: manpages.altlinux.team

```
source_url:       https://manpages.altlinux.team/exports/index.json
sections:         1,5,8
langs:            ru,en
content_field:    text
metadata_fields:  section,package,lang,branch,version
filter_field:     canonical
filter_value:     true
verify_checksum:  true
```

## Development

```sh
python3 -m pytest -q
ruff check .
yamllint .
```

The ingestion core (manifest parsing, gzip line-streaming, record→document
mapping, page-id assignment, and the disk cache) lives in the `ndjson_source`
package, which is **independent of the Dify SDK** so it can be unit-tested without
`dify_plugin` installed. `datasources/ndjson.py` is the thin SDK adapter.
