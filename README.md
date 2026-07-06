# dify-datasource-ndjson

A Dify datasource plugin that loads a **bulk NDJSON dump** into a Dify Knowledge
base — one document per record — instead of crawling a live site.

It was built for the **manpages.altlinux.team** export layout (a JSON manifest
`index.json` listing per-section/lang gzip files `man{N}.{lang}.ndjson.gz`), but
is **generic**: any site that publishes an NDJSON dump — with or without a
manifest — works, via configurable field mapping.

## How it works

Give it a **Source URL**. The plugin auto-detects what it points at:

- **A JSON manifest** — a JSON object with an `exports` array. Each entry has a
  `url` (relative or absolute) and optionally `section`, `lang`, `sha256`. The
  plugin filters `exports` by your `sections` / `langs`, downloads each selected
  export (gzip), optionally verifies its `sha256`, decompresses it, and streams
  its records.
- **A bare `.ndjson` or `.ndjson.gz`** — streamed directly (gzip detected by
  magic bytes or the `.gz` suffix).

Each NDJSON line is parsed as one JSON object and mapped to a document via the
`*_field` parameters. Blank and malformed lines are skipped, never fatal. gzip
is decompressed **lazily** line-by-line, so a ~45 MB compressed export is never
fully materialised as decompressed text in memory.

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
> section: 5 | package: systemd | lang: ru | branch: sisyphus | version: 254

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
