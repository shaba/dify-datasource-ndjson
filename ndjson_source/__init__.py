"""Core NDJSON-dump ingestion logic, independent of the Dify SDK.

Everything here is plain Python (stdlib + requests) so it can be unit-tested
without ``dify_plugin`` installed, mirroring the ``forgejo_client`` package in the
sibling datasource plugin.
"""

from .cache import (
    PageCache,
    decode_recipe,
    encode_recipe,
    normalize_recipe,
    populate,
    recipe_key,
)
from .http import DEFAULT_UA, Fetch, default_fetch
from .manifest import ChecksumError, is_manifest, load_manifest, select_exports
from .model import DocumentPage, Page
from .records import (
    iter_document_pages,
    iter_pages,
    iter_records,
    pages_from_recipe,
    record_to_page,
)

__all__ = [
    "DEFAULT_UA",
    "Fetch",
    "default_fetch",
    "ChecksumError",
    "is_manifest",
    "load_manifest",
    "select_exports",
    "Page",
    "DocumentPage",
    "iter_pages",
    "iter_records",
    "record_to_page",
    "iter_document_pages",
    "pages_from_recipe",
    "PageCache",
    "normalize_recipe",
    "encode_recipe",
    "decode_recipe",
    "recipe_key",
    "populate",
]
