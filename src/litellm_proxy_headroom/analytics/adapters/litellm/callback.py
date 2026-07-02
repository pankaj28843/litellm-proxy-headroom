from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from headroom.agent_savings import get_agent_savings_profile
from headroom.compress import CompressConfig, compress
from headroom.integrations.litellm_callback import (
    HeadroomCallback as _HeadroomLiteLLMCallback,
)
from headroom.tokenizers import get_tokenizer
from litellm.integrations.custom_logger import CustomLogger

from ...application.buffering import AsyncIngestionBuffer, AsyncIngestionBufferConfig
from ...application.commands import (
    CompressionActivityIngestCommand,
    CompressionChunkCommand,
    CompressionConfigCommand,
    CompressionExecutionCommand,
    CompressionRequestCommand,
    IngestionEventCommand,
    ProviderCallCommand,
    TraceContextCommand,
)
from ..headroom.hooks import AnalyticsCompressionHooks
from ..otel.telemetry import get_analytics_telemetry
from .http_client import AnalyticsHttpClient, AnalyticsHttpClientConfig
from .trace_mapping import trace_context_from_litellm_payload
from .usage_mapping import (
    provider_response_metadata,
    response_cost,
    response_id,
    token_usage_from_response,
)

logger = logging.getLogger(__name__)

DEFAULT_SAVINGS_PROFILE = "agent-90"
SAVINGS_PROFILE_ENV = "HEADROOM_SAVINGS_PROFILE"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
PROXY_RUN_MARKER_HEADER = "x-llm-proxy-run"
PROXY_PROJECT_HEADER = "x-llm-proxy-project"
PROXY_CLIENT_HEADER = "x-llm-proxy-client"
PROXY_COMPRESSION_HEADER = "x-llm-proxy-compression"
PROXY_RESPONSES_PROVIDER_PASSTHROUGH_HEADER = (
    "x-llm-proxy-responses-provider-passthrough"
)
ANALYTICS_RETRIEVAL_TOOL_NAME = "litellm_proxy_analytics_retrieve_chunk"
RESPONSES_MIN_MUTABLE_BYTES = 512
RESPONSES_OUTPUT_ITEM_TYPES = frozenset(
    {
        "custom_tool_call_output",
        "function_call_output",
        "local_shell_call_output",
        "apply_patch_call_output",
    }
)
RESPONSES_TOOL_SCHEMA_DROP_KEYS = frozenset(
    {
        "$id",
        "$schema",
        "$comment",
        "deprecated",
        "examples",
        "example",
        "markdownDescription",
        "readOnly",
        "title",
        "writeOnly",
    }
)
RESPONSES_TOOL_SCHEMA_COMPACTION_ENV = "HEADROOM_RESPONSES_TOOL_SCHEMA_COMPACTION"
RESPONSES_MUTABLE_OUTPUT_COMPRESSION_ENV = (
    "HEADROOM_RESPONSES_MUTABLE_OUTPUT_COMPRESSION"
)
RESPONSES_DROP_CODEX_PROMPT_CACHE_KEY_ENV = (
    "HEADROOM_RESPONSES_DROP_CODEX_PROMPT_CACHE_KEY"
)
RESPONSES_CHATGPT_PROVIDER_PASSTHROUGH_ENV = (
    "HEADROOM_RESPONSES_CHATGPT_PROVIDER_PASSTHROUGH"
)
RESPONSES_CODEX_HEADER_PASSTHROUGH_ENV = (
    "HEADROOM_RESPONSES_CODEX_HEADER_PASSTHROUGH"
)
RESPONSES_CHATGPT_SESSION_AFFINITY_ENV = "HEADROOM_RESPONSES_CHATGPT_SESSION_AFFINITY"
DEBUG_PAYLOAD_SHAPES_ENV = "LITELLM_PROXY_DEBUG_PAYLOAD_SHAPES"
RESPONSES_CHATGPT_SESSION_AFFINITY_PREFIX = "codex-cache"
RESPONSES_CODEX_HEADER_PASSTHROUGH_NAMES = frozenset(
    {
        "originator",
        "session-id",
        "thread-id",
        "x-client-request-id",
        "x-codex-beta-features",
        "x-codex-parent-thread-id",
        "x-codex-turn-metadata",
        "x-codex-turn-state",
        "x-codex-window-id",
        "x-openai-internal-codex-responses-lite",
        "x-openai-memgen-request",
        "x-openai-subagent",
        "x-responsesapi-include-timing-metrics",
    }
)
RESPONSES_PROVIDER_PASSTHROUGH_FIELDS = frozenset(
    {
        "client_metadata",
        "max_output_tokens",
        "model",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt_cache_key",
        "prompt_cache_retention",
        "service_tier",
        "store",
        "stream",
        "text",
        "truncation",
    }
)
RESPONSES_CACHE_STABLE_TOP_LEVEL_KEYS = frozenset(
    {
        "client_metadata",
        "include",
        "instructions",
        "max_output_tokens",
        "model",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt_cache_key",
        "prompt_cache_retention",
        "reasoning",
        "response_format",
        "service_tier",
        "store",
        "stream",
        "text",
        "tool_choice",
        "tools",
        "truncation",
    }
)
RESPONSES_CACHE_IGNORED_TOP_LEVEL_KEYS = frozenset(
    {
        "api_key",
        "headers",
        "litellm_call_id",
        "litellm_session_id",
        "litellm_logging_obj",
        "litellm_params",
        "metadata",
        "proxy_server_request",
        "user_api_key",
    }
)
RESPONSES_CACHE_DIAGNOSTIC_VOLATILE_TOP_LEVEL_KEYS = frozenset(
    {
        "litellm_session_id",
        "prompt_cache_key",
    }
)
COMPRESSION_HEADER_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
LOCAL_WRAPPER_SYSTEM_AS_USER_CLIENTS = frozenset(
    {"claude", "copilot", "opencode", "pi"}
)
WRAPPER_SYSTEM_AS_USER_PREFIX = "System instructions:\n\n"


@dataclass(frozen=True, slots=True)
class CompressionCapture:
    request_key: str
    event_key: str
    litellm_call_id: str | None
    model: str
    incoming_route: str | None
    request_metadata: dict[str, Any]
    tokens_before: int | None
    tokens_after: int | None
    tokens_saved: int | None
    compression_ratio: float | None
    transforms_applied: list[Any]
    compression_status: str | None
    skip_reason: str | None
    attempted_input_tokens: int | None
    started_at_ms: int
    ccr_hash: str
    ccr_hashes: tuple[str, ...]
    content_hash: str
    cache_hot_zone: dict[str, Any] | None
    trace: TraceContextCommand


@dataclass(frozen=True, slots=True)
class ResponsesOutputSlot:
    item_index: int
    text: str
    item_type: str


@dataclass(frozen=True, slots=True)
class ResponsesToolCompaction:
    tools_before_tokens: int
    tools_after_tokens: int
    tokens_saved: int
    tools_before_bytes: int
    tools_after_bytes: int


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bounded_identifier(
    value: str | None, prefix: str, max_length: int = 255
) -> str | None:
    if not value:
        return None
    if len(value) <= max_length:
        return value
    digest = _stable_hash(value)[:48]
    bounded = f"{prefix}:{digest}"
    return bounded[:max_length]


def _sanitized_metadata_text(value: str | None, max_length: int = 255) -> str | None:
    if not value:
        return None
    decoded = urllib.parse.unquote(value)
    cleaned = "".join(ch for ch in decoded if ch.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _event_key(request_key: str, idempotency_key: str, status: str) -> str:
    candidate = f"{request_key}:{idempotency_key}:{status}"
    if len(candidate) <= 255:
        return candidate

    digest = _stable_hash(
        {
            "request_key": request_key,
            "idempotency_key": idempotency_key,
            "status": status,
        }
    )[:48]
    suffix = f":{digest}:{status}"
    return f"{request_key[: 255 - len(suffix)]}{suffix}"


def _metadata(container: dict[str, Any]) -> dict[str, Any]:
    value = container.get("metadata")
    if isinstance(value, dict):
        return value
    value = container.get("litellm_metadata")
    if isinstance(value, dict):
        return value
    litellm_params = container.get("litellm_params")
    if isinstance(litellm_params, dict) and isinstance(
        litellm_params.get("metadata"), dict
    ):
        return litellm_params["metadata"]
    return {}


def _incoming_route_from_data(data: dict[str, Any]) -> str | None:
    metadata = _metadata(data)
    for key in ("incoming_route", "route", "path", "request_path"):
        value = metadata.get(key) or data.get(key)
        if value:
            return str(value)
    if data.get("input") is not None and data.get("messages") is None:
        return "/v1/responses"
    if data.get("messages") is not None:
        return "/v1/chat/completions"
    return None


def _proxy_request_headers(data: dict[str, Any]) -> dict[str, Any]:
    proxy_request = data.get("proxy_server_request")
    if not isinstance(proxy_request, dict):
        litellm_params = data.get("litellm_params")
        if isinstance(litellm_params, dict):
            proxy_request = litellm_params.get("proxy_server_request")
    if isinstance(proxy_request, dict):
        headers = proxy_request.get("headers")
        if isinstance(headers, dict):
            return headers

    for metadata in _candidate_metadata_maps(data):
        headers = metadata.get("headers")
        if isinstance(headers, dict):
            return headers
    return {}


def _candidate_metadata_maps(data: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    maps: list[dict[str, Any]] = []
    for key in ("metadata", "litellm_metadata"):
        value = data.get(key)
        if isinstance(value, dict):
            maps.append(value)

    litellm_params = data.get("litellm_params")
    if isinstance(litellm_params, dict):
        metadata = litellm_params.get("metadata")
        if isinstance(metadata, dict):
            maps.append(metadata)
    return tuple(maps)


def _is_proxy_logging_payload(data: dict[str, Any]) -> bool:
    if data.get("proxy_server_request") is not None:
        return True
    litellm_params = data.get("litellm_params")
    return isinstance(litellm_params, dict) and (
        litellm_params.get("proxy_server_request") is not None
    )


def _is_final_stream_success_logging_payload(data: dict[str, Any]) -> bool:
    if data.get("stream") is not True:
        return False
    if (
        data.get("async_complete_streaming_response") is not None
        or data.get("complete_streaming_response") is not None
    ):
        return True
    standard_logging_object = data.get("standard_logging_object")
    return (
        isinstance(standard_logging_object, dict)
        and standard_logging_object.get("stream") is True
    )


def _header_value(headers: dict[str, Any], header_name: str) -> str | None:
    wanted = header_name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted and value:
            return str(value)
    return None


def _safe_header_passthrough_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text or "\r" in text or "\n" in text:
        return None
    return text


def _has_header(headers: dict[str, Any], header_name: str) -> bool:
    wanted = header_name.lower()
    return any(str(key).lower() == wanted for key in headers)


def _active_savings_profile(environ: Mapping[str, str] | None = None) -> str:
    source = environ if environ is not None else os.environ
    requested = source.get(SAVINGS_PROFILE_ENV, DEFAULT_SAVINGS_PROFILE).strip()
    return get_agent_savings_profile(requested or DEFAULT_SAVINGS_PROFILE).name


def _env_flag_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _request_metadata_from_data(
    data: dict[str, Any], savings_profile: str
) -> dict[str, Any]:
    source_metadata = _metadata(data)
    headers = _proxy_request_headers(data)
    metadata: dict[str, Any] = {
        "integration": "litellm-responses"
        if data.get("input") is not None and data.get("messages") is None
        else "litellm-chat",
        "savings_profile": savings_profile,
    }
    marker = _header_value(headers, PROXY_RUN_MARKER_HEADER) or _metadata_value(
        source_metadata,
        "litellm_proxy_run_marker",
        PROXY_RUN_MARKER_HEADER,
    )
    if marker:
        metadata["litellm_proxy_run_marker"] = _bounded_identifier(marker, "run")
    project = _sanitized_metadata_text(
        _header_value(headers, PROXY_PROJECT_HEADER)
        or _metadata_value(
            source_metadata,
            "litellm_proxy_project",
            PROXY_PROJECT_HEADER,
        )
    )
    if project:
        metadata["litellm_proxy_project"] = project
    client = _sanitized_metadata_text(
        _header_value(headers, PROXY_CLIENT_HEADER)
        or _metadata_value(
            source_metadata,
            "litellm_proxy_client",
            PROXY_CLIENT_HEADER,
        ),
        max_length=64,
    )
    if client:
        metadata["litellm_proxy_client"] = client
    compression_mode = _compression_mode_from_data(data) or _sanitized_metadata_text(
        _metadata_value(
            source_metadata,
            "litellm_proxy_compression_mode",
            PROXY_COMPRESSION_HEADER,
        ),
        max_length=32,
    )
    if compression_mode:
        metadata["litellm_proxy_compression_mode"] = compression_mode.lower()
    provider_passthrough_mode = _responses_provider_passthrough_mode_from_data(data)
    if provider_passthrough_mode:
        metadata["litellm_proxy_responses_provider_passthrough"] = (
            provider_passthrough_mode
        )
    affinity_hash = _provider_session_affinity_hash(data)
    if affinity_hash:
        metadata["provider_session_affinity_source"] = "prompt_cache_key"
        metadata["provider_session_affinity_hash"] = affinity_hash
    return metadata


def _metadata_value(metadata: dict[str, Any], *keys: str) -> str | None:
    wanted = {key.lower().replace("-", "_") for key in keys}
    for key, value in metadata.items():
        if str(key).lower().replace("-", "_") in wanted and value:
            return str(value)
    return None


def _proxy_client_from_data(data: dict[str, Any]) -> str | None:
    return _sanitized_metadata_text(
        _header_value(_proxy_request_headers(data), PROXY_CLIENT_HEADER)
        or _metadata_value(_metadata(data), "litellm_proxy_client", PROXY_CLIENT_HEADER),
        max_length=64,
    )


def _compression_mode_from_data(data: dict[str, Any]) -> str | None:
    value = _sanitized_metadata_text(
        _header_value(_proxy_request_headers(data), PROXY_COMPRESSION_HEADER),
        max_length=32,
    )
    return value.lower() if value else None


def _responses_provider_passthrough_mode_from_data(data: dict[str, Any]) -> str | None:
    value = _sanitized_metadata_text(
        _header_value(
            _proxy_request_headers(data),
            PROXY_RESPONSES_PROVIDER_PASSTHROUGH_HEADER,
        ),
        max_length=32,
    )
    if not value:
        return None
    normalized = value.lower()
    if normalized in COMPRESSION_HEADER_DISABLED_VALUES:
        return "off"
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return "on"
    return None


def _compression_disabled_by_proxy_header(data: dict[str, Any]) -> bool:
    value = _compression_mode_from_data(data)
    return value in COMPRESSION_HEADER_DISABLED_VALUES


def _compression_disabled_result(
    transforms_applied: list[Any], *, savings_profile: str
) -> dict[str, Any]:
    return {
        "messages": None,
        "tokens_before": None,
        "tokens_after": None,
        "tokens_saved": None,
        "compression_ratio": None,
        "transforms_applied": list(dict.fromkeys(transforms_applied)),
        "savings_profile": savings_profile,
        "skip_reason": "compression_disabled_by_proxy_header",
        "attempted_input_tokens": 0,
        "responses_units_attempted": 0,
        "responses_units_modified": 0,
    }


def _existing_chatgpt_session_id(data: dict[str, Any]) -> str | None:
    for key in ("litellm_session_id", "session_id"):
        value = data.get(key)
        if value:
            return str(value)

    metadata = data.get("metadata")
    if isinstance(metadata, dict) and metadata.get("session_id"):
        return str(metadata["session_id"])

    litellm_params = data.get("litellm_params")
    if isinstance(litellm_params, dict):
        for key in ("litellm_session_id", "session_id"):
            value = litellm_params.get(key)
            if value:
                return str(value)
        params_metadata = litellm_params.get("metadata")
        if isinstance(params_metadata, dict) and params_metadata.get("session_id"):
            return str(params_metadata["session_id"])
    return None


def _sync_logging_session_id(data: dict[str, Any], session_id: str) -> None:
    logging_obj = data.get("litellm_logging_obj")
    for container_name in ("litellm_params",):
        container = getattr(logging_obj, container_name, None)
        if isinstance(container, dict):
            container.setdefault("litellm_session_id", session_id)
            metadata = container.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata.setdefault("session_id", session_id)
    model_call_details = getattr(logging_obj, "model_call_details", None)
    if isinstance(model_call_details, dict):
        litellm_params = model_call_details.setdefault("litellm_params", {})
        if isinstance(litellm_params, dict):
            litellm_params.setdefault("litellm_session_id", session_id)
            metadata = litellm_params.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata.setdefault("session_id", session_id)


def _apply_codex_chatgpt_session_affinity(
    data: dict[str, Any], model: str
) -> str | None:
    if not _env_flag_enabled(RESPONSES_CHATGPT_SESSION_AFFINITY_ENV, default=True):
        return None
    if data.get("input") is None or data.get("messages") is not None:
        return None
    if _proxy_client_from_data(data) != "codex":
        return None
    if _existing_chatgpt_session_id(data):
        return None

    prompt_cache_key = data.get("prompt_cache_key")
    if not isinstance(prompt_cache_key, str) or not prompt_cache_key:
        return None

    session_hash = _stable_hash({"model": model, "prompt_cache_key": prompt_cache_key})[
        :48
    ]
    session_id = f"{RESPONSES_CHATGPT_SESSION_AFFINITY_PREFIX}-{session_hash}"
    data["litellm_session_id"] = session_id

    litellm_params = data.get("litellm_params")
    if isinstance(litellm_params, dict):
        litellm_params.setdefault("litellm_session_id", session_id)
        params_metadata = litellm_params.setdefault("metadata", {})
        if isinstance(params_metadata, dict):
            params_metadata.setdefault("session_id", session_id)

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        data["metadata"] = metadata
    metadata.setdefault("session_id", session_id)
    _sync_logging_session_id(data, session_id)
    return session_hash


def _provider_session_affinity_hash(data: dict[str, Any]) -> str | None:
    value = data.get("litellm_session_id")
    if not value:
        return None
    session_id = str(value)
    prefix = f"{RESPONSES_CHATGPT_SESSION_AFFINITY_PREFIX}-"
    if not session_id.startswith(prefix):
        return None
    return session_id[len(prefix) :]


def _guard_codex_prompt_cache_key(data: dict[str, Any]) -> bool:
    if not _env_flag_enabled(RESPONSES_DROP_CODEX_PROMPT_CACHE_KEY_ENV):
        return False
    if _proxy_client_from_data(data) != "codex":
        return False
    if "prompt_cache_key" not in data:
        return False
    data.pop("prompt_cache_key", None)
    return True


def _preserve_codex_responses_headers(data: dict[str, Any]) -> list[str]:
    """Forward only Codex-native Responses route headers through LiteLLM."""

    if not _env_flag_enabled(RESPONSES_CODEX_HEADER_PASSTHROUGH_ENV, default=True):
        return []
    if data.get("input") is None or data.get("messages") is not None:
        return []
    if _proxy_client_from_data(data) != "codex":
        return []

    source_headers = _proxy_request_headers(data)
    if not source_headers:
        return []

    extra_headers = data.get("extra_headers")
    if not isinstance(extra_headers, dict):
        extra_headers = {}
        data["extra_headers"] = extra_headers

    preserved: list[str] = []
    for source_name, source_value in source_headers.items():
        header_name = str(source_name).lower()
        if header_name not in RESPONSES_CODEX_HEADER_PASSTHROUGH_NAMES:
            continue
        header_value = _safe_header_passthrough_value(source_value)
        if header_value is None:
            continue
        if _has_header(extra_headers, header_name):
            preserved.append(header_name)
            continue
        extra_headers[header_name] = header_value
        preserved.append(header_name)

    if not preserved and not extra_headers:
        data.pop("extra_headers", None)
    return sorted(dict.fromkeys(preserved))


def _preserve_responses_provider_passthrough_fields(data: dict[str, Any]) -> list[str]:
    """Keep cache-sensitive Responses fields through LiteLLM provider transforms."""

    preserved: list[str] = []
    extra_body = data.get("extra_body")
    if not isinstance(extra_body, dict):
        extra_body = {}
        data["extra_body"] = extra_body

    for field in sorted(RESPONSES_PROVIDER_PASSTHROUGH_FIELDS):
        if field not in data:
            continue
        if field in extra_body and extra_body[field] == data[field]:
            preserved.append(field)
            continue
        extra_body[field] = data[field]
        preserved.append(field)

    if not preserved and not extra_body:
        data.pop("extra_body", None)
    return preserved


def _should_preserve_responses_provider_passthrough(data: dict[str, Any]) -> bool:
    request_mode = _responses_provider_passthrough_mode_from_data(data)
    if request_mode is not None:
        return request_mode == "on"
    raw = os.getenv(RESPONSES_CHATGPT_PROVIDER_PASSTHROUGH_ENV)
    if raw is not None:
        return _env_flag_enabled(RESPONSES_CHATGPT_PROVIDER_PASSTHROUGH_ENV)
    return _proxy_client_from_data(data) == "codex"


def _incoming_route_from_response(
    incoming_route: str | None, response: Any, response_key: str | None
) -> str | None:
    if incoming_route != "/v1/chat/completions":
        return incoming_route
    response_object = (
        response.get("object")
        if isinstance(response, dict)
        else getattr(response, "object", None)
    )
    if response_object == "response":
        return "/v1/responses"
    if response_key and response_key.startswith("resp_"):
        return "/v1/responses"
    return incoming_route


def _request_key_from_data(data: dict[str, Any]) -> str:
    metadata = _metadata(data)
    for key in (
        "litellm_proxy_analytics_request_key",
        "request_id",
        "litellm_call_id",
        "trace_id",
    ):
        value = metadata.get(key) or data.get(key)
        if value:
            return str(value)
    return f"litellm-{uuid.uuid4()}"


def _litellm_call_id_from_data(data: dict[str, Any]) -> str | None:
    metadata = _metadata(data)
    for container in (metadata, data):
        value = container.get("litellm_call_id")
        if value:
            return _bounded_identifier(str(value), "litellm-call")

    litellm_params = data.get("litellm_params")
    if isinstance(litellm_params, dict):
        value = litellm_params.get("litellm_call_id")
        if value:
            return _bounded_identifier(str(value), "litellm-call")

    logging_obj = data.get("litellm_logging_obj")
    value = getattr(logging_obj, "litellm_call_id", None)
    if value:
        return _bounded_identifier(str(value), "litellm-call")

    model_call_details = getattr(logging_obj, "model_call_details", None)
    if isinstance(model_call_details, dict):
        value = model_call_details.get("litellm_call_id")
        if value:
            return _bounded_identifier(str(value), "litellm-call")
        litellm_params = model_call_details.get("litellm_params")
        if isinstance(litellm_params, dict):
            value = litellm_params.get("litellm_call_id")
            if value:
                return _bounded_identifier(str(value), "litellm-call")

    return None


def _ensure_request_key(data: dict[str, Any]) -> str:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        data["metadata"] = metadata

    existing = metadata.get("litellm_proxy_analytics_request_key")
    if existing:
        request_key = str(existing)
        _sync_logging_request_key(data, request_key)
        return request_key

    request_key = _request_key_from_data(data)
    metadata["litellm_proxy_analytics_request_key"] = request_key
    _sync_logging_request_key(data, request_key)
    return request_key


def _sync_logging_request_key(data: dict[str, Any], request_key: str) -> None:
    logging_obj = data.get("litellm_logging_obj")
    for container_name in ("litellm_params",):
        container = getattr(logging_obj, container_name, None)
        if isinstance(container, dict):
            metadata = container.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["litellm_proxy_analytics_request_key"] = request_key
    model_call_details = getattr(logging_obj, "model_call_details", None)
    if isinstance(model_call_details, dict):
        litellm_params = model_call_details.setdefault("litellm_params", {})
        if isinstance(litellm_params, dict):
            metadata = litellm_params.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["litellm_proxy_analytics_request_key"] = request_key


def _user_api_key_value(user_api_key: Any, user_api_key_dict: Any) -> str:
    if user_api_key is not None:
        return str(user_api_key)
    if isinstance(user_api_key_dict, dict):
        return str(
            user_api_key_dict.get("token")
            or user_api_key_dict.get("api_key")
            or user_api_key_dict.get("key_alias")
            or ""
        )
    for attr in ("token", "api_key", "key_alias"):
        value = getattr(user_api_key_dict, attr, None)
        if value:
            return str(value)
    return ""


def _duration_ms(start_time: Any, end_time: Any) -> int | None:
    try:
        return max(int((end_time - start_time).total_seconds() * 1000), 0)
    except Exception:
        return None


def _message_text(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts) if parts else None
    return None


def _content_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _content_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        content = value.get("content")
        if content is not None:
            return _content_text(content)
    return None


def _move_wrapper_system_to_user_message(data: dict[str, Any]) -> bool:
    client = _proxy_client_from_data(data)
    if not client or client.split("-", 1)[0] not in LOCAL_WRAPPER_SYSTEM_AS_USER_CLIENTS:
        return False

    messages = data.get("messages")
    if not isinstance(messages, list):
        return False

    system_parts: list[str] = []
    if "system" in data:
        system_text = _content_text(data.pop("system"))
        if system_text:
            system_parts.append(system_text)

    user_messages: list[Any] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            system_text = _message_text(message) or _content_text(message)
            if system_text:
                system_parts.append(system_text)
            continue
        user_messages.append(message)

    if not system_parts:
        data["messages"] = user_messages
        return False

    data["messages"] = [
        {
            "role": "user",
            "content": WRAPPER_SYSTEM_AS_USER_PREFIX + "\n\n".join(system_parts),
        },
        *user_messages,
    ]
    return True


def _payload_debug_shape(data: dict[str, Any], call_type: str) -> dict[str, Any]:
    messages = data.get("messages")
    input_value = data.get("input")
    metadata = _metadata(data)
    headers = _proxy_request_headers(data)
    return {
        "call_type": call_type,
        "client": _proxy_client_from_data(data),
        "keys": sorted(
            key
            for key in data
            if key not in {"api_key", "headers", "user_api_key"}
        ),
        "metadata_keys": sorted(metadata),
        "proxy_header_keys": sorted(headers),
        "message_roles": [
            message.get("role") if isinstance(message, dict) else type(message).__name__
            for message in messages
        ]
        if isinstance(messages, list)
        else None,
        "input_type": type(input_value).__name__ if input_value is not None else None,
        "input_item_types": [
            item.get("type") if isinstance(item, dict) else type(item).__name__
            for item in input_value[:8]
        ]
        if isinstance(input_value, list)
        else None,
        "has_system": "system" in data,
        "has_instructions": "instructions" in data,
    }


def _json_token_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_byte_len(value: Any) -> int:
    return len(_json_token_text(value).encode("utf-8", errors="replace"))


def _count_text_tokens(text: str, model: str) -> int:
    return int(get_tokenizer(model or DEFAULT_MODEL).count_text(text))


def _compact_responses_tool_schema_value(
    value: Any, parent_key: str | None = None
) -> Any:
    if isinstance(value, list):
        return [
            _compact_responses_tool_schema_value(item, parent_key) for item in value
        ]
    if not isinstance(value, dict):
        return value

    compacted: dict[str, Any] = {}
    for key, child in value.items():
        if parent_key != "properties" and key in RESPONSES_TOOL_SCHEMA_DROP_KEYS:
            continue
        if key == "description" and isinstance(child, str):
            compacted[key] = " ".join(child.split())
            continue
        compacted[key] = _compact_responses_tool_schema_value(child, key)
    return compacted


def _compact_responses_tools(
    data: dict[str, Any], model: str
) -> ResponsesToolCompaction | None:
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        return None

    compacted_tools = _compact_responses_tool_schema_value(tools)
    before_bytes = _json_byte_len(tools)
    after_bytes = _json_byte_len(compacted_tools)
    if after_bytes >= before_bytes:
        return None

    try:
        before_tokens = _count_text_tokens(_json_token_text(tools), model)
        after_tokens = _count_text_tokens(_json_token_text(compacted_tools), model)
    except Exception:
        return None
    tokens_saved = before_tokens - after_tokens
    if tokens_saved <= 0:
        return None

    data["tools"] = compacted_tools
    return ResponsesToolCompaction(
        tools_before_tokens=before_tokens,
        tools_after_tokens=after_tokens,
        tokens_saved=tokens_saved,
        tools_before_bytes=before_bytes,
        tools_after_bytes=after_bytes,
    )


def _responses_mutable_output_slots(
    data: dict[str, Any], *, min_bytes: int = RESPONSES_MIN_MUTABLE_BYTES
) -> tuple[list[ResponsesOutputSlot], str | None]:
    input_value = data.get("input")
    if input_value is None:
        return [], "responses_missing_input"
    if isinstance(input_value, str):
        return [], "responses_string_input_protected"
    if not isinstance(input_value, list):
        return [], "responses_unsupported_input_shape"

    protected_retrieval_call_ids = _responses_retrieval_call_ids(input_value)
    slots: list[ResponsesOutputSlot] = []
    saw_output_item = False
    saw_protected_retrieval_output = False
    saw_text_output = False
    saw_below_floor = False

    for item_index, item in enumerate(input_value):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in RESPONSES_OUTPUT_ITEM_TYPES:
            continue
        saw_output_item = True
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id in protected_retrieval_call_ids:
            saw_protected_retrieval_output = True
            continue
        output = item.get("output")
        if not isinstance(output, str) or not output:
            continue
        saw_text_output = True
        if len(output.encode("utf-8", errors="replace")) < min_bytes:
            saw_below_floor = True
            continue
        slots.append(
            ResponsesOutputSlot(
                item_index=item_index,
                text=output,
                item_type=str(item_type),
            )
        )

    if slots:
        return slots, None
    if saw_below_floor:
        return [], "responses_below_floor"
    if saw_text_output:
        return [], "responses_no_mutable_units"
    if saw_protected_retrieval_output:
        return [], "responses_retrieval_output_protected"
    if saw_output_item:
        return [], "responses_output_without_text_slot"
    return [], "responses_no_mutable_units"


def _responses_retrieval_call_ids(input_value: list[Any]) -> set[str]:
    call_ids: set[str] = set()
    for item in input_value:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        name = item.get("name")
        call_id = item.get("call_id")
        if not isinstance(name, str) or not isinstance(call_id, str) or not call_id:
            continue
        if name == ANALYTICS_RETRIEVAL_TOOL_NAME or name.endswith(
            f"__{ANALYTICS_RETRIEVAL_TOOL_NAME}"
        ):
            call_ids.add(call_id)
    return call_ids


def _field_shape(value: Any) -> dict[str, Any]:
    shape: dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, dict):
        shape["keys"] = sorted(str(key) for key in value)
    elif isinstance(value, list):
        shape["length"] = len(value)
    elif isinstance(value, str):
        shape["chars"] = len(value)
    return shape


def _responses_input_item_shape(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"type": type(item).__name__}

    shape: dict[str, Any] = {
        "type": item.get("type"),
        "role": item.get("role"),
        "keys": sorted(str(key) for key in item),
    }
    content = item.get("content")
    if isinstance(content, list):
        shape["content_part_types"] = [
            part.get("type") if isinstance(part, dict) else type(part).__name__
            for part in content
        ]
    elif content is not None:
        shape["content_type"] = type(content).__name__
    if "output" in item:
        shape["output_type"] = type(item.get("output")).__name__
    return {key: value for key, value in shape.items() if value is not None}


def _responses_cache_boundary(input_value: list[Any]) -> tuple[int, str | None]:
    for index, item in enumerate(input_value):
        if isinstance(item, dict) and item.get("type") in RESPONSES_OUTPUT_ITEM_TYPES:
            return index, str(item.get("type"))
    return len(input_value), None


def _responses_input_item_type_counts(input_value: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in input_value:
        item_type = item.get("type") if isinstance(item, dict) else type(item).__name__
        key = str(item_type or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _responses_cache_field_presence(data: dict[str, Any]) -> dict[str, bool]:
    fields = (
        "client_metadata",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt_cache_key",
        "prompt_cache_retention",
        "service_tier",
        "text",
        "truncation",
    )
    return {field: field in data for field in fields}


def responses_cache_hot_zone_fingerprint(data: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted fingerprint for the provider-cache-sensitive prefix."""

    stable_top_level = {
        key: data[key]
        for key in sorted(RESPONSES_CACHE_STABLE_TOP_LEVEL_KEYS)
        if key in data
    }
    input_value = data.get("input")
    boundary_index: int | None = None
    boundary_type: str | None = None
    stable_input_prefix: list[Any] = []
    input_item_count: int | None = None
    input_item_type_counts: dict[str, int] = {}
    output_item_count = 0
    last_input_item_type: str | None = None

    if isinstance(input_value, list):
        input_item_count = len(input_value)
        boundary_index, boundary_type = _responses_cache_boundary(input_value)
        stable_input_prefix = input_value[:boundary_index]
        input_item_type_counts = _responses_input_item_type_counts(input_value)
        output_item_count = sum(
            1
            for item in input_value
            if isinstance(item, dict)
            and item.get("type") in RESPONSES_OUTPUT_ITEM_TYPES
        )
        if input_value:
            last_item = input_value[-1]
            last_input_item_type = (
                str(last_item.get("type") or "unknown")
                if isinstance(last_item, dict)
                else type(last_item).__name__
            )

    diagnostic_stable_top_level = {
        key: value
        for key, value in stable_top_level.items()
        if key not in RESPONSES_CACHE_DIAGNOSTIC_VOLATILE_TOP_LEVEL_KEYS
    }
    stable_prefix = {
        "top_level": stable_top_level,
        "input_prefix": stable_input_prefix,
    }
    stable_prefix_without_prompt_cache_key = {
        "top_level": diagnostic_stable_top_level,
        "input_prefix": stable_input_prefix,
    }
    ignored_top_level_keys = sorted(
        str(key)
        for key in data
        if key not in RESPONSES_CACHE_STABLE_TOP_LEVEL_KEYS and key != "input"
    )
    volatile_top_level_keys = sorted(
        str(key) for key in data if key in RESPONSES_CACHE_IGNORED_TOP_LEVEL_KEYS
    )

    return {
        "version": 1,
        "stable_prefix_hash": _stable_hash(stable_prefix),
        "stable_prefix_without_prompt_cache_key_hash": _stable_hash(
            stable_prefix_without_prompt_cache_key
        ),
        "stable_prefix_bytes": _json_byte_len(stable_prefix),
        "stable_top_level_hash": _stable_hash(stable_top_level),
        "stable_top_level_keys": sorted(str(key) for key in stable_top_level),
        "stable_top_level_field_hashes": {
            str(key): _stable_hash(value) for key, value in stable_top_level.items()
        },
        "stable_input_prefix_hash": _stable_hash(stable_input_prefix),
        "stable_input_item_hashes": [
            _stable_hash(item) for item in stable_input_prefix
        ],
        "ignored_top_level_keys": ignored_top_level_keys,
        "volatile_top_level_keys": volatile_top_level_keys,
        "input_type": type(input_value).__name__,
        "input_item_count": input_item_count,
        "stable_input_item_count": len(stable_input_prefix),
        "stable_input_item_shapes": [
            _responses_input_item_shape(item) for item in stable_input_prefix
        ],
        "top_level_field_presence": _responses_cache_field_presence(data),
        "continuation": {
            "previous_response_id_present": "previous_response_id" in data,
            "previous_response_id_hash": _stable_hash(data["previous_response_id"])
            if "previous_response_id" in data
            else None,
            "truncation_present": "truncation" in data,
            "input_item_type_counts": input_item_type_counts,
            "output_item_count": output_item_count,
            "last_input_item_type": last_input_item_type,
        },
        "mutable_boundary": {
            "input_index": boundary_index,
            "item_type": boundary_type,
        },
    }


def responses_mutable_output_fingerprint(
    data: dict[str, Any], model: str
) -> dict[str, Any]:
    """Return a redacted fingerprint for mutable Responses output payloads."""

    input_value = data.get("input")
    if not isinstance(input_value, list):
        return {
            "version": 1,
            "input_type": type(input_value).__name__,
            "output_item_count": 0,
            "text_output_item_count": 0,
            "output_bytes": 0,
            "output_tokens_estimate": 0,
            "output_hash": _stable_hash([]),
            "output_item_types": [],
        }

    outputs: list[dict[str, Any]] = []
    output_item_types: list[str] = []
    for item in input_value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in RESPONSES_OUTPUT_ITEM_TYPES:
            continue
        output_item_types.append(str(item_type))
        output = item.get("output")
        if isinstance(output, str):
            outputs.append(
                {
                    "item_type": str(item_type),
                    "bytes": len(output.encode("utf-8", errors="replace")),
                    "tokens_estimate": _count_text_tokens(output, model),
                    "hash": _stable_hash(output),
                }
            )

    return {
        "version": 1,
        "input_type": type(input_value).__name__,
        "output_item_count": len(output_item_types),
        "text_output_item_count": len(outputs),
        "output_bytes": sum(int(item["bytes"]) for item in outputs),
        "output_tokens_estimate": sum(int(item["tokens_estimate"]) for item in outputs),
        "output_hash": _stable_hash(outputs),
        "output_item_types": output_item_types,
    }


def responses_deployment_payload_fingerprint(data: dict[str, Any]) -> dict[str, Any]:
    """Return content-free shape evidence for the final LiteLLM deployment kwargs."""

    model = str(data.get("model") or DEFAULT_MODEL)
    effective_data = dict(data)
    extra_body = data.get("extra_body")
    if isinstance(extra_body, dict):
        effective_data.update(extra_body)
    effective_model = str(effective_data.get("model") or model)
    return {
        "version": 2,
        "hook": "async_pre_call_deployment_hook",
        "model": model,
        "effective_model": effective_model,
        "data_keys": sorted(str(key) for key in data),
        "effective_data_keys": sorted(str(key) for key in effective_data),
        "cache_hot_zone": responses_cache_hot_zone_fingerprint(effective_data),
        "mutable_output": responses_mutable_output_fingerprint(
            effective_data, effective_model
        ),
    }


def redacted_litellm_payload_shape(
    data: dict[str, Any], call_type: str, response: Any | None = None
) -> dict[str, Any]:
    """Return a content-free shape summary for LiteLLM callback diagnostics."""

    input_value = data.get("input")
    input_shape: dict[str, Any] = _field_shape(input_value)
    if isinstance(input_value, list):
        items: list[dict[str, Any]] = []
        for item in input_value:
            if not isinstance(item, dict):
                items.append({"type": type(item).__name__})
                continue
            item_shape: dict[str, Any] = {
                "type": item.get("type"),
                "role": item.get("role"),
                "keys": sorted(str(key) for key in item),
            }
            content = item.get("content")
            if isinstance(content, list):
                item_shape["content_part_types"] = [
                    part.get("type") if isinstance(part, dict) else type(part).__name__
                    for part in content
                ]
            elif content is not None:
                item_shape["content_type"] = type(content).__name__
            if "output" in item:
                item_shape["output_type"] = type(item.get("output")).__name__
            items.append(item_shape)
        input_shape["items"] = items

    response_shape: dict[str, Any] | None = None
    if response is not None:
        usage = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        response_shape = {
            "type": type(response).__name__,
            "id_present": bool(response_id(response)),
            "usage": _field_shape(usage),
        }

    metadata = _metadata(data)
    return {
        "hook": "async_pre_call_hook",
        "call_type": call_type,
        "data_keys": sorted(str(key) for key in data),
        "data_types": {str(key): type(value).__name__ for key, value in data.items()},
        "model": data.get("model") if isinstance(data.get("model"), str) else None,
        "metadata_keys": sorted(str(key) for key in metadata),
        "cache_hot_zone": responses_cache_hot_zone_fingerprint(data),
        "input": input_shape,
        "messages": _field_shape(data.get("messages")),
        "response": response_shape,
    }


def _buffer_config_from_env() -> AsyncIngestionBufferConfig:
    return AsyncIngestionBufferConfig(
        max_queue_size=int(os.getenv("HEADROOM_ANALYTICS_BUFFER_SIZE", "1000")),
        worker_count=int(os.getenv("HEADROOM_ANALYTICS_BUFFER_WORKERS", "2")),
        max_attempts=int(os.getenv("HEADROOM_ANALYTICS_MAX_ATTEMPTS", "3")),
        retry_base_seconds=float(
            os.getenv("HEADROOM_ANALYTICS_RETRY_BASE_SECONDS", "0.1")
        ),
        retry_max_seconds=float(
            os.getenv("HEADROOM_ANALYTICS_RETRY_MAX_SECONDS", "1.0")
        ),
        shutdown_timeout_seconds=float(
            os.getenv("HEADROOM_ANALYTICS_SHUTDOWN_TIMEOUT_SECONDS", "2.0")
        ),
    )


class HeadroomAnalyticsCallback(_HeadroomLiteLLMCallback, CustomLogger):
    """Headroom LiteLLM callback that also posts analytics to the backend."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _HeadroomLiteLLMCallback.__init__(self, *args, **kwargs)
        CustomLogger.__init__(self, turn_off_message_logging=True)
        config = AnalyticsHttpClientConfig.from_env()
        self._analytics_client = AnalyticsHttpClient(config) if config else None
        self._analytics_buffer = (
            AsyncIngestionBuffer(self._analytics_client, _buffer_config_from_env())
            if self._analytics_client is not None
            else None
        )
        self._pending: dict[str, CompressionCapture] = {}
        self._max_pending = int(os.getenv("HEADROOM_ANALYTICS_PENDING_LIMIT", "1000"))
        self._last_compress_result: dict[str, Any] | None = None
        self._savings_profile = _active_savings_profile()
        self._deployment_payload_shapes: dict[str, dict[str, Any]] = {}

    def _compress_messages(
        self, messages: list[dict[str, Any]], model: str
    ) -> dict[str, Any] | None:
        hooks = AnalyticsCompressionHooks(delegate=self._hooks)
        result = compress(
            messages=messages,
            model=model or DEFAULT_MODEL,
            model_limit=self._model_limit,
            hooks=hooks,
            config=CompressConfig(savings_profile=self._savings_profile),
        )
        observation = hooks.consume_last_observation()
        transforms_applied = (
            list(observation.transforms_applied)
            if observation is not None
            else result.transforms_applied
        )
        payload = {
            "messages": result.messages,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "tokens_saved": result.tokens_saved,
            "compression_ratio": result.compression_ratio,
            "transforms_applied": transforms_applied,
            "ccr_hashes": list(observation.ccr_hashes)
            if observation is not None
            else [],
            "savings_profile": self._savings_profile,
        }
        self._last_compress_result = payload
        return payload

    def _local_compress(
        self, messages: list[dict[str, Any]], model: str
    ) -> dict[str, Any] | None:
        return self._compress_messages(messages, model)

    def _local_compress_responses(
        self, data: dict[str, Any], model: str
    ) -> dict[str, Any] | None:
        try:
            min_bytes = max(
                int(
                    os.getenv(
                        "HEADROOM_RESPONSES_MIN_MUTABLE_BYTES",
                        str(RESPONSES_MIN_MUTABLE_BYTES),
                    )
                ),
                0,
            )
        except ValueError:
            min_bytes = RESPONSES_MIN_MUTABLE_BYTES
        transforms_applied: list[Any] = []
        if _apply_codex_chatgpt_session_affinity(data, model):
            transforms_applied.append("openai:responses:chatgpt_session_affinity")
        if _guard_codex_prompt_cache_key(data):
            transforms_applied.append("openai:responses:prompt_cache_key_removed")
        preserved_headers = _preserve_codex_responses_headers(data)
        if preserved_headers:
            transforms_applied.append("openai:responses:codex_header_passthrough")
        preserved_passthrough = (
            _preserve_responses_provider_passthrough_fields(data)
            if _should_preserve_responses_provider_passthrough(data)
            else []
        )
        if preserved_passthrough:
            transforms_applied.append("openai:responses:chatgpt_provider_passthrough")
        if "prompt_cache_key" in preserved_passthrough:
            transforms_applied.append("openai:responses:prompt_cache_key_passthrough")
        if _compression_disabled_by_proxy_header(data):
            return _compression_disabled_result(
                transforms_applied, savings_profile=self._savings_profile
            )
        tool_compaction = (
            _compact_responses_tools(data, model)
            if _env_flag_enabled(RESPONSES_TOOL_SCHEMA_COMPACTION_ENV)
            else None
        )
        if tool_compaction is not None:
            transforms_applied.append("openai:responses:tool_schema_compaction")
        slots, skip_reason = _responses_mutable_output_slots(data, min_bytes=min_bytes)
        if skip_reason is not None:
            if tool_compaction is not None:
                return {
                    "messages": None,
                    "tokens_before": tool_compaction.tools_before_tokens,
                    "tokens_after": tool_compaction.tools_after_tokens,
                    "tokens_saved": tool_compaction.tokens_saved,
                    "compression_ratio": tool_compaction.tokens_saved
                    / tool_compaction.tools_before_tokens
                    if tool_compaction.tools_before_tokens > 0
                    else 0.0,
                    "transforms_applied": list(dict.fromkeys(transforms_applied)),
                    "savings_profile": self._savings_profile,
                    "skip_reason": None,
                    "attempted_input_tokens": tool_compaction.tools_before_tokens,
                    "responses_units_attempted": 0,
                    "responses_units_modified": 0,
                    "tool_schema_bytes_before": tool_compaction.tools_before_bytes,
                    "tool_schema_bytes_after": tool_compaction.tools_after_bytes,
                }
            return {
                "messages": None,
                "tokens_before": None,
                "tokens_after": None,
                "tokens_saved": None,
                "compression_ratio": None,
                "transforms_applied": list(dict.fromkeys(transforms_applied)),
                "savings_profile": self._savings_profile,
                "skip_reason": skip_reason,
                "attempted_input_tokens": 0,
                "responses_units_attempted": 0,
                "responses_units_modified": 0,
            }
        if not _env_flag_enabled(RESPONSES_MUTABLE_OUTPUT_COMPRESSION_ENV):
            if tool_compaction is not None:
                return {
                    "messages": None,
                    "tokens_before": tool_compaction.tools_before_tokens,
                    "tokens_after": tool_compaction.tools_after_tokens,
                    "tokens_saved": tool_compaction.tokens_saved,
                    "compression_ratio": tool_compaction.tokens_saved
                    / tool_compaction.tools_before_tokens
                    if tool_compaction.tools_before_tokens > 0
                    else 0.0,
                    "transforms_applied": list(dict.fromkeys(transforms_applied)),
                    "savings_profile": self._savings_profile,
                    "skip_reason": None,
                    "attempted_input_tokens": tool_compaction.tools_before_tokens,
                    "responses_units_attempted": 0,
                    "responses_units_modified": 0,
                    "tool_schema_bytes_before": tool_compaction.tools_before_bytes,
                    "tool_schema_bytes_after": tool_compaction.tools_after_bytes,
                }
            return {
                "messages": None,
                "tokens_before": None,
                "tokens_after": None,
                "tokens_saved": None,
                "compression_ratio": None,
                "transforms_applied": list(dict.fromkeys(transforms_applied)),
                "savings_profile": self._savings_profile,
                "skip_reason": (
                    "responses_mutable_output_compression_disabled_no_positive_"
                    "provider_proof"
                ),
                "attempted_input_tokens": 0,
                "responses_units_attempted": 0,
                "responses_units_modified": 0,
            }

        replacements: list[tuple[int, str]] = []
        tokens_before = tool_compaction.tools_before_tokens if tool_compaction else 0
        tokens_after = tool_compaction.tools_after_tokens if tool_compaction else 0
        tokens_saved = tool_compaction.tokens_saved if tool_compaction else 0
        attempted_units = 0

        for slot in slots:
            attempted_units += 1
            result = self._compress_messages(
                [{"role": "tool", "content": slot.text}],
                model,
            )
            if result is None:
                continue
            before = int(result.get("tokens_before") or 0)
            after = int(result.get("tokens_after") or before)
            saved = int(result.get("tokens_saved") or 0)
            tokens_before += before

            messages = result.get("messages")
            replacement = None
            if isinstance(messages, list) and messages:
                first = messages[0]
                if isinstance(first, dict):
                    replacement = _message_text(first)

            if (
                saved <= 0
                or replacement is None
                or replacement == slot.text
                or len(replacement.encode("utf-8", errors="replace"))
                >= len(slot.text.encode("utf-8", errors="replace"))
            ):
                tokens_after += before
                continue

            tokens_saved += saved
            tokens_after += after
            replacements.append((slot.item_index, replacement))
            for transform in result.get("transforms_applied") or []:
                if transform not in transforms_applied:
                    transforms_applied.append(transform)

        if not replacements:
            if tool_compaction is not None:
                return {
                    "messages": None,
                    "tokens_before": tokens_before,
                    "tokens_after": tokens_after,
                    "tokens_saved": tokens_saved,
                    "compression_ratio": tokens_saved / tokens_before
                    if tokens_before > 0
                    else 0.0,
                    "transforms_applied": list(dict.fromkeys(transforms_applied)),
                    "savings_profile": self._savings_profile,
                    "skip_reason": None,
                    "attempted_input_tokens": tokens_before,
                    "responses_units_attempted": attempted_units,
                    "responses_units_modified": 0,
                    "tool_schema_bytes_before": tool_compaction.tools_before_bytes,
                    "tool_schema_bytes_after": tool_compaction.tools_after_bytes,
                }
            return {
                "messages": None,
                "tokens_before": tokens_before or None,
                "tokens_after": tokens_before or None,
                "tokens_saved": 0 if tokens_before else None,
                "compression_ratio": 0.0 if tokens_before else None,
                "transforms_applied": list(dict.fromkeys(transforms_applied)),
                "savings_profile": self._savings_profile,
                "skip_reason": "responses_no_smaller_output",
                "attempted_input_tokens": tokens_before,
                "responses_units_attempted": attempted_units,
                "responses_units_modified": 0,
            }

        input_value = data.get("input")
        if not isinstance(input_value, list):
            return None
        for item_index, replacement in replacements:
            item = input_value[item_index]
            if isinstance(item, dict):
                item["output"] = replacement

        compression_ratio = tokens_saved / tokens_before if tokens_before > 0 else 0.0
        output_transforms = [
            *transforms_applied,
            "openai:responses:tool_output_units",
        ]
        return {
            "messages": None,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "tokens_saved": tokens_saved,
            "compression_ratio": compression_ratio,
            "transforms_applied": list(dict.fromkeys(output_transforms)),
            "savings_profile": self._savings_profile,
            "skip_reason": None,
            "attempted_input_tokens": tokens_before,
            "responses_units_attempted": attempted_units,
            "responses_units_modified": len(replacements),
            "tool_schema_bytes_before": tool_compaction.tools_before_bytes
            if tool_compaction
            else None,
            "tool_schema_bytes_after": tool_compaction.tools_after_bytes
            if tool_compaction
            else None,
        }

    async def async_pre_call_hook(
        self,
        user_api_key: Any = None,
        data: dict[str, Any] | None = None,
        call_type: str = "",
        *,
        user_api_key_dict: Any = None,
        cache: Any = None,
        **_: Any,
    ) -> dict[str, Any] | None:
        del cache
        if data is None:
            return data
        if call_type not in ("completion", "acompletion", "responses", "aresponses"):
            return data

        model = str(data.get("model") or DEFAULT_MODEL)

        request_key = _ensure_request_key(data)
        if _env_flag_enabled(DEBUG_PAYLOAD_SHAPES_ENV):
            logger.warning(
                "litellm_callback_payload_shape=%s",
                json.dumps(_payload_debug_shape(data, call_type), sort_keys=True),
            )
        _move_wrapper_system_to_user_message(data)
        messages = data.get("messages", [])

        if not messages:
            if data.get("input") is not None:
                self._last_compress_result = None
                headroom_result = self._local_compress_responses(data, model)
                compression = self._compression_capture(
                    request_key, model, data, headroom_result=headroom_result
                )
                if compression is not None:
                    self._remember(compression)
            return data

        if _compression_disabled_by_proxy_header(data):
            compression = self._compression_capture(
                request_key,
                model,
                data,
                _compression_disabled_result([], savings_profile=self._savings_profile),
            )
            if compression is not None:
                self._remember(compression)
            return data

        self._last_compress_result = None
        result = await _HeadroomLiteLLMCallback.async_pre_call_hook(
            self,
            user_api_key=_user_api_key_value(user_api_key, user_api_key_dict),
            data=data,
            call_type=call_type,
        )

        if result is data:
            compression = self._compression_capture(
                request_key, model, data, self._last_compress_result
            )
            self._last_compress_result = None
            if compression is not None:
                self._remember(compression)
        return result

    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any
    ) -> dict[str, Any]:
        call_type_value = getattr(call_type, "value", call_type)
        if call_type_value in ("responses", "aresponses") or (
            kwargs.get("input") is not None and kwargs.get("messages") is None
        ):
            request_key = _metadata(kwargs).get("litellm_proxy_analytics_request_key")
            if request_key:
                if len(self._deployment_payload_shapes) >= self._max_pending:
                    oldest_key = next(iter(self._deployment_payload_shapes))
                    self._deployment_payload_shapes.pop(oldest_key, None)
                self._deployment_payload_shapes[str(request_key)] = (
                    responses_deployment_payload_fingerprint(kwargs)
                )
        return kwargs

    async def async_success_handler(
        self,
        kwargs: dict[str, Any],
        response: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        await _HeadroomLiteLLMCallback.async_success_handler(
            self, kwargs, response, start_time, end_time
        )
        if _is_proxy_logging_payload(
            kwargs
        ) and not _is_final_stream_success_logging_payload(kwargs):
            self._enrich_pending_capture(kwargs)
            return
        capture = self._pop_capture(kwargs)
        if capture is None:
            capture = self._post_call_capture(kwargs)
        if capture is None:
            return
        capture = self._capture_with_current_request_context(capture, kwargs)
        await self._post_capture(
            capture,
            response=response,
            status="succeeded",
            duration_ms=_duration_ms(start_time, end_time),
        )

    async def async_failure_handler(
        self,
        kwargs: dict[str, Any],
        response: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        await _HeadroomLiteLLMCallback.async_failure_handler(
            self, kwargs, response, start_time, end_time
        )
        if _is_proxy_logging_payload(kwargs):
            self._enrich_pending_capture(kwargs)
            return
        capture = self._pop_capture(kwargs)
        if capture is None:
            capture = self._post_call_capture(kwargs)
        if capture is None:
            return
        capture = self._capture_with_current_request_context(capture, kwargs)
        await self._post_capture(
            capture,
            response=response,
            status="failed",
            duration_ms=_duration_ms(start_time, end_time),
            error_message=str(response) if response is not None else None,
        )

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: Any,
        response: Any,
    ) -> None:
        del user_api_key_dict
        capture = self._pop_capture(data)
        if capture is None:
            capture = self._post_call_capture(data)
        if capture is None:
            return None
        capture = self._capture_with_current_request_context(capture, data)
        await self._post_capture(
            capture,
            response=response,
            status="succeeded",
            duration_ms=None,
        )
        return None

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> None:
        del user_api_key_dict, traceback_str
        capture = self._pop_capture(request_data)
        if capture is None:
            capture = self._post_call_capture(request_data)
        if capture is None:
            return None
        capture = self._capture_with_current_request_context(capture, request_data)
        await self._post_capture(
            capture,
            response=None,
            status="failed",
            duration_ms=None,
            error_message=str(original_exception),
        )
        return None

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        await self.async_success_handler(
            kwargs=kwargs,
            response=response_obj,
            start_time=start_time,
            end_time=end_time,
        )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        await self.async_failure_handler(
            kwargs=kwargs,
            response=response_obj,
            start_time=start_time,
            end_time=end_time,
        )

    async def async_logging_hook(
        self, kwargs: dict[str, Any], result: Any, call_type: str
    ) -> tuple[dict[str, Any], Any]:
        del call_type
        return kwargs, result

    def _capture_with_current_request_context(
        self,
        capture: CompressionCapture,
        data: dict[str, Any],
    ) -> CompressionCapture:
        request_metadata = dict(capture.request_metadata)
        request_metadata.update(
            _request_metadata_from_data(data, self._savings_profile)
        )
        trace = trace_context_from_litellm_payload(data)
        if not (
            trace.traceparent or trace.trace_id or trace.span_id or trace.tracestate
        ):
            trace = capture.trace
        return replace(
            capture,
            litellm_call_id=_litellm_call_id_from_data(data) or capture.litellm_call_id,
            incoming_route=_incoming_route_from_data(data) or capture.incoming_route,
            request_metadata=request_metadata,
            trace=trace,
        )

    def _enrich_pending_capture(self, data: dict[str, Any]) -> None:
        pending_key: str | None = None
        request_key = _metadata(data).get("litellm_proxy_analytics_request_key")
        if request_key and str(request_key) in self._pending:
            pending_key = str(request_key)
        elif len(self._pending) == 1:
            pending_key = next(iter(self._pending))
        if pending_key is None:
            return
        self._pending[pending_key] = self._capture_with_current_request_context(
            self._pending[pending_key],
            data,
        )

    def _compression_capture(
        self,
        request_key: str,
        model: str,
        data: dict[str, Any],
        headroom_result: dict[str, Any] | None,
    ) -> CompressionCapture | None:
        payload = data.get("messages")
        if payload is None:
            payload = data.get("input")
        if payload is None:
            return None
        content_hash = _stable_hash(payload)
        ccr_hash = f"litellm:{content_hash[:48]}"
        tokens_after = None
        tokens_before = None
        tokens_saved = None
        compression_ratio = None
        transforms_applied: list[Any] = []
        compression_status: str | None = None
        skip_reason: str | None = None
        attempted_input_tokens: int | None = None

        if headroom_result is not None:
            tokens_before = headroom_result.get("tokens_before")
            tokens_after = headroom_result.get("tokens_after")
            tokens_saved = headroom_result.get("tokens_saved")
            compression_ratio = headroom_result.get("compression_ratio")
            transforms_applied = list(headroom_result.get("transforms_applied") or [])
            skip_reason = headroom_result.get("skip_reason")
            attempted_input_tokens = headroom_result.get("attempted_input_tokens")
            if skip_reason:
                compression_status = "skipped"
        raw_ccr_hashes = (
            headroom_result.get("ccr_hashes")
            if isinstance(headroom_result, dict)
            else None
        )
        if not isinstance(raw_ccr_hashes, (list, tuple, set, frozenset)):
            raw_ccr_hashes = []

        return CompressionCapture(
            request_key=request_key,
            event_key=f"{request_key}:compression:{int(time.time() * 1000)}",
            litellm_call_id=_litellm_call_id_from_data(data),
            model=model,
            incoming_route=_incoming_route_from_data(data),
            request_metadata=_request_metadata_from_data(data, self._savings_profile),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            transforms_applied=transforms_applied,
            compression_status=compression_status,
            skip_reason=skip_reason,
            attempted_input_tokens=attempted_input_tokens,
            started_at_ms=int(time.time() * 1000),
            ccr_hash=ccr_hash,
            ccr_hashes=tuple(str(value) for value in raw_ccr_hashes if value),
            content_hash=content_hash,
            cache_hot_zone=responses_cache_hot_zone_fingerprint(data)
            if data.get("input") is not None and data.get("messages") is None
            else None,
            trace=trace_context_from_litellm_payload(data),
        )

    def _post_call_capture(self, data: dict[str, Any]) -> CompressionCapture | None:
        request_key = _ensure_request_key(data)
        model = str(data.get("model") or DEFAULT_MODEL)
        return self._compression_capture(
            request_key=request_key,
            model=model,
            data=data,
            headroom_result={
                "tokens_before": None,
                "tokens_after": None,
                "tokens_saved": None,
                "compression_ratio": None,
                "transforms_applied": [],
                "skip_reason": "compression_not_attempted_post_call_fallback",
                "attempted_input_tokens": 0,
            },
        )

    def _remember(self, capture: CompressionCapture) -> None:
        if len(self._pending) >= self._max_pending:
            oldest_key = next(iter(self._pending))
            self._pending.pop(oldest_key, None)
        self._pending[capture.request_key] = capture

    def _pop_capture(self, kwargs: dict[str, Any]) -> CompressionCapture | None:
        request_key = _metadata(kwargs).get("litellm_proxy_analytics_request_key")
        if request_key:
            capture = self._pending.pop(str(request_key), None)
            if capture is not None:
                return capture
        if len(self._pending) == 1:
            _, capture = self._pending.popitem()
            return capture
        return None

    async def _post_capture(
        self,
        capture: CompressionCapture,
        *,
        response: Any,
        status: str,
        duration_ms: int | None,
        error_message: str | None = None,
    ) -> None:
        buffer = self._analytics_buffer
        if buffer is None:
            return

        raw_response_key = response_id(response)
        response_key = _bounded_identifier(raw_response_key, "response")
        incoming_route = _incoming_route_from_response(
            capture.incoming_route, response, response_key
        )
        provider_call = self._provider_call(capture, response, status, duration_ms)
        idempotency_key = response_key or capture.content_hash
        execution_status = capture.compression_status or status
        transforms: dict[str, Any] = {"applied": capture.transforms_applied}
        if capture.ccr_hashes:
            transforms["ccr_hashes"] = list(capture.ccr_hashes)
        if capture.skip_reason:
            transforms["skip_reason"] = capture.skip_reason
        if capture.attempted_input_tokens is not None:
            transforms["attempted_input_tokens"] = capture.attempted_input_tokens
        if capture.cache_hot_zone is not None:
            transforms["cache_hot_zone"] = capture.cache_hot_zone
        deployment_payload = self._deployment_payload_shapes.pop(
            capture.request_key, None
        )
        if deployment_payload is not None:
            transforms["deployment_payload"] = deployment_payload
        savings_profile = str(
            capture.request_metadata.get("savings_profile") or DEFAULT_SAVINGS_PROFILE
        )
        command = CompressionActivityIngestCommand(
            event=IngestionEventCommand(
                source="litellm-headroom-callback",
                event_type="compression_result",
                event_key=_event_key(capture.request_key, idempotency_key, status),
                raw_payload={
                    "model": capture.model,
                    "provider_status": status,
                    "compression_status": execution_status,
                    "skip_reason": capture.skip_reason,
                    "response_id": response_key,
                },
                trace=capture.trace,
            ),
            request=CompressionRequestCommand(
                request_key=capture.request_key,
                source_system="litellm-proxy",
                incoming_route=incoming_route,
                model_hint=capture.model,
                metadata=capture.request_metadata,
                trace=capture.trace,
            ),
            config=CompressionConfigCommand(
                config_hash=_stable_hash(
                    {
                        "strategy": savings_profile,
                        "model": capture.model,
                    }
                ),
                strategy_name=savings_profile,
                strategy_version="1",
                target_model=capture.model,
                raw_config={"savings_profile": savings_profile},
            ),
            execution=CompressionExecutionCommand(
                attempt_number=1,
                status=execution_status,
                original_tokens=capture.tokens_before,
                compressed_tokens=capture.tokens_after,
                tokens_saved=capture.tokens_saved,
                compression_ratio=capture.compression_ratio,
                duration_ms=duration_ms,
                transforms=transforms,
                error_message=error_message,
                trace=capture.trace,
            ),
            chunks=[
                CompressionChunkCommand(
                    ordinal=0,
                    ccr_hash=capture.ccr_hash,
                    content_hash=capture.content_hash,
                    compressed_tokens=capture.tokens_after,
                    storage_policy="hash_only",
                    metadata={
                        "source": "litellm-headroom-callback",
                        "skip_reason": capture.skip_reason,
                        "attempted_input_tokens": capture.attempted_input_tokens,
                    },
                )
            ],
            provider_calls=[provider_call] if provider_call is not None else [],
        )
        get_analytics_telemetry().record_compression_story(
            command,
            success=status == "succeeded",
            latency_ms=duration_ms,
        )
        if not buffer.submit_nowait(command):
            logger.debug("analytics buffer rejected %s", capture.request_key)

    def _provider_call(
        self,
        capture: CompressionCapture,
        response: Any,
        status: str,
        duration_ms: int | None,
    ) -> ProviderCallCommand | None:
        usage = token_usage_from_response(response)
        response_key = _bounded_identifier(response_id(response), "response")
        cost_total = response_cost(response)
        provider_call_key = response_key or _bounded_identifier(
            f"{capture.request_key}:provider", "provider"
        )
        provider = capture.model.split("/", 1)[0] if "/" in capture.model else "unknown"
        return ProviderCallCommand(
            provider_call_key=provider_call_key,
            execution_attempt=1,
            provider=provider,
            model=capture.model,
            litellm_call_id=capture.litellm_call_id,
            provider_response_id=response_key,
            status=status,
            duration_ms=duration_ms,
            cost_total=cost_total,
            currency="USD" if cost_total is not None else None,
            raw_response_metadata=provider_response_metadata(response),
            trace=capture.trace,
            token_usage=[usage] if usage is not None else [],
        )

    async def flush_analytics(self, timeout_seconds: float | None = None) -> bool:
        buffer = self._analytics_buffer
        if buffer is None:
            return True
        return await buffer.flush(timeout_seconds)

    async def aclose(self) -> None:
        buffer = self._analytics_buffer
        if buffer is not None:
            await buffer.aclose()

    def analytics_buffer_stats(self) -> dict[str, Any]:
        buffer = self._analytics_buffer
        if buffer is None:
            return {
                "enabled": False,
                "submitted": 0,
                "delivered": 0,
                "failed": 0,
                "dropped_full": 0,
                "retried": 0,
                "max_depth": 0,
                "current_depth": 0,
                "closed": False,
            }
        snapshot = buffer.snapshot()
        get_analytics_telemetry().record_buffer_snapshot(snapshot)
        return {
            "enabled": True,
            "submitted": snapshot.submitted,
            "delivered": snapshot.delivered,
            "failed": snapshot.failed,
            "dropped_full": snapshot.dropped_full,
            "retried": snapshot.retried,
            "max_depth": snapshot.max_depth,
            "current_depth": snapshot.current_depth,
            "closed": snapshot.closed,
        }
