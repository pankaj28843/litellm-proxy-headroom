from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from headroom.compress import CompressConfig, compress
from headroom.integrations.litellm_callback import (
    HeadroomCallback as _HeadroomLiteLLMCallback,
)
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

SAVINGS_PROFILE = "agent-90"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


@dataclass(frozen=True, slots=True)
class CompressionCapture:
    request_key: str
    event_key: str
    model: str
    incoming_route: str | None
    tokens_before: int | None
    tokens_after: int | None
    tokens_saved: int | None
    compression_ratio: float | None
    transforms_applied: list[Any]
    started_at_ms: int
    ccr_hash: str
    content_hash: str
    trace: TraceContextCommand


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
        "x-openwebui-message-id",
        "x-openwebui-chat-id",
    ):
        value = metadata.get(key) or data.get(key)
        if value:
            return str(value)
    return f"litellm-{uuid.uuid4()}"


def _ensure_request_key(data: dict[str, Any]) -> str:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        data["metadata"] = metadata

    existing = metadata.get("litellm_proxy_analytics_request_key")
    if existing:
        return str(existing)

    request_key = _request_key_from_data(data)
    metadata["litellm_proxy_analytics_request_key"] = request_key
    return request_key


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

    def _local_compress(
        self, messages: list[dict[str, Any]], model: str
    ) -> dict[str, Any] | None:
        hooks = AnalyticsCompressionHooks(delegate=self._hooks)
        result = compress(
            messages=messages,
            model=model or DEFAULT_MODEL,
            model_limit=self._model_limit,
            hooks=hooks,
            config=CompressConfig(savings_profile=SAVINGS_PROFILE),
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
            "savings_profile": SAVINGS_PROFILE,
        }
        self._last_compress_result = payload
        return payload

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

        messages = data.get("messages", [])
        model = str(data.get("model") or DEFAULT_MODEL)

        request_key = _ensure_request_key(data)

        if not messages:
            if data.get("input") is not None:
                compression = self._compression_capture(
                    request_key, model, data, headroom_result=None
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
        capture = self._pop_capture(kwargs)
        if capture is None:
            capture = self._post_call_capture(kwargs)
        if capture is None:
            return
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
        capture = self._pop_capture(kwargs)
        if capture is None:
            capture = self._post_call_capture(kwargs)
        if capture is None:
            return
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

        if headroom_result is not None:
            tokens_before = headroom_result.get("tokens_before")
            tokens_after = headroom_result.get("tokens_after")
            tokens_saved = headroom_result.get("tokens_saved")
            compression_ratio = headroom_result.get("compression_ratio")
            transforms_applied = list(headroom_result.get("transforms_applied") or [])

        return CompressionCapture(
            request_key=request_key,
            event_key=f"{request_key}:compression:{int(time.time() * 1000)}",
            model=model,
            incoming_route=_incoming_route_from_data(data),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            transforms_applied=transforms_applied,
            started_at_ms=int(time.time() * 1000),
            ccr_hash=ccr_hash,
            content_hash=content_hash,
            trace=trace_context_from_litellm_payload(data),
        )

    def _post_call_capture(self, data: dict[str, Any]) -> CompressionCapture | None:
        request_key = _ensure_request_key(data)
        model = str(data.get("model") or DEFAULT_MODEL)
        return self._compression_capture(
            request_key=request_key,
            model=model,
            data=data,
            headroom_result=None,
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
        command = CompressionActivityIngestCommand(
            event=IngestionEventCommand(
                source="litellm-headroom-callback",
                event_type="compression_result",
                event_key=_event_key(capture.request_key, idempotency_key, status),
                raw_payload={
                    "model": capture.model,
                    "status": status,
                    "response_id": response_key,
                },
                trace=capture.trace,
            ),
            request=CompressionRequestCommand(
                request_key=capture.request_key,
                source_system="litellm-proxy",
                incoming_route=incoming_route,
                model_hint=capture.model,
                trace=capture.trace,
            ),
            config=CompressionConfigCommand(
                config_hash=_stable_hash(
                    {
                        "strategy": SAVINGS_PROFILE,
                        "model": capture.model,
                    }
                ),
                strategy_name=SAVINGS_PROFILE,
                strategy_version="1",
                target_model=capture.model,
                raw_config={"savings_profile": SAVINGS_PROFILE},
            ),
            execution=CompressionExecutionCommand(
                attempt_number=1,
                status=status,
                original_tokens=capture.tokens_before,
                compressed_tokens=capture.tokens_after,
                tokens_saved=capture.tokens_saved,
                compression_ratio=capture.compression_ratio,
                duration_ms=duration_ms,
                transforms={"applied": capture.transforms_applied},
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
                    metadata={"source": "litellm-headroom-callback"},
                )
            ],
            provider_calls=[provider_call] if provider_call is not None else [],
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
