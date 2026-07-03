"""Core NDJSON-dump ingestion logic, independent of the Dify SDK.

Everything here is plain Python (stdlib + requests) so it can be unit-tested
without ``dify_plugin`` installed, mirroring the ``docs_crawler`` package in the
sibling litecrawl plugin.
"""

from .http import DEFAULT_UA, Fetch, default_fetch
from .manifest import ChecksumError, is_manifest, load_manifest, select_exports
from .model import CrawlProgress, Page
from .records import iter_pages, iter_records, record_to_page
from .stream import NDJSON_TIME_BUDGET, stream_pages

__all__ = [
    "DEFAULT_UA",
    "Fetch",
    "default_fetch",
    "ChecksumError",
    "is_manifest",
    "load_manifest",
    "select_exports",
    "CrawlProgress",
    "Page",
    "iter_pages",
    "iter_records",
    "record_to_page",
    "NDJSON_TIME_BUDGET",
    "stream_pages",
]
