from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Page:
    """One ingested NDJSON record, mapped to the fields Dify consumes.

    ``content`` may carry a compact metadata header (see ``records.record_to_page``)
    because the target document has no dedicated free-form metadata field.
    """

    source_url: str
    title: str
    description: str
    content: str


@dataclass
class DocumentPage:
    """One online-document page: a stable ``page_id``, its ``title`` and the full
    ``content`` (with an optional metadata header).

    ``_get_pages`` returns a light listing (id + title + type, no content), while
    the content is stashed in the disk cache keyed by ``page_id`` and later pulled
    one page at a time by ``_get_content``.
    """

    page_id: str
    title: str
    content: str
    type: str
