from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

from ...application.commands import CompressionActivityIngestCommand

logger = logging.getLogger(__name__)

_TRACE_ID_RE = re.compile(r"^[\da-fA-F]{32}$")
_SPAN_ID_RE = re.compile(r"^[\da-fA-F]{16}$")


@dataclass(frozen=True, slots=True)
class AnalyticsHttpClientConfig:
    base_url: str
    timeout_seconds: float = 0.75

    @classmethod
    def from_env(cls) -> AnalyticsHttpClientConfig | None:
        base_url = os.getenv("HEADROOM_ANALYTICS_URL", "").strip()
        if not base_url:
            return None
        timeout = float(os.getenv("HEADROOM_ANALYTICS_TIMEOUT_SECONDS", "0.75"))
        return cls(base_url=base_url.rstrip("/"), timeout_seconds=timeout)


class AnalyticsHttpClient:
    def __init__(self, config: AnalyticsHttpClientConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def post_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> bool:
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
            self._client = client

        try:
            response = await client.post(
                f"{self._config.base_url}/ingest/compression",
                json=command.model_dump(mode="json"),
                headers=_trace_headers(command),
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("analytics ingest failed: %s", exc)
            return False

    async def aclose(self) -> None:
        client = self._client
        if client is not None:
            await client.aclose()
            self._client = None


def _trace_headers(command: CompressionActivityIngestCommand) -> dict[str, str]:
    trace = command.event.trace
    headers: dict[str, str] = {}
    if trace.traceparent:
        headers["traceparent"] = trace.traceparent
    elif trace.trace_id and trace.span_id:
        trace_id = trace.trace_id.lower()
        span_id = trace.span_id.lower()
        if _TRACE_ID_RE.match(trace_id) and _SPAN_ID_RE.match(span_id):
            headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
    if trace.tracestate:
        headers["tracestate"] = trace.tracestate
    return headers
