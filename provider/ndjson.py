from collections.abc import Mapping
from typing import Any

from dify_plugin.interfaces.datasource import DatasourceProvider


class NdjsonDatasourceProvider(DatasourceProvider):
    def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
        # The dump URL is supplied as a datasource parameter, not a credential,
        # so there is nothing to reach out to here. Credentials are all optional
        # (user_agent / token / auth_header for private dumps): no-op validation.
        return None
