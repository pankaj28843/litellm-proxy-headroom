from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StatsBreakdownRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    requests: int
    executions: int
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    negative_savings_executions: int


class StatsBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: str
    rows: list[StatsBreakdownRow]


class CompressionRecordSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    request_key: str
    execution_id: str
    attempt_number: int
    status: str
    is_simulated: bool
    started_at: datetime | None
    tenant_id: str | None
    team_id: str | None
    provider: str | None
    model: str | None
    strategy_name: str
    strategy_version: str
    original_tokens: int | None
    compressed_tokens: int | None
    tokens_saved: int | None
    compression_ratio: float | None
    duration_ms: int | None
    provider_calls: int
    chunks: int
    retrievals: int


class CompressionRecordPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[CompressionRecordSummary]


class CompressionChunkSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    execution_id: str
    ordinal: int
    ccr_hash: str | None
    content_hash: str | None
    role: str | None
    tool_name: str | None
    original_tokens: int | None
    compressed_tokens: int | None
    storage_policy: str
    has_original_content: bool
    has_compressed_content: bool
    retrievals: int
    created_at: datetime


class TokenUsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_source: str
    input_tokens: int | None
    cached_input_tokens: int | None
    newly_processed_input_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    has_raw_usage: bool


class ProviderCallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_call_id: str
    execution_id: str | None
    provider_call_key: str
    provider: str
    model: str
    status: str
    litellm_call_id: str | None
    provider_request_id: str | None
    provider_response_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: int | None
    cost_total: str | None
    currency: str | None
    error_type: str | None
    has_raw_response_metadata: bool
    token_usage: list[TokenUsageSummary]


class CacheActivitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_activity_id: str
    cache_system: str
    operation: str
    hit: bool | None
    tokens_read: int | None
    tokens_written: int | None
    key_hash: str | None
    ttl_seconds: int | None
    occurred_at: datetime


class CompressionExecutionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    config_snapshot_id: str
    attempt_number: int
    is_simulated: bool
    status: str
    strategy_name: str
    strategy_version: str
    algorithm: str | None
    target_model: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: int | None
    original_tokens: int | None
    compressed_tokens: int | None
    tokens_saved: int | None
    compression_ratio: float | None
    error_type: str | None
    chunks: list[CompressionChunkSummary]


class CompressionRequestDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    request_key: str
    source_system: str
    tenant_id: str | None
    team_id: str | None
    user_id: str | None
    incoming_route: str | None
    provider_hint: str | None
    model_hint: str | None
    external_request_id: str | None
    trace_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    executions: list[CompressionExecutionDetail]
    provider_calls: list[ProviderCallSummary]
    cache_activities: list[CacheActivitySummary]
