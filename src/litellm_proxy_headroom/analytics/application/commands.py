from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TraceContextCommand(StrictCommand):
    trace_id: str | None = None
    span_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None


class IngestionEventCommand(StrictCommand):
    source: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    event_key: str = Field(min_length=1, max_length=255)
    payload_hash: str | None = Field(default=None, max_length=128)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    trace: TraceContextCommand = Field(default_factory=TraceContextCommand)


class CompressionRequestCommand(StrictCommand):
    request_key: str = Field(min_length=1, max_length=255)
    source_system: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, max_length=128)
    team_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    incoming_route: str | None = Field(default=None, max_length=255)
    provider_hint: str | None = Field(default=None, max_length=128)
    model_hint: str | None = Field(default=None, max_length=255)
    external_request_id: str | None = Field(default=None, max_length=255)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    trace: TraceContextCommand = Field(default_factory=TraceContextCommand)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompressionConfigCommand(StrictCommand):
    config_hash: str = Field(min_length=1, max_length=128)
    strategy_name: str = Field(min_length=1, max_length=128)
    strategy_version: str | None = Field(default=None, max_length=64)
    algorithm: str | None = Field(default=None, max_length=128)
    target_model: str | None = Field(default=None, max_length=255)
    token_budget: int | None = Field(default=None, ge=0)
    trigger_reason: str | None = Field(default=None, max_length=255)
    raw_config: dict[str, Any] = Field(default_factory=dict)


class CompressionExecutionCommand(StrictCommand):
    attempt_number: int = Field(ge=1)
    status: Literal["pending", "succeeded", "failed", "skipped"]
    is_simulated: bool = False
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    original_tokens: int | None = Field(default=None, ge=0)
    compressed_tokens: int | None = Field(default=None, ge=0)
    tokens_saved: int | None = None
    compression_ratio: Decimal | None = None
    transforms: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = None
    trace: TraceContextCommand = Field(default_factory=TraceContextCommand)


class CompressionChunkCommand(StrictCommand):
    ordinal: int = Field(ge=0)
    ccr_hash: str | None = Field(default=None, max_length=255)
    content_hash: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=64)
    tool_name: str | None = Field(default=None, max_length=255)
    original_tokens: int | None = Field(default=None, ge=0)
    compressed_tokens: int | None = Field(default=None, ge=0)
    original_bytes: int | None = Field(default=None, ge=0)
    compressed_bytes: int | None = Field(default=None, ge=0)
    item_count: int | None = Field(default=None, ge=0)
    storage_policy: Literal["hash_only", "plaintext", "encrypted", "external_ref"] = (
        "hash_only"
    )
    original_content: str | None = None
    compressed_content: str | None = None
    original_content_ref: str | None = Field(default=None, max_length=1024)
    compressed_content_ref: str | None = Field(default=None, max_length=1024)
    retention_expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TokenUsageBreakdownCommand(StrictCommand):
    measurement_source: Literal[
        "provider_reported",
        "estimated_before",
        "estimated_after",
        "simulation",
    ]
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    newly_processed_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    raw_usage: dict[str, Any] = Field(default_factory=dict)


class CostCalculationCommand(StrictCommand):
    calculation_kind: Literal["measured", "estimated", "simulation"]
    pricing_snapshot_id: str | None = None
    input_cost: Decimal | None = Field(default=None, ge=0)
    cached_input_cost: Decimal | None = Field(default=None, ge=0)
    cache_write_cost: Decimal | None = Field(default=None, ge=0)
    output_cost: Decimal | None = Field(default=None, ge=0)
    reasoning_cost: Decimal | None = Field(default=None, ge=0)
    total_cost: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    assumptions: dict[str, Any] = Field(default_factory=dict)


class ProviderCallCommand(StrictCommand):
    provider_call_key: str = Field(min_length=1, max_length=255)
    execution_attempt: int | None = Field(default=None, ge=1)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    litellm_call_id: str | None = Field(default=None, max_length=255)
    provider_request_id: str | None = Field(default=None, max_length=255)
    provider_response_id: str | None = Field(default=None, max_length=255)
    status: Literal["pending", "succeeded", "failed", "streaming"]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    cost_total: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = None
    raw_response_metadata: dict[str, Any] = Field(default_factory=dict)
    trace: TraceContextCommand = Field(default_factory=TraceContextCommand)
    token_usage: list[TokenUsageBreakdownCommand] = Field(default_factory=list)
    cost_calculations: list[CostCalculationCommand] = Field(default_factory=list)


class CacheActivityCommand(StrictCommand):
    cache_system: Literal["provider", "litellm", "headroom_ccr", "redis"]
    operation: Literal["read", "write", "create", "delete"]
    hit: bool | None = None
    execution_attempt: int | None = Field(default=None, ge=1)
    provider_call_key: str | None = Field(default=None, max_length=255)
    ccr_hash: str | None = Field(default=None, max_length=255)
    tokens_read: int | None = Field(default=None, ge=0)
    tokens_written: int | None = Field(default=None, ge=0)
    key_hash: str | None = Field(default=None, max_length=255)
    ttl_seconds: int | None = Field(default=None, ge=0)
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompressionActivityIngestCommand(StrictCommand):
    event: IngestionEventCommand
    request: CompressionRequestCommand
    config: CompressionConfigCommand
    execution: CompressionExecutionCommand
    chunks: list[CompressionChunkCommand] = Field(default_factory=list)
    provider_calls: list[ProviderCallCommand] = Field(default_factory=list)
    cache_activities: list[CacheActivityCommand] = Field(default_factory=list)

    @field_validator("provider_calls")
    @classmethod
    def provider_call_keys_are_unique(
        cls, provider_calls: list[ProviderCallCommand]
    ) -> list[ProviderCallCommand]:
        keys = [call.provider_call_key for call in provider_calls]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "provider_call_key values must be unique per ingest command"
            )
        return provider_calls


class ChunkRetrievalCommand(StrictCommand):
    ccr_hash: str = Field(min_length=1, max_length=255)
    retrieval_source: str = Field(min_length=1, max_length=64)
    query_hash: str | None = Field(default=None, max_length=255)
    result_count: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    success: bool = True
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = None
    retrieved_at: datetime | None = None
    trace: TraceContextCommand = Field(default_factory=TraceContextCommand)


class SimulationRunCommand(StrictCommand):
    simulation_key: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    strategy_name: str | None = Field(default=None, max_length=128)
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    pricing_overrides: dict[str, Any] = Field(default_factory=dict)
    selected_filter: dict[str, Any] = Field(default_factory=dict)
    trace: TraceContextCommand = Field(default_factory=TraceContextCommand)
