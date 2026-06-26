from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IngestionEventModel(Base):
    __tablename__ = "analytics_ingestion_events"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "event_type",
            "event_key",
            name="uq_analytics_ingestion_event_source_type_key",
        ),
        Index("ix_analytics_ingestion_events_received_at", "received_at"),
        Index(
            "ix_analytics_ingestion_events_received_at_brin",
            "received_at",
            postgresql_using="brin",
        ),
        Index("ix_analytics_ingestion_events_trace_id", "trace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_hash: Mapped[str | None] = mapped_column(String(128))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="received", server_default="received"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)


class CompressionRequestModel(Base):
    __tablename__ = "compression_requests"
    __table_args__ = (
        UniqueConstraint("request_key", name="uq_compression_requests_request_key"),
        Index("ix_compression_requests_tenant_team", "tenant_id", "team_id"),
        Index("ix_compression_requests_provider_model", "provider_hint", "model_hint"),
        Index("ix_compression_requests_started_at", "started_at"),
        Index(
            "ix_compression_requests_started_at_brin",
            "started_at",
            postgresql_using="brin",
        ),
        Index("ix_compression_requests_trace_id", "trace_id"),
        Index(
            "uq_compression_requests_source_external",
            "source_system",
            "external_request_id",
            unique=True,
            postgresql_where=text("external_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128))
    team_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(128))
    incoming_route: Mapped[str | None] = mapped_column(String(255))
    provider_hint: Mapped[str | None] = mapped_column(String(128))
    model_hint: Mapped[str | None] = mapped_column(String(255))
    external_request_id: Mapped[str | None] = mapped_column(String(255))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompressionConfigSnapshotModel(Base):
    __tablename__ = "compression_config_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "config_hash",
            "strategy_name",
            "strategy_version",
            name="uq_compression_config_snapshot_hash_strategy",
        ),
        Index("ix_compression_config_snapshots_strategy", "strategy_name"),
        Index("ix_compression_config_snapshots_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    algorithm: Mapped[str | None] = mapped_column(String(128))
    target_model: Mapped[str | None] = mapped_column(String(255))
    token_budget: Mapped[int | None] = mapped_column(Integer)
    trigger_reason: Mapped[str | None] = mapped_column(String(255))
    raw_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompressionExecutionModel(Base):
    __tablename__ = "compression_executions"
    __table_args__ = (
        CheckConstraint("attempt_number >= 1", name="ck_compression_attempt_positive"),
        CheckConstraint(
            "original_tokens IS NULL OR original_tokens >= 0",
            name="ck_compression_original_tokens_nonnegative",
        ),
        CheckConstraint(
            "compressed_tokens IS NULL OR compressed_tokens >= 0",
            name="ck_compression_compressed_tokens_nonnegative",
        ),
        Index("ix_compression_executions_request", "request_id"),
        Index("ix_compression_executions_config", "config_snapshot_id"),
        Index("ix_compression_executions_status", "status"),
        Index("ix_compression_executions_started_at", "started_at"),
        Index(
            "ix_compression_executions_started_at_brin",
            "started_at",
            postgresql_using="brin",
        ),
        Index(
            "ix_compression_executions_negative_savings",
            "tokens_saved",
            postgresql_where=text("tokens_saved < 0"),
        ),
        Index(
            "uq_compression_executions_actual_attempt",
            "request_id",
            "attempt_number",
            unique=True,
            postgresql_where=text("is_simulated = false"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compression_requests.id", ondelete="CASCADE"), nullable=False
    )
    config_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compression_config_snapshots.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    original_tokens: Mapped[int | None] = mapped_column(Integer)
    compressed_tokens: Mapped[int | None] = mapped_column(Integer)
    tokens_saved: Mapped[int | None] = mapped_column(Integer)
    compression_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    transforms: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompressionChunkModel(Base):
    __tablename__ = "compression_chunks"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "ordinal",
            name="uq_compression_chunks_execution_ordinal",
        ),
        CheckConstraint("ordinal >= 0", name="ck_compression_chunks_ordinal"),
        Index("ix_compression_chunks_execution", "execution_id"),
        Index("ix_compression_chunks_ccr_hash", "ccr_hash"),
        Index("ix_compression_chunks_content_hash", "content_hash"),
        Index("ix_compression_chunks_retention_expires_at", "retention_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compression_executions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(255))
    ccr_hash: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str | None] = mapped_column(String(255))
    original_tokens: Mapped[int | None] = mapped_column(Integer)
    compressed_tokens: Mapped[int | None] = mapped_column(Integer)
    original_bytes: Mapped[int | None] = mapped_column(Integer)
    compressed_bytes: Mapped[int | None] = mapped_column(Integer)
    item_count: Mapped[int | None] = mapped_column(Integer)
    storage_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="hash_only", server_default="hash_only"
    )
    original_content: Mapped[str | None] = mapped_column(Text)
    compressed_content: Mapped[str | None] = mapped_column(Text)
    original_content_ref: Mapped[str | None] = mapped_column(String(1024))
    compressed_content_ref: Mapped[str | None] = mapped_column(String(1024))
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    storage_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderCallModel(Base):
    __tablename__ = "provider_calls"
    __table_args__ = (
        UniqueConstraint("provider_call_key", name="uq_provider_calls_key"),
        Index("ix_provider_calls_request", "request_id"),
        Index("ix_provider_calls_execution", "execution_id"),
        Index("ix_provider_calls_provider_model", "provider", "model"),
        Index("ix_provider_calls_status", "status"),
        Index("ix_provider_calls_started_at", "started_at"),
        Index(
            "ix_provider_calls_started_at_brin",
            "started_at",
            postgresql_using="brin",
        ),
        Index(
            "uq_provider_calls_provider_request_id",
            "provider",
            "provider_request_id",
            unique=True,
            postgresql_where=text("provider_request_id IS NOT NULL"),
        ),
        Index(
            "uq_provider_calls_provider_response_id",
            "provider",
            "provider_response_id",
            unique=True,
            postgresql_where=text("provider_response_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compression_requests.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_executions.id", ondelete="SET NULL")
    )
    provider_call_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    litellm_call_id: Mapped[str | None] = mapped_column(String(255))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    provider_response_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str | None] = mapped_column(String(8))
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_response_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TokenUsageBreakdownModel(Base):
    __tablename__ = "token_usage_breakdowns"
    __table_args__ = (
        CheckConstraint(
            "provider_call_id IS NOT NULL OR execution_id IS NOT NULL",
            name="ck_token_usage_has_parent",
        ),
        Index("ix_token_usage_provider_call", "provider_call_id"),
        Index("ix_token_usage_execution", "execution_id"),
        Index("ix_token_usage_measurement_source", "measurement_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_calls.id", ondelete="CASCADE")
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_executions.id", ondelete="CASCADE")
    )
    measurement_source: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    newly_processed_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    raw_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CacheActivityModel(Base):
    __tablename__ = "cache_activities"
    __table_args__ = (
        Index("ix_cache_activities_request", "request_id"),
        Index("ix_cache_activities_execution", "execution_id"),
        Index("ix_cache_activities_provider_call", "provider_call_id"),
        Index("ix_cache_activities_chunk", "chunk_id"),
        Index("ix_cache_activities_system_operation", "cache_system", "operation"),
        Index("ix_cache_activities_occurred_at", "occurred_at"),
        Index(
            "ix_cache_activities_occurred_at_brin",
            "occurred_at",
            postgresql_using="brin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_requests.id", ondelete="CASCADE")
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_executions.id", ondelete="CASCADE")
    )
    provider_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_calls.id", ondelete="CASCADE")
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_chunks.id", ondelete="CASCADE")
    )
    cache_system: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    hit: Mapped[bool | None] = mapped_column(Boolean)
    tokens_read: Mapped[int | None] = mapped_column(Integer)
    tokens_written: Mapped[int | None] = mapped_column(Integer)
    key_hash: Mapped[str | None] = mapped_column(String(255))
    ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activity_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ChunkRetrievalEventModel(Base):
    __tablename__ = "chunk_retrieval_events"
    __table_args__ = (
        Index("ix_chunk_retrieval_events_chunk", "chunk_id"),
        Index("ix_chunk_retrieval_events_ccr_hash", "ccr_hash"),
        Index("ix_chunk_retrieval_events_retrieved_at", "retrieved_at"),
        Index(
            "ix_chunk_retrieval_events_retrieved_at_brin",
            "retrieved_at",
            postgresql_using="brin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_chunks.id", ondelete="SET NULL")
    )
    ccr_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    retrieval_source: Mapped[str] = mapped_column(String(64), nullable=False)
    query_hash: Mapped[str | None] = mapped_column(String(255))
    result_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PricingSnapshotModel(Base):
    __tablename__ = "pricing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "currency",
            "effective_from",
            name="uq_pricing_snapshots_identity_effective",
        ),
        Index("ix_pricing_snapshots_provider_model", "provider", "model"),
        Index("ix_pricing_snapshots_effective", "effective_from", "effective_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_token_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    cached_input_token_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    cache_write_token_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    output_token_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    reasoning_token_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CostCalculationModel(Base):
    __tablename__ = "cost_calculations"
    __table_args__ = (
        CheckConstraint(
            """
            provider_call_id IS NOT NULL
            OR execution_id IS NOT NULL
            OR simulation_result_id IS NOT NULL
            """,
            name="ck_cost_calculations_has_parent",
        ),
        Index("ix_cost_calculations_provider_call", "provider_call_id"),
        Index("ix_cost_calculations_execution", "execution_id"),
        Index("ix_cost_calculations_simulation_result", "simulation_result_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_calls.id", ondelete="CASCADE")
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_executions.id", ondelete="CASCADE")
    )
    simulation_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("simulation_results.id", ondelete="CASCADE")
    )
    pricing_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pricing_snapshots.id", ondelete="SET NULL")
    )
    calculation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    input_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    cached_input_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    cache_write_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    output_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    reasoning_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="USD", server_default="USD"
    )
    assumptions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SimulationRunModel(Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (
        UniqueConstraint("simulation_key", name="uq_simulation_runs_key"),
        Index("ix_simulation_runs_status", "status"),
        Index("ix_simulation_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    simulation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    strategy_name: Mapped[str | None] = mapped_column(String(128))
    config_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    pricing_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    selected_filter: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SimulationResultModel(Base):
    __tablename__ = "simulation_results"
    __table_args__ = (
        Index("ix_simulation_results_run", "simulation_run_id"),
        Index("ix_simulation_results_source_request", "source_request_id"),
        Index("ix_simulation_results_source_execution", "source_execution_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_requests.id", ondelete="SET NULL")
    )
    source_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_executions.id", ondelete="SET NULL")
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compression_chunks.id", ondelete="SET NULL")
    )
    simulated_original_tokens: Mapped[int | None] = mapped_column(Integer)
    simulated_compressed_tokens: Mapped[int | None] = mapped_column(Integer)
    simulated_tokens_saved: Mapped[int | None] = mapped_column(Integer)
    simulated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    baseline_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    diff_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RetentionPolicyModel(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (UniqueConstraint("name", name="uq_retention_policies_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    content_ttl_days: Mapped[int | None] = mapped_column(Integer)
    metadata_ttl_days: Mapped[int | None] = mapped_column(Integer)
    derived_stats_ttl_days: Mapped[int | None] = mapped_column(Integer)
    simulation_ttl_days: Mapped[int | None] = mapped_column(Integer)
    archive_after_days: Mapped[int | None] = mapped_column(Integer)
    policy_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
