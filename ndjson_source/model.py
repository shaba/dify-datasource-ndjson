from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Page:
    """One ingested NDJSON record, mapped to the fields Dify consumes.

    ``content`` may carry a compact metadata header (see ``records.record_to_page``)
    because ``WebSiteInfoDetail`` has no dedicated metadata field.
    """

    source_url: str
    title: str
    description: str
    content: str


@dataclass
class CrawlProgress:
    """A snapshot emitted while streaming an ingest. ``web_info`` is the
    cumulative list of pages collected so far (bounded by max_records + the time
    budget)."""

    web_info: list[Page]
    completed: int
    total: int
    status: str  # "processing" | "completed"
    capped: bool  # stopped before the source was exhausted
    reason: str  # "" | "max_records" | "time_budget"
