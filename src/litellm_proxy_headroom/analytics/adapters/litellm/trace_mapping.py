from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ...application.commands import TraceContextCommand

_TRACEPARENT_RE = re.compile(
    r"^[\da-fA-F]{2}-([\da-fA-F]{32})-([\da-fA-F]{16})-[\da-fA-F]{2}$"
)


def trace_context_from_litellm_payload(
    payload: Mapping[str, Any],
) -> TraceContextCommand:
    maps = _candidate_maps(payload)
    traceparent = _lookup(maps, "traceparent", "parent_traceparent")
    trace_id = _lookup(maps, "trace_id", "otel_trace_id", "litellm_trace_id")
    span_id = _lookup(maps, "span_id", "otel_span_id", "litellm_span_id")
    tracestate = _lookup(maps, "tracestate")

    if traceparent:
        match = _TRACEPARENT_RE.match(traceparent)
        if match is not None:
            trace_id = trace_id or match.group(1).lower()
            span_id = span_id or match.group(2).lower()

    return TraceContextCommand(
        trace_id=trace_id,
        span_id=span_id,
        traceparent=traceparent,
        tracestate=tracestate,
    )


def _candidate_maps(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    maps: list[Mapping[str, Any]] = [payload]
    for key in (
        "metadata",
        "litellm_metadata",
        "headers",
        "request_headers",
        "extra_headers",
    ):
        value = payload.get(key)
        if isinstance(value, Mapping):
            maps.append(value)

    litellm_params = payload.get("litellm_params")
    if isinstance(litellm_params, Mapping):
        maps.append(litellm_params)
        metadata = litellm_params.get("metadata")
        if isinstance(metadata, Mapping):
            maps.append(metadata)
    return maps


def _lookup(maps: list[Mapping[str, Any]], *keys: str) -> str | None:
    wanted = {key.lower() for key in keys}
    for mapping in maps:
        for key, value in mapping.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted and value:
                return str(value)
    return None
