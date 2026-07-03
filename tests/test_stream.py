from __future__ import annotations

from ndjson_source.model import Page
from ndjson_source.records import iter_pages
from ndjson_source.stream import stream_pages

from tests.conftest import Clock, FakeFetch, ndjson_bytes


def _pages(clock: Clock, n: int, per_page: float = 0.0):
    """Lazy Page generator that advances the clock as each page is consumed."""
    for i in range(n):
        clock.advance(per_page)
        yield Page(source_url=str(i), title="", description="", content="x")


def test_stream_emits_processing_then_completed():
    clock = Clock()
    progs = list(
        stream_pages(
            _pages(clock, 500), source_total=None, max_records=10**9,
            now=clock, progress_every_pages=200,
        )
    )
    assert progs[-1].status == "completed"
    assert progs[-1].completed == 500 and progs[-1].total == 500
    assert not progs[-1].capped
    assert any(p.status == "processing" for p in progs)
    # completed counts are monotonically non-decreasing
    counts = [p.completed for p in progs]
    assert counts == sorted(counts)


def test_stream_small_source_no_processing_snapshot():
    clock = Clock()
    progs = list(
        stream_pages(_pages(clock, 3), source_total=None, max_records=10**9, now=clock)
    )
    assert len(progs) == 1 and progs[0].status == "completed"
    assert progs[0].completed == 3


def test_stream_time_budget_partial_result():
    clock = Clock()
    # 1 s per page, deadline at 10 s -> ~10 pages before we stop.
    progs = list(
        stream_pages(
            _pages(clock, 5000, per_page=1.0), source_total=None,
            max_records=5000, now=clock, deadline=10.0,
        )
    )
    final = progs[-1]
    assert final.status == "completed"
    assert final.capped and final.reason == "time_budget"
    assert final.completed == 10
    assert final.total == final.completed  # unknown source size -> total == N


def test_stream_time_budget_stops_reading_lazily():
    # The generator must not be drained past the budget: laziness means only the
    # consumed pages are produced.
    clock = Clock()
    produced = {"n": 0}

    def counting_pages():
        for i in range(5000):
            clock.advance(1.0)
            produced["n"] += 1
            yield Page(source_url=str(i), title="", description="", content="x")

    list(
        stream_pages(
            counting_pages(), source_total=None, max_records=5000,
            now=clock, deadline=5.0,
        )
    )
    assert produced["n"] == 5  # stopped at the budget, did not build all 5000


def test_stream_max_records_via_iter_pages():
    # End-to-end: iter_pages self-limits, stream reports the true source size.
    raw = ndjson_bytes([{"url": str(i), "text": "x"} for i in range(1000)])
    fetch = FakeFetch({"https://h/d.ndjson": raw})
    clock = Clock()
    page_iter = iter_pages("https://h/d.ndjson", fetch=fetch, max_records=50)
    progs = list(
        stream_pages(page_iter, source_total=None, max_records=50, now=clock)
    )
    final = progs[-1]
    assert final.status == "completed"
    assert final.completed == 50
