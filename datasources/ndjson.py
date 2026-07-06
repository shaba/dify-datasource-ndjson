import logging
from collections.abc import Generator, Mapping
from typing import Any

from dify_plugin.entities.datasource import (
    DatasourceGetPagesResponse,
    DatasourceMessage,
    GetOnlineDocumentPageContentRequest,
    OnlineDocumentInfo,
)
from dify_plugin.interfaces.datasource.online_document import OnlineDocumentDatasource

from ndjson_source import (
    DEFAULT_UA,
    PageCache,
    decode_recipe,
    default_fetch,
    encode_recipe,
    normalize_recipe,
    pages_from_recipe,
    populate,
    recipe_key,
)

logger = logging.getLogger(__name__)

# One workspace for this datasource; the id carries the encoded recipe so
# ``_get_content`` can rebuild the cache without the datasource parameters.
_WORKSPACE_NAME = "NDJSON dump"


class NdjsonDatasource(OnlineDocumentDatasource):
    """NDJSON-dump online_document datasource (one page per record).

    ``_get_pages`` returns a light listing (id + title, no content) and stashes the
    rendered content in a disk cache keyed by a hash of the datasource parameters.
    ``_get_content`` reads one page from that cache, rebuilding it once on a miss.
    """

    def _fetch(self):
        """Build a credential-aware ``fetch`` (custom UA + optional auth header)."""
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

        return fetch

    def _get_pages(
        self, datasource_parameters: Mapping[str, Any]
    ) -> DatasourceGetPagesResponse:
        recipe = normalize_recipe(datasource_parameters or {})
        workspace_id = encode_recipe(recipe)
        cache = PageCache(recipe_key(recipe))
        fetch = self._fetch()

        # Single pass: render each record into the disk cache while collecting a
        # light listing (id + title + type, NO content) for the response. The
        # listing is one bounded message; content is pulled later, page by page.
        pages: list[dict[str, Any]] = []
        with cache.writer() as writer:
            for page in pages_from_recipe(recipe, fetch=fetch):
                writer.add(page.page_id, page.title, page.content)
                pages.append({
                    "page_id": page.page_id,
                    "page_name": page.title or page.page_id,
                    "type": page.type,
                    "last_edited_time": "",
                })

        logger.info("ndjson: listed %d pages from %s", len(pages), recipe["source_url"])
        info = OnlineDocumentInfo(
            workspace_id=workspace_id,
            workspace_name=_WORKSPACE_NAME,
            workspace_icon="",
            pages=pages,
            total=len(pages),
        )
        return DatasourceGetPagesResponse(result=[info])

    def _get_content(
        self, page: GetOnlineDocumentPageContentRequest
    ) -> Generator[DatasourceMessage, None, None]:
        recipe = decode_recipe(page.workspace_id)
        cache = PageCache(recipe_key(recipe))

        row = cache.get(page.page_id)
        if row is None:
            # Cache miss (evicted / plugin restarted with a wiped temp dir): rebuild
            # it once from the recipe, then read. Subsequent pages hit the ready
            # cache -- the source is not re-downloaded per page.
            logger.info("ndjson: cache miss for %s, rebuilding", page.page_id)
            populate(cache, recipe, fetch=self._fetch())
            row = cache.get(page.page_id)
            if row is None:
                raise ValueError(f"page not found: {page.page_id}")

        title, content = row
        yield self.create_variable_message("page_id", page.page_id)
        yield self.create_variable_message("workspace_id", page.workspace_id)
        yield self.create_variable_message("title", title)
        yield self.create_variable_message("content", content)
