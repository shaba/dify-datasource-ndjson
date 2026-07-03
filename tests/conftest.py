from __future__ import annotations

import gzip
import hashlib
import json


def ndjson_bytes(records: list[dict], *, raw_lines: list[str] | None = None) -> bytes:
    """Serialise records (plus optional raw extra lines) to NDJSON bytes."""
    lines = [json.dumps(r) for r in records]
    if raw_lines:
        lines.extend(raw_lines)
    return ("\n".join(lines) + "\n").encode("utf-8")


def gz(data: bytes) -> bytes:
    """gzip-compress bytes in memory (deterministic mtime=0)."""
    return gzip.compress(data, mtime=0)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeFetch:
    """URL -> bytes map. Records call order; never touches the network.

    Signature matches ``ndjson_source.http.Fetch``: (url, timeout) ->
    (status, content_type, bytes).
    """

    def __init__(self, routes: dict[str, bytes], content_type: str = "application/octet-stream"):
        self.routes = routes
        self.content_type = content_type
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: int = 60):
        self.calls.append(url)
        try:
            body = self.routes[url]
        except KeyError as exc:  # pragma: no cover - test wiring error
            raise AssertionError(f"unexpected fetch: {url}") from exc
        return 200, self.content_type, body


class Clock:
    """Manually-advanced monotonic clock for deterministic budget tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def advance(self, dt: float) -> None:
        self.t += dt

    def __call__(self) -> float:
        return self.t
