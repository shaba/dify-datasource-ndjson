from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator

from .model import CrawlProgress, Page

# Wall-clock budget for a single ingest request. The plugin runs inside one Dify
# request capped at MAX_REQUEST_TIMEOUT (300 s, see main.py); we stop reading
# well before that so a partial-but-completed result is always returned instead
# of the whole request being killed mid-stream. Overridable per call for tests.
NDJSON_TIME_BUDGET = 240.0


def stream_pages(
    page_iter: Iterable[Page],
    *,
    source_total: int | None,
    max_records: int,
    now: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
    progress_every_pages: int = 200,
    progress_every_seconds: float = 5.0,
) -> Iterator[CrawlProgress]:
    """Lazily consume a ``Page`` iterator, emitting periodic ``processing``
    snapshots and a final ``completed`` snapshot.

    Stops early when the wall-clock ``deadline`` (measured via ``now``) passes.
    Because ``page_iter`` is lazy, breaking out of the loop stops the underlying
    download loop -- no further exports are fetched.

    ``source_total`` is the size of the source when known; pass ``None`` when the
    record count is not known ahead of time (the usual NDJSON case, where total
    is only known once the stream is exhausted).

    Time and clock are injectable (``now``/``deadline``) so tests stay
    deterministic without sleeping.
    """
    pages: list[Page] = []
    last_n = 0
    last_t = now()
    truncated = False

    src = iter(page_iter)
    try:
        for page in src:
            pages.append(page)
            n = len(pages)
            t = now()
            if (n - last_n) >= progress_every_pages or (t - last_t) >= progress_every_seconds:
                last_n, last_t = n, t
                yield CrawlProgress(
                    web_info=pages,
                    completed=n,
                    total=max(source_total or 0, n),
                    status="processing",
                    capped=False,
                    reason="",
                )
            if deadline is not None and now() >= deadline:
                truncated = True
                break
    finally:
        close = getattr(src, "close", None)
        if callable(close):
            close()

    n = len(pages)
    if truncated:
        capped, reason = True, "time_budget"
    elif n >= max_records and source_total is not None and source_total > max_records:
        capped, reason = True, "max_records"
    else:
        capped, reason = False, ""

    total = source_total if (capped and source_total is not None) else n
    yield CrawlProgress(
        web_info=pages,
        completed=n,
        total=total,
        status="completed",
        capped=capped,
        reason=reason,
    )
