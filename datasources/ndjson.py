import logging
import time
from collections.abc import Generator, Mapping
from typing import Any

from dify_plugin.entities.datasource import (
    WebSiteInfo,
    WebSiteInfoDetail,
    WebsiteCrawlMessage,
)
from dify_plugin.interfaces.datasource.website import WebsiteCrawlDatasource

from ndjson_source import (
    DEFAULT_UA,
    CrawlProgress,
    default_fetch,
    iter_pages,
    stream_pages,
)
from ndjson_source.stream import NDJSON_TIME_BUDGET

logger = logging.getLogger(__name__)


class NdjsonDatasource(WebsiteCrawlDatasource):
    def _get_website_crawl(
        self, datasource_parameters: Mapping[str, Any]
    ) -> Generator[WebsiteCrawlMessage, None, None]:
        source_url = str(datasource_parameters.get("source_url") or "").strip()
        if not source_url:
            raise ValueError("source_url is required")

        def _s(name: str, default: str = "") -> str:
            return str(datasource_parameters.get(name) or default).strip()

        def _csv(name: str) -> list[str]:
            return [p.strip() for p in _s(name).split(",") if p.strip()]

        sections = _s("sections") or None
        langs = _s("langs") or None
        url_field = _s("url_field", "url")
        title_field = _s("title_field", "title")
        content_field = _s("content_field", "text")
        metadata_fields = _csv("metadata_fields")
        filter_field = _s("filter_field") or None
        filter_value = _s("filter_value") or None
        verify_checksum = bool(datasource_parameters.get("verify_checksum", True))
        raw_max = datasource_parameters.get("max_records")
        max_records: int | None = int(raw_max) if raw_max else None

        credentials = self.runtime.credentials or {}
        user_agent = str(credentials.get("user_agent") or DEFAULT_UA)
        headers: dict[str, str] = {}
        token = str(credentials.get("token") or "").strip()
        auth_header = str(credentials.get("auth_header") or "").strip()
        if auth_header:
            headers["Authorization"] = auth_header
        elif token:
            headers["Authorization"] = f"Bearer {token}"

        def fetch(target: str, timeout: int = 60) -> tuple[int, str, bytes]:
            return default_fetch(
                target, timeout, user_agent=user_agent, headers=headers or None
            )

        # Time budget: stop reading before the 300 s request cap so a
        # partial-but-completed result is always returned (see NDJSON_TIME_BUDGET).
        now = time.monotonic
        deadline = now() + NDJSON_TIME_BUDGET

        crawl_res = WebSiteInfo(web_info_list=[], status="processing", total=0, completed=0)
        yield self.create_crawl_message(crawl_res)

        page_iter = iter_pages(
            source_url,
            fetch=fetch,
            sections=sections,
            langs=langs,
            url_field=url_field,
            title_field=title_field,
            content_field=content_field,
            metadata_fields=metadata_fields,
            filter_field=filter_field,
            filter_value=filter_value,
            verify_checksum=verify_checksum,
            max_records=max_records,
        )

        # The record count is unknown until the stream is exhausted, so
        # source_total is None (total is reported as N). max_records bounds the
        # generator itself; the value passed here only tunes capped-reporting.
        cap = max_records if max_records else 10**9
        last: CrawlProgress | None = None
        for progress in stream_pages(
            page_iter, source_total=None, max_records=cap, now=now, deadline=deadline
        ):
            # Dify reads web_info_list ONLY from the final "completed" message
            # (processing events carry just total/completed). Emitting the
            # cumulative list on every processing snapshot is O(n^2) in payload
            # and times out the daemon on large dumps -- so send it once, at the end.
            if progress.status == "completed":
                crawl_res.web_info_list = [
                    WebSiteInfoDetail(
                        source_url=page.source_url,
                        content=page.content,
                        title=page.title,
                        description=page.description,
                    )
                    for page in progress.web_info
                ]
            else:
                crawl_res.web_info_list = []
            crawl_res.status = progress.status
            crawl_res.total = progress.total
            crawl_res.completed = progress.completed
            yield self.create_crawl_message(crawl_res)
            last = progress

        if last is not None and last.capped:
            logger.info(
                "ndjson: capped ingest of %s -- took %d records (limited by %s)",
                source_url, last.completed, last.reason,
            )
