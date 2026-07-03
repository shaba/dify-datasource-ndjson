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

Ingestion streams under a **wall-clock time budget** (~240 s of the 300 s Dify
request cap): periodic `processing` snapshots are emitted, then a final
`completed` snapshot. If the budget or `max_records` is hit first, a
partial-but-`completed` result is returned instead of the request being killed
mid-stream.

### Semantics note

This is a `website_crawl`-type datasource, but it does **not** crawl — the
`WebsiteCrawlMessage` contract (incremental `WebSiteInfo` with a growing
`web_info_list`) simply fits bulk ingestion perfectly. Each NDJSON record becomes
one `WebSiteInfoDetail`.

### Metadata limitation

`WebSiteInfoDetail` exposes only `source_url`, `title`, `content`, and
`description` — there is **no free-form metadata field**. Any fields you list in
`metadata_fields` are therefore inlined as a compact one-line header at the top
of the document content, e.g.:

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
mapping, and the streaming time-budget) lives in the `ndjson_source` package,
which is **independent of the Dify SDK** so it can be unit-tested without
`dify_plugin` installed. `datasources/ndjson.py` is the thin SDK adapter.
