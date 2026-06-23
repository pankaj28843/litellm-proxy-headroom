from __future__ import annotations

from contextlib import nullcontext
from functools import lru_cache
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

from ...application.buffering import AsyncIngestionBufferSnapshot
from ...application.commands import CompressionActivityIngestCommand

LOW_CARD_UNKNOWN = "unknown"


def _attrs(
    **values: str | int | float | bool | None,
) -> dict[str, str | int | float | bool]:
    return {key: value for key, value in values.items() if value is not None}


def _token_values(
    command: CompressionActivityIngestCommand,
) -> list[tuple[str, int, str]]:
    values: list[tuple[str, int, str]] = []
    for provider_call in command.provider_calls:
        provider = provider_call.provider or LOW_CARD_UNKNOWN
        for usage in provider_call.token_usage:
            fields = {
                "input": usage.input_tokens,
                "input.cached": usage.cached_input_tokens,
                "input.new": usage.newly_processed_input_tokens,
                "input.cache_write": usage.cache_write_tokens,
                "output": usage.output_tokens,
                "output.reasoning": usage.reasoning_tokens,
                "total": usage.total_tokens,
            }
            for token_type, count in fields.items():
                if count is not None:
                    values.append((provider, int(count), token_type))
    return values


class AnalyticsTelemetry:
    def __init__(self) -> None:
        meter = metrics.get_meter("litellm_proxy.analytics")
        self._tracer = trace.get_tracer("litellm_proxy.analytics")
        self._compression_duration = meter.create_histogram(
            "litellm.proxy.analytics.compression.duration",
            unit="ms",
            description="Compression execution duration.",
        )
        self._compression_original_tokens = meter.create_histogram(
            "litellm.proxy.analytics.compression.original_tokens",
            unit="{token}",
            description="Original tokens before compression.",
        )
        self._compression_compressed_tokens = meter.create_histogram(
            "litellm.proxy.analytics.compression.compressed_tokens",
            unit="{token}",
            description="Compressed tokens after compression.",
        )
        self._compression_tokens_saved = meter.create_histogram(
            "litellm.proxy.analytics.compression.tokens_saved",
            unit="{token}",
            description="Tokens saved by compression.",
        )
        self._compression_ratio = meter.create_histogram(
            "litellm.proxy.analytics.compression.ratio",
            unit="1",
            description="Compression ratio per execution.",
        )
        self._compression_failures = meter.create_counter(
            "litellm.proxy.analytics.compression.failures",
            unit="{failure}",
            description="Compression failures observed by analytics.",
        )
        self._provider_tokens = meter.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Provider-reported GenAI token usage.",
        )
        self._cache_operations = meter.create_counter(
            "litellm.proxy.analytics.cache.operations",
            unit="{operation}",
            description="Cache read/write activity observed by analytics.",
        )
        self._cache_tokens = meter.create_histogram(
            "litellm.proxy.analytics.cache.tokens",
            unit="{token}",
            description="Tokens read from or written to caches.",
        )
        self._persistence_latency = meter.create_histogram(
            "litellm.proxy.analytics.persistence.latency",
            unit="ms",
            description="Analytics persistence latency.",
        )
        self._persistence_failures = meter.create_counter(
            "litellm.proxy.analytics.persistence.failures",
            unit="{failure}",
            description="Analytics persistence failures.",
        )
        self._retrieval_latency = meter.create_histogram(
            "litellm.proxy.analytics.retrieval.latency",
            unit="ms",
            description="Compressed chunk retrieval latency.",
        )
        self._retrieval_results = meter.create_counter(
            "litellm.proxy.analytics.retrieval.results",
            unit="{result}",
            description="Compressed chunk retrieval outcomes.",
        )
        self._retrieval_failures = meter.create_counter(
            "litellm.proxy.analytics.retrieval.failures",
            unit="{failure}",
            description="Compressed chunk retrieval failures.",
        )
        self._buffer_depth = meter.create_histogram(
            "litellm.proxy.analytics.buffer.depth",
            unit="{item}",
            description="LiteLLM analytics buffer depth snapshots.",
        )

    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Any:
        try:
            return self._tracer.start_as_current_span(name, attributes=attributes)
        except Exception:
            return nullcontext()

    def mark_span_error(self, exc: Exception) -> None:
        span = trace.get_current_span()
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, exc.__class__.__name__))

    def record_ingest(
        self,
        command: CompressionActivityIngestCommand,
        *,
        latency_ms: int,
        success: bool,
    ) -> None:
        status = "ok" if success else "error"
        persistence_attrs = _attrs(
            **{
                "litellm.proxy.analytics.operation": "ingest",
                "litellm.proxy.analytics.status": status,
            }
        )
        self._persistence_latency.record(latency_ms, persistence_attrs)
        if not success:
            self._persistence_failures.add(1, persistence_attrs)

        execution = command.execution
        compression_attrs = _attrs(
            **{
                "litellm.proxy.analytics.strategy": command.config.strategy_name,
                "litellm.proxy.analytics.status": execution.status,
            }
        )
        if execution.duration_ms is not None:
            self._compression_duration.record(
                int(execution.duration_ms), compression_attrs
            )
        if execution.original_tokens is not None:
            self._compression_original_tokens.record(
                int(execution.original_tokens), compression_attrs
            )
        if execution.compressed_tokens is not None:
            self._compression_compressed_tokens.record(
                int(execution.compressed_tokens), compression_attrs
            )
        if execution.tokens_saved is not None:
            self._compression_tokens_saved.record(
                int(execution.tokens_saved), compression_attrs
            )
        if execution.compression_ratio is not None:
            self._compression_ratio.record(
                float(execution.compression_ratio), compression_attrs
            )
        if execution.status != "succeeded":
            self._compression_failures.add(1, compression_attrs)

        for provider, count, token_type in _token_values(command):
            self._provider_tokens.record(
                count,
                _attrs(
                    **{
                        "gen_ai.operation.name": "chat",
                        "gen_ai.provider.name": provider,
                        "gen_ai.token.type": token_type,
                    }
                ),
            )

        for activity in command.cache_activities:
            cache_attrs = _attrs(
                **{
                    "litellm.proxy.analytics.cache.system": activity.cache_system,
                    "litellm.proxy.analytics.cache.operation": activity.operation,
                    "litellm.proxy.analytics.cache.hit": activity.hit,
                }
            )
            self._cache_operations.add(1, cache_attrs)
            for count in (activity.tokens_read, activity.tokens_written):
                if count is not None:
                    self._cache_tokens.record(int(count), cache_attrs)

    def record_retrieval(
        self,
        *,
        source: str,
        found: bool,
        latency_ms: int,
        operation: str = "retrieve_chunk",
    ) -> None:
        attrs = _attrs(
            **{
                "litellm.proxy.analytics.operation": operation,
                "litellm.proxy.analytics.retrieval.source": source,
                "litellm.proxy.analytics.retrieval.result": "found"
                if found
                else "not_found",
            }
        )
        self._retrieval_latency.record(latency_ms, attrs)
        self._retrieval_results.add(1, attrs)
        if not found:
            self._retrieval_failures.add(1, attrs)

    def record_buffer_snapshot(
        self, snapshot: AsyncIngestionBufferSnapshot | dict[str, Any]
    ) -> None:
        current_depth = (
            snapshot.current_depth
            if isinstance(snapshot, AsyncIngestionBufferSnapshot)
            else snapshot.get("current_depth")
        )
        if current_depth is not None:
            self._buffer_depth.record(
                int(current_depth),
                {"litellm.proxy.analytics.buffer.name": "litellm_callback"},
            )


@lru_cache(maxsize=1)
def get_analytics_telemetry() -> AnalyticsTelemetry:
    return AnalyticsTelemetry()
