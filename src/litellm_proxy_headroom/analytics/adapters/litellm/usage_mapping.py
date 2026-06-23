from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from ...application.commands import TokenUsageBreakdownCommand


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _nested(obj: Any, key: str) -> Any:
    value = _get(obj, key)
    if value is None:
        return {}
    return value


def response_id(response: Any) -> str | None:
    value = _get(response, "id")
    return str(value) if value else None


def response_cost(response: Any) -> Decimal | None:
    for container in (
        _get(response, "_hidden_params"),
        _get(response, "metadata"),
        response,
    ):
        if not isinstance(container, Mapping) and container is not response:
            continue
        value = _get(container, "response_cost")
        if value is None:
            value = _get(container, "cost")
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
    return None


def token_usage_from_response(response: Any) -> TokenUsageBreakdownCommand | None:
    usage = _get(response, "usage")
    if usage is None:
        return None

    prompt_details = _nested(usage, "prompt_tokens_details")
    completion_details = _nested(usage, "completion_tokens_details")
    metadata = provider_response_metadata(response)
    additional_usage = _nested(metadata, "additional_usage_values")

    cached_tokens = (
        _get(prompt_details, "cached_tokens")
        or _get(usage, "cache_read_input_tokens")
        or _get(usage, "cached_input_tokens")
        or _get(additional_usage, "cache_read_input_tokens")
        or _get(additional_usage, "cached_input_tokens")
    )
    cache_write_tokens = (
        _get(prompt_details, "cache_creation_tokens")
        or _get(prompt_details, "cache_write_tokens")
        or _get(usage, "cache_creation_input_tokens")
        or _get(usage, "cache_write_input_tokens")
        or _get(additional_usage, "cache_creation_input_tokens")
        or _get(additional_usage, "cache_write_input_tokens")
    )
    reasoning_tokens = (
        _get(completion_details, "reasoning_tokens")
        or _get(usage, "reasoning_tokens")
        or _get(usage, "thinking_tokens")
        or _get(additional_usage, "reasoning_tokens")
        or _get(additional_usage, "thinking_tokens")
    )
    input_tokens = (
        _get(usage, "prompt_tokens")
        or _get(usage, "input_tokens")
        or _get(additional_usage, "input_tokens")
        or _get(additional_usage, "prompt_tokens")
    )
    output_tokens = (
        _get(usage, "completion_tokens")
        or _get(usage, "output_tokens")
        or _get(additional_usage, "output_tokens")
        or _get(additional_usage, "completion_tokens")
    )
    total_tokens = (
        _get(usage, "total_tokens")
        or _get(additional_usage, "total_tokens")
        or _sum_tokens(input_tokens, output_tokens)
    )

    newly_processed_input_tokens = None
    if input_tokens is not None and cached_tokens is not None:
        newly_processed_input_tokens = max(int(input_tokens) - int(cached_tokens), 0)

    raw_usage = usage if isinstance(usage, dict) else _usage_object_to_dict(usage)
    if additional_usage:
        raw_usage = dict(raw_usage)
        raw_usage["additional_usage_values"] = dict(additional_usage)
    return TokenUsageBreakdownCommand(
        measurement_source="provider_reported",
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        newly_processed_input_tokens=newly_processed_input_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        raw_usage=raw_usage,
    )


def provider_response_metadata(response: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "id",
        "model",
        "object",
        "created",
        "system_fingerprint",
        "service_tier",
    ):
        value = _get(response, key)
        if value is not None:
            metadata[key] = value

    hidden_params = _get(response, "_hidden_params")
    if isinstance(hidden_params, Mapping):
        metadata["_hidden_params"] = _redacted_mapping(hidden_params)
        additional_usage = hidden_params.get("additional_usage_values")
        if isinstance(additional_usage, Mapping):
            metadata["additional_usage_values"] = _redacted_mapping(additional_usage)

    response_metadata = _get(response, "metadata")
    if isinstance(response_metadata, Mapping):
        metadata["metadata"] = _redacted_mapping(response_metadata)
        additional_usage = response_metadata.get("additional_usage_values")
        if isinstance(additional_usage, Mapping):
            metadata["additional_usage_values"] = _redacted_mapping(additional_usage)

    usage = _get(response, "usage")
    if usage is not None:
        metadata["usage"] = (
            usage if isinstance(usage, dict) else _usage_object_to_dict(usage)
        )

    return metadata


def _usage_object_to_dict(usage: Any) -> dict[str, Any]:
    if hasattr(usage, "model_dump"):
        return usage.model_dump(mode="json")
    keys = [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "thinking_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_read_input_tokens",
        "cached_input_tokens",
    ]
    return {key: _get(usage, key) for key in keys if _get(usage, key) is not None}


def _redacted_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        normalized = str(key).lower()
        if any(secret in normalized for secret in ("api_key", "token", "secret")):
            redacted[str(key)] = "[REDACTED]"
        elif isinstance(value, Mapping):
            redacted[str(key)] = _redacted_mapping(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            redacted[str(key)] = value
        elif isinstance(value, list):
            redacted[str(key)] = [
                item
                for item in value
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
        else:
            redacted[str(key)] = str(value)
    return redacted


def _sum_tokens(input_tokens: Any, output_tokens: Any) -> int | None:
    if input_tokens is None or output_tokens is None:
        return None
    return int(input_tokens) + int(output_tokens)
