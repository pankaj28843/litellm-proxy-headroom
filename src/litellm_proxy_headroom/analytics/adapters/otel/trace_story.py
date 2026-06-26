from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from opentelemetry import trace

from ...application.commands import (
    CompressionActivityIngestCommand,
    ProviderCallCommand,
    TokenUsageBreakdownCommand,
)

MAX_ATTR_TEXT = 512
MAX_ID_TEXT = 255
MAX_LIST_ITEMS = 8
MAX_METADATA_ATTRS = 64
CRITICAL_TRACE_ATTR_KEYS = (
    "litellm.proxy.analytics.request.key",
    "litellm.proxy.analytics.request.route",
    "litellm.proxy.session.run_marker",
    "litellm.proxy.session.project",
    "litellm.proxy.session.client",
    "litellm.proxy.compression.mode",
    "litellm.proxy.provider.session_affinity_hash",
    "litellm.proxy.provider.session_affinity_source",
    "session.id",
    "gen_ai.conversation.id",
)

_CONTENT_KEY_RE = re.compile(
    r"(?:body|content|input|message|output|prompt|query|raw)",
    re.IGNORECASE,
)
_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_SECRET_KEY_RE = re.compile(
    r"(?:"
    r"api[_-]?key"
    r"|authorization"
    r"|bearer"
    r"|cookie"
    r"|credential"
    r"|password"
    r"|secret"
    r"|(?:^|[_.-])(?:access|auth|id|refresh|session)[_.-]?token(?:$|[_.-])"
    r"|(?:^|[_.-])token(?:$|[_.-])"
    r")",
    re.IGNORECASE,
)

PrimitiveAttr = str | int | float | bool
AttributeValue = PrimitiveAttr | tuple[PrimitiveAttr, ...]


@dataclass(frozen=True, slots=True)
class TraceStoryEvent:
    name: str
    attributes: dict[str, AttributeValue]


@dataclass(frozen=True, slots=True)
class CompressionTraceStory:
    attributes: dict[str, AttributeValue]
    events: tuple[TraceStoryEvent, ...]


def compression_trace_story(
    command: CompressionActivityIngestCommand,
    *,
    success: bool | None = None,
    latency_ms: int | None = None,
) -> CompressionTraceStory:
    attrs: dict[str, AttributeValue] = {}
    _add(attrs, "litellm.proxy.analytics.operation", "compression_activity")
    _add(attrs, "litellm.proxy.analytics.success", success)
    _add(attrs, "litellm.proxy.analytics.persistence.latency_ms", latency_ms)

    _add(attrs, "litellm.proxy.analytics.event.source", command.event.source)
    _add(attrs, "litellm.proxy.analytics.event.type", command.event.event_type)
    _add_id(attrs, "litellm.proxy.analytics.event.key", command.event.event_key)
    _add_id(
        attrs, "litellm.proxy.analytics.event.payload_hash", command.event.payload_hash
    )

    _add_id(attrs, "litellm.proxy.analytics.request.key", command.request.request_key)
    _add(
        attrs,
        "litellm.proxy.analytics.request.source_system",
        command.request.source_system,
    )
    _add(attrs, "litellm.proxy.analytics.request.route", command.request.incoming_route)
    _add(
        attrs,
        "litellm.proxy.analytics.request.provider_hint",
        command.request.provider_hint,
    )
    _add(
        attrs, "litellm.proxy.analytics.request.model_hint", command.request.model_hint
    )
    _add_id(
        attrs,
        "litellm.proxy.analytics.request.external_request_id",
        command.request.external_request_id,
    )
    _add(attrs, "litellm.proxy.analytics.request.tenant_id", command.request.tenant_id)
    _add(attrs, "litellm.proxy.analytics.request.team_id", command.request.team_id)

    metadata = command.request.metadata
    _add(attrs, "litellm.proxy.analytics.integration", metadata.get("integration"))
    _add(
        attrs,
        "litellm.proxy.session.run_marker",
        metadata.get("litellm_proxy_run_marker"),
    )
    _add(attrs, "litellm.proxy.session.project", metadata.get("litellm_proxy_project"))
    _add(attrs, "litellm.proxy.session.client", metadata.get("litellm_proxy_client"))
    _add(
        attrs,
        "litellm.proxy.compression.mode",
        metadata.get("litellm_proxy_compression_mode"),
    )
    _add(
        attrs,
        "litellm.proxy.provider.session_affinity_hash",
        metadata.get("provider_session_affinity_hash"),
    )
    _add(
        attrs,
        "litellm.proxy.provider.session_affinity_source",
        metadata.get("provider_session_affinity_source"),
    )
    session_id = _session_id(command)
    _add(attrs, "session.id", session_id)
    _add(attrs, "gen_ai.conversation.id", session_id)
    _add_safe_metadata_attrs(
        attrs,
        "litellm.proxy.headroom.request.metadata",
        metadata,
    )

    _add(attrs, "litellm.proxy.compression.strategy", command.config.strategy_name)
    _add(
        attrs,
        "litellm.proxy.compression.strategy_version",
        command.config.strategy_version,
    )
    _add(attrs, "litellm.proxy.compression.target_model", command.config.target_model)
    _add(attrs, "litellm.proxy.compression.algorithm", command.config.algorithm)
    _add(
        attrs, "litellm.proxy.compression.trigger_reason", command.config.trigger_reason
    )

    execution = command.execution
    transforms = execution.transforms
    applied_transforms = _string_list(transforms.get("applied"))
    headroom_ccr_hashes = _string_list(transforms.get("ccr_hashes"))
    skip_reason = _text(transforms.get("skip_reason") or execution.error_type)

    _add(attrs, "litellm.proxy.compression.status", execution.status)
    _add(attrs, "litellm.proxy.compression.simulated", execution.is_simulated)
    _add(attrs, "litellm.proxy.compression.duration_ms", execution.duration_ms)
    _add(attrs, "litellm.proxy.compression.original_tokens", execution.original_tokens)
    _add(
        attrs,
        "litellm.proxy.compression.compressed_tokens",
        execution.compressed_tokens,
    )
    _add(attrs, "litellm.proxy.compression.tokens_saved", execution.tokens_saved)
    _add(
        attrs,
        "litellm.proxy.compression.ratio",
        _decimal_float(execution.compression_ratio),
    )
    _add(attrs, "litellm.proxy.compression.skip_reason", skip_reason)
    _add(attrs, "litellm.proxy.compression.transforms.count", len(applied_transforms))
    _add(attrs, "litellm.proxy.compression.transforms.applied", applied_transforms)
    _add(
        attrs,
        "litellm.proxy.compression.attempted_input_tokens",
        transforms.get("attempted_input_tokens"),
    )
    _add(attrs, "litellm.proxy.compression.ccr.hashes", headroom_ccr_hashes)
    _add_safe_metadata_attrs(
        attrs,
        "litellm.proxy.headroom.execution.transforms",
        transforms,
    )

    _add_cache_hot_zone(
        attrs, "litellm.proxy.cache.hot_zone", transforms.get("cache_hot_zone")
    )
    deployment = transforms.get("deployment_payload")
    if isinstance(deployment, dict):
        _add_cache_hot_zone(
            attrs,
            "litellm.proxy.cache.deployment.hot_zone",
            deployment.get("cache_hot_zone"),
        )
        _add_mutable_output(
            attrs,
            "litellm.proxy.cache.deployment.mutable_output",
            deployment.get("mutable_output"),
        )

    chunk_hashes = _chunk_hashes(command)
    content_hashes = _content_hashes(command)
    ccr_hashes = tuple(dict.fromkeys((*headroom_ccr_hashes, *chunk_hashes)))[
        :MAX_LIST_ITEMS
    ]
    _add(attrs, "litellm.proxy.compression.chunks.count", len(command.chunks))
    _add(attrs, "litellm.proxy.compression.ccr.hashes", ccr_hashes)
    _add(attrs, "litellm.proxy.compression.content_hashes", content_hashes)
    _add(
        attrs,
        "litellm.proxy.compression.chunks.original_tokens",
        _sum_optional(chunk.original_tokens for chunk in command.chunks),
    )
    _add(
        attrs,
        "litellm.proxy.compression.chunks.compressed_tokens",
        _sum_optional(chunk.compressed_tokens for chunk in command.chunks),
    )
    first_chunk = command.chunks[0] if command.chunks else None
    if first_chunk is not None:
        _add_safe_metadata_attrs(
            attrs,
            "litellm.proxy.headroom.chunk.metadata",
            first_chunk.metadata,
        )

    first_call = command.provider_calls[0] if command.provider_calls else None
    first_usage = _first_usage(first_call)
    _add(attrs, "litellm.proxy.provider.calls.count", len(command.provider_calls))
    if first_call is not None:
        _add_provider_attrs(attrs, first_call, first_usage)
        _add_safe_metadata_attrs(
            attrs,
            "litellm.proxy.provider.response.metadata",
            first_call.raw_response_metadata,
        )
    if first_usage is not None:
        _add_safe_metadata_attrs(
            attrs,
            "litellm.proxy.provider.usage.raw",
            first_usage.raw_usage,
        )
    _add_cache_attrs(attrs, command)
    _add_trace_attrs(attrs, command)

    events = _story_events(
        command,
        applied_transforms=applied_transforms,
        ccr_hashes=ccr_hashes,
        first_call=first_call,
        first_usage=first_usage,
        skip_reason=skip_reason,
    )
    return CompressionTraceStory(attrs, tuple(events))


def record_current_compression_story(
    command: CompressionActivityIngestCommand,
    *,
    success: bool | None = None,
    latency_ms: int | None = None,
) -> None:
    story = compression_trace_story(command, success=success, latency_ms=latency_ms)
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return
    for key, value in story.attributes.items():
        span.set_attribute(key, value)
    for event in story.events:
        span.add_event(event.name, event.attributes)
    for key in CRITICAL_TRACE_ATTR_KEYS:
        value = story.attributes.get(key)
        if value is not None:
            span.set_attribute(key, value)


def _add(
    attrs: dict[str, AttributeValue],
    key: str,
    value: Any,
) -> None:
    attr_value = _attr_value(value)
    if attr_value is not None:
        attrs[key] = attr_value


def _add_id(attrs: dict[str, AttributeValue], key: str, value: Any) -> None:
    text = _text(value, max_length=MAX_ID_TEXT)
    if text is not None:
        attrs[key] = text


def _attr_value(value: Any) -> AttributeValue | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items: list[PrimitiveAttr] = []
        for item in value:
            item_value = _attr_value(item)
            if isinstance(item_value, (str, int, float, bool)):
                items.append(item_value)
            if len(items) >= MAX_LIST_ITEMS:
                break
        return tuple(items) if items else None
    return None


def _text(value: Any, *, max_length: int = MAX_ATTR_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    items: list[str] = []
    for item in value:
        text = _text(item, max_length=MAX_ID_TEXT)
        if text is not None:
            items.append(text)
        if len(items) >= MAX_LIST_ITEMS:
            break
    return tuple(items)


def _add_safe_metadata_attrs(
    attrs: dict[str, AttributeValue],
    prefix: str,
    mapping: Any,
) -> None:
    if not isinstance(mapping, dict) or not mapping:
        return

    visible_keys: list[str] = []
    emitted = 0
    hashed = 0
    redacted = 0
    for raw_key, value in sorted(mapping.items(), key=lambda item: str(item[0])):
        safe_key = _safe_metadata_key(raw_key)
        if safe_key is None:
            continue
        if _is_secret_metadata_key(safe_key):
            redacted += 1
            continue
        visible_keys.append(safe_key)
        if emitted >= MAX_METADATA_ATTRS:
            continue
        new_emitted, new_hashed = _add_safe_metadata_value(
            attrs,
            f"{prefix}.{safe_key}",
            value,
            key=safe_key,
            depth=0,
            remaining=MAX_METADATA_ATTRS - emitted,
        )
        emitted += new_emitted
        hashed += new_hashed

    _add(attrs, f"{prefix}.keys", tuple(visible_keys[:MAX_LIST_ITEMS]))
    _add(attrs, f"{prefix}.keys.count", len(visible_keys) + redacted)
    _add(attrs, f"{prefix}.visible_keys.count", len(visible_keys))
    _add(attrs, f"{prefix}.attributes.count", emitted)
    _add(attrs, f"{prefix}.hashed_values.count", hashed)
    _add(attrs, f"{prefix}.redacted_values.count", redacted)


def _add_safe_metadata_value(
    attrs: dict[str, AttributeValue],
    prefix: str,
    value: Any,
    *,
    key: str,
    depth: int,
    remaining: int,
) -> tuple[int, int]:
    if remaining <= 0:
        return 0, 0
    if _is_content_metadata_key(key):
        return _add_metadata_shape(attrs, prefix, value)
    attr_value = _attr_value(value)
    if attr_value is not None:
        attrs[prefix] = attr_value
        return 1, 0
    if isinstance(value, dict) and depth < 1:
        emitted = 0
        hashed = 0
        redacted = 0
        for raw_key, nested_value in sorted(
            value.items(), key=lambda item: str(item[0])
        ):
            nested_key = _safe_metadata_key(raw_key)
            if nested_key is None:
                continue
            if _is_secret_metadata_key(nested_key):
                redacted += 1
                continue
            new_emitted, new_hashed = _add_safe_metadata_value(
                attrs,
                f"{prefix}.{nested_key}",
                nested_value,
                key=nested_key,
                depth=depth + 1,
                remaining=remaining - emitted,
            )
            emitted += new_emitted
            hashed += new_hashed
            if emitted >= remaining:
                break
        if redacted and emitted < remaining:
            _add(attrs, f"{prefix}.redacted_values.count", redacted)
            emitted += 1
        if emitted:
            return emitted, hashed
    return _add_metadata_shape(attrs, prefix, value)


def _add_metadata_shape(
    attrs: dict[str, AttributeValue],
    prefix: str,
    value: Any,
) -> tuple[int, int]:
    _add(attrs, f"{prefix}.hash", _stable_value_hash(value))
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        _add(attrs, f"{prefix}.items.count", len(value))
    elif value is not None:
        _add(attrs, f"{prefix}.chars", len(str(value)))
    return 2, 1


def _safe_metadata_key(value: Any) -> str | None:
    text = _text(value, max_length=96)
    if text is None:
        return None
    normalized = _SAFE_KEY_RE.sub("_", text).strip("._-").lower()
    return normalized or None


def _is_secret_metadata_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def _is_content_metadata_key(key: str) -> bool:
    if "token" in key:
        return False
    return bool(_CONTENT_KEY_RE.search(key))


def _stable_value_hash(value: Any) -> str:
    encoded = json.dumps(
        _redacted_for_hash(value),
        sort_keys=True,
        default=str,
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _redacted_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _safe_metadata_key(raw_key)
            if key is not None and _is_secret_metadata_key(key):
                redacted[str(raw_key)] = "[REDACTED]"
            else:
                redacted[str(raw_key)] = _redacted_for_hash(raw_value)
        return redacted
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redacted_for_hash(item) for item in value]
    return value


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _session_id(command: CompressionActivityIngestCommand) -> str:
    metadata = command.request.metadata
    for key in (
        "litellm_proxy_run_marker",
        "provider_session_affinity_hash",
        "litellm_proxy_project",
    ):
        value = _text(metadata.get(key), max_length=MAX_ID_TEXT)
        if value is not None:
            return value
    return command.request.request_key[:MAX_ID_TEXT]


def _sum_optional(values: Any) -> int | None:
    total = 0
    saw_value = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        saw_value = True
    return total if saw_value else None


def _chunk_hashes(command: CompressionActivityIngestCommand) -> tuple[str, ...]:
    hashes = [
        text
        for chunk in command.chunks
        if (text := _text(chunk.ccr_hash, max_length=MAX_ID_TEXT)) is not None
    ]
    return tuple(dict.fromkeys(hashes))[:MAX_LIST_ITEMS]


def _content_hashes(command: CompressionActivityIngestCommand) -> tuple[str, ...]:
    hashes = [
        text
        for chunk in command.chunks
        if (text := _text(chunk.content_hash, max_length=MAX_ID_TEXT)) is not None
    ]
    return tuple(dict.fromkeys(hashes))[:MAX_LIST_ITEMS]


def _first_usage(
    provider_call: ProviderCallCommand | None,
) -> TokenUsageBreakdownCommand | None:
    if provider_call is None:
        return None
    for usage in provider_call.token_usage:
        if usage.measurement_source == "provider_reported":
            return usage
    return provider_call.token_usage[0] if provider_call.token_usage else None


def _add_provider_attrs(
    attrs: dict[str, AttributeValue],
    provider_call: ProviderCallCommand,
    usage: TokenUsageBreakdownCommand | None,
) -> None:
    _add(attrs, "litellm.proxy.provider.name", provider_call.provider)
    _add(attrs, "litellm.proxy.provider.model", provider_call.model)
    _add(attrs, "litellm.proxy.provider.status", provider_call.status)
    _add_id(attrs, "litellm.proxy.provider.call_key", provider_call.provider_call_key)
    _add_id(attrs, "litellm.proxy.litellm.call_id", provider_call.litellm_call_id)
    _add_id(
        attrs, "litellm.proxy.provider.request_id", provider_call.provider_request_id
    )
    _add_id(
        attrs, "litellm.proxy.provider.response_id", provider_call.provider_response_id
    )
    _add(attrs, "litellm.proxy.provider.duration_ms", provider_call.duration_ms)
    _add(attrs, "litellm.proxy.provider.cost.total", _measured_cost(provider_call))
    _add(attrs, "litellm.proxy.provider.cost.currency", provider_call.currency)
    _add(attrs, "gen_ai.operation.name", "chat")
    _add(attrs, "gen_ai.provider.name", provider_call.provider)
    _add(attrs, "gen_ai.request.model", provider_call.model)
    _add(attrs, "gen_ai.response.model", provider_call.model)
    _add_id(attrs, "gen_ai.response.id", provider_call.provider_response_id)
    _add(attrs, "llm.model_name", provider_call.model)
    _add(attrs, "llm.provider", provider_call.provider)
    if usage is None:
        return
    _add(attrs, "litellm.proxy.provider.usage.source", usage.measurement_source)
    _add(attrs, "gen_ai.usage.input_tokens", usage.input_tokens)
    _add(attrs, "gen_ai.usage.output_tokens", usage.output_tokens)
    _add(attrs, "litellm.proxy.provider.usage.total_tokens", usage.total_tokens)
    _add(
        attrs,
        "litellm.proxy.provider.usage.cached_input_tokens",
        usage.cached_input_tokens,
    )
    _add(
        attrs,
        "litellm.proxy.provider.usage.cache_write_tokens",
        usage.cache_write_tokens,
    )
    _add(attrs, "litellm.proxy.provider.usage.reasoning_tokens", usage.reasoning_tokens)
    _add(attrs, "llm.token_count.prompt", usage.input_tokens)
    _add(attrs, "llm.token_count.completion", usage.output_tokens)
    _add(attrs, "llm.token_count.total", usage.total_tokens)
    _add(attrs, "llm.token_count.prompt_details.cache_read", usage.cached_input_tokens)
    _add(attrs, "llm.token_count.prompt_details.cache_write", usage.cache_write_tokens)
    _add(attrs, "llm.token_count.completion_details.reasoning", usage.reasoning_tokens)


def _measured_cost(provider_call: ProviderCallCommand) -> float | None:
    if provider_call.cost_total is not None:
        return float(provider_call.cost_total)
    for calculation in provider_call.cost_calculations:
        if (
            calculation.calculation_kind == "measured"
            and calculation.total_cost is not None
        ):
            return float(calculation.total_cost)
    return None


def _add_cache_attrs(
    attrs: dict[str, AttributeValue],
    command: CompressionActivityIngestCommand,
) -> None:
    _add(attrs, "litellm.proxy.cache.activities.count", len(command.cache_activities))
    for system in ("provider", "headroom_ccr", "litellm", "redis"):
        activities = [
            activity
            for activity in command.cache_activities
            if activity.cache_system == system
        ]
        if not activities:
            continue
        prefix = f"litellm.proxy.cache.{system}"
        _add(attrs, f"{prefix}.operations.count", len(activities))
        _add(
            attrs,
            f"{prefix}.tokens_read",
            _sum_optional(a.tokens_read for a in activities),
        )
        _add(
            attrs,
            f"{prefix}.tokens_written",
            _sum_optional(a.tokens_written for a in activities),
        )
        hits = [activity.hit for activity in activities if activity.hit is not None]
        if hits:
            _add(attrs, f"{prefix}.hit", any(hits))


def _add_trace_attrs(
    attrs: dict[str, AttributeValue],
    command: CompressionActivityIngestCommand,
) -> None:
    for prefix, trace_context in (
        ("event", command.event.trace),
        ("request", command.request.trace),
        ("execution", command.execution.trace),
    ):
        _add_id(attrs, f"litellm.proxy.trace.{prefix}.trace_id", trace_context.trace_id)
        _add_id(attrs, f"litellm.proxy.trace.{prefix}.span_id", trace_context.span_id)


def _add_cache_hot_zone(
    attrs: dict[str, AttributeValue],
    prefix: str,
    value: Any,
) -> None:
    if not isinstance(value, dict):
        return
    for key in (
        "stable_prefix_hash",
        "stable_prefix_without_prompt_cache_key_hash",
        "stable_top_level_hash",
        "stable_input_prefix_hash",
        "input_type",
    ):
        _add(attrs, f"{prefix}.{key}", value.get(key))
    for key in (
        "stable_prefix_bytes",
        "input_item_count",
        "stable_input_item_count",
    ):
        _add(attrs, f"{prefix}.{key}", value.get(key))
    boundary = value.get("mutable_boundary")
    if isinstance(boundary, dict):
        _add(
            attrs, f"{prefix}.mutable_boundary.input_index", boundary.get("input_index")
        )
        _add(attrs, f"{prefix}.mutable_boundary.item_type", boundary.get("item_type"))


def _add_mutable_output(
    attrs: dict[str, AttributeValue],
    prefix: str,
    value: Any,
) -> None:
    if not isinstance(value, dict):
        return
    for key in (
        "output_item_count",
        "text_output_item_count",
        "output_bytes",
        "output_tokens_estimate",
    ):
        _add(attrs, f"{prefix}.{key}", value.get(key))
    _add(attrs, f"{prefix}.output_hash", value.get("output_hash"))
    _add(
        attrs,
        f"{prefix}.output_item_types",
        _string_list(value.get("output_item_types")),
    )


def _story_events(
    command: CompressionActivityIngestCommand,
    *,
    applied_transforms: tuple[str, ...],
    ccr_hashes: tuple[str, ...],
    first_call: ProviderCallCommand | None,
    first_usage: TokenUsageBreakdownCommand | None,
    skip_reason: str | None,
) -> list[TraceStoryEvent]:
    events = [
        TraceStoryEvent(
            "compression.request.captured",
            _event_attrs(
                request_key=command.request.request_key,
                route=command.request.incoming_route,
                strategy=command.config.strategy_name,
                model=command.request.model_hint or command.config.target_model,
                status=command.execution.status,
            ),
        )
    ]
    metadata_sections = _metadata_sections(command, first_call, first_usage)
    if metadata_sections:
        events.append(
            TraceStoryEvent(
                "headroom.metadata.captured",
                _event_attrs(
                    sections=metadata_sections,
                    request_metadata_keys=len(command.request.metadata),
                    transform_keys=len(command.execution.transforms),
                    chunk_metadata_keys=sum(
                        len(chunk.metadata) for chunk in command.chunks
                    ),
                    provider_metadata_keys=(
                        len(first_call.raw_response_metadata)
                        if first_call is not None
                        else 0
                    ),
                ),
            )
        )
    if skip_reason:
        events.append(
            TraceStoryEvent(
                "compression.skipped",
                _event_attrs(reason=skip_reason, status=command.execution.status),
            )
        )
    if applied_transforms:
        events.append(
            TraceStoryEvent(
                "compression.transforms.applied",
                _event_attrs(
                    transforms=applied_transforms,
                    tokens_before=command.execution.original_tokens,
                    tokens_after=command.execution.compressed_tokens,
                    tokens_saved=command.execution.tokens_saved,
                ),
            )
        )
    if ccr_hashes:
        events.append(
            TraceStoryEvent(
                "compression.ccr.stored",
                _event_attrs(ccr_hashes=ccr_hashes, chunk_count=len(command.chunks)),
            )
        )
    if first_call is not None:
        provider_attrs = _event_attrs(
            provider=first_call.provider,
            model=first_call.model,
            status=first_call.status,
            response_id=first_call.provider_response_id,
            cost_total=_measured_cost(first_call),
        )
        if first_usage is not None:
            provider_attrs.update(
                _event_attrs(
                    measurement_source=first_usage.measurement_source,
                    input_tokens=first_usage.input_tokens,
                    cached_input_tokens=first_usage.cached_input_tokens,
                    cache_write_tokens=first_usage.cache_write_tokens,
                    output_tokens=first_usage.output_tokens,
                    reasoning_tokens=first_usage.reasoning_tokens,
                    total_tokens=first_usage.total_tokens,
                )
            )
        events.append(TraceStoryEvent("provider.usage.reported", provider_attrs))
    if command.cache_activities:
        events.append(
            TraceStoryEvent(
                "provider.cache.reported",
                _event_attrs(
                    cache_systems=tuple(
                        dict.fromkeys(
                            activity.cache_system
                            for activity in command.cache_activities
                        )
                    ),
                    provider_hit=any(
                        activity.hit is True and activity.cache_system == "provider"
                        for activity in command.cache_activities
                    ),
                    cache_activity_count=len(command.cache_activities),
                ),
            )
        )
    events.append(
        TraceStoryEvent(
            "economics.verdict",
            _event_attrs(
                usefulness_verdict="unproven_without_direct_vs_proxy_aggregate",
                measured_cost_available=first_call is not None
                and _measured_cost(first_call) is not None,
                cost_total=_measured_cost(first_call)
                if first_call is not None
                else None,
                savings_claim_allowed=False,
            ),
        )
    )
    return events


def _metadata_sections(
    command: CompressionActivityIngestCommand,
    first_call: ProviderCallCommand | None,
    first_usage: TokenUsageBreakdownCommand | None,
) -> tuple[str, ...]:
    sections: list[str] = []
    if command.request.metadata:
        sections.append("request")
    if command.execution.transforms:
        sections.append("transforms")
    if any(chunk.metadata for chunk in command.chunks):
        sections.append("chunks")
    if first_call is not None and first_call.raw_response_metadata:
        sections.append("provider_response")
    if first_usage is not None and first_usage.raw_usage:
        sections.append("provider_usage")
    return tuple(sections)


def _event_attrs(**values: Any) -> dict[str, AttributeValue]:
    attrs: dict[str, AttributeValue] = {}
    for key, value in values.items():
        _add(attrs, key, value)
    return attrs
