"""create analytics foundation

Revision ID: 0001_analytics
Revises:
Create Date: 2026-06-23 10:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_analytics"
down_revision = None
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "analytics_ingestion_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "raw_payload",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=32), server_default="received", nullable=False
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=64), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "event_type",
            "event_key",
            name="uq_analytics_ingestion_event_source_type_key",
        ),
    )
    op.create_index(
        "ix_analytics_ingestion_events_received_at",
        "analytics_ingestion_events",
        ["received_at"],
    )
    op.create_index(
        "ix_analytics_ingestion_events_received_at_brin",
        "analytics_ingestion_events",
        ["received_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "ix_analytics_ingestion_events_trace_id",
        "analytics_ingestion_events",
        ["trace_id"],
    )

    op.create_table(
        "compression_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_key", sa.String(length=255), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("team_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("incoming_route", sa.String(length=255), nullable=True),
        sa.Column("provider_hint", sa.String(length=128), nullable=True),
        sa.Column("model_hint", sa.String(length=255), nullable=True),
        sa.Column("external_request_id", sa.String(length=255), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "request_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_key", name="uq_compression_requests_request_key"),
    )
    op.create_index(
        "ix_compression_requests_tenant_team",
        "compression_requests",
        ["tenant_id", "team_id"],
    )
    op.create_index(
        "ix_compression_requests_provider_model",
        "compression_requests",
        ["provider_hint", "model_hint"],
    )
    op.create_index(
        "ix_compression_requests_started_at", "compression_requests", ["started_at"]
    )
    op.create_index(
        "ix_compression_requests_started_at_brin",
        "compression_requests",
        ["started_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "ix_compression_requests_trace_id", "compression_requests", ["trace_id"]
    )
    op.create_index(
        "uq_compression_requests_source_external",
        "compression_requests",
        ["source_system", "external_request_id"],
        unique=True,
        postgresql_where=sa.text("external_request_id IS NOT NULL"),
    )

    op.create_table(
        "compression_config_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_hash", sa.String(length=128), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column(
            "strategy_version", sa.String(length=64), server_default="", nullable=False
        ),
        sa.Column("algorithm", sa.String(length=128), nullable=True),
        sa.Column("target_model", sa.String(length=255), nullable=True),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("trigger_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "raw_config",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "config_hash",
            "strategy_name",
            "strategy_version",
            name="uq_compression_config_snapshot_hash_strategy",
        ),
    )
    op.create_index(
        "ix_compression_config_snapshots_strategy",
        "compression_config_snapshots",
        ["strategy_name"],
    )
    op.create_index(
        "ix_compression_config_snapshots_created_at",
        "compression_config_snapshots",
        ["created_at"],
    )

    op.create_table(
        "compression_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "is_simulated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("original_tokens", sa.Integer(), nullable=True),
        sa.Column("compressed_tokens", sa.Integer(), nullable=True),
        sa.Column("tokens_saved", sa.Integer(), nullable=True),
        sa.Column("compression_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "transforms",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_compression_attempt_positive"
        ),
        sa.CheckConstraint(
            "original_tokens IS NULL OR original_tokens >= 0",
            name="ck_compression_original_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "compressed_tokens IS NULL OR compressed_tokens >= 0",
            name="ck_compression_compressed_tokens_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["config_snapshot_id"], ["compression_config_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["compression_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compression_executions_request", "compression_executions", ["request_id"]
    )
    op.create_index(
        "ix_compression_executions_config",
        "compression_executions",
        ["config_snapshot_id"],
    )
    op.create_index(
        "ix_compression_executions_status", "compression_executions", ["status"]
    )
    op.create_index(
        "ix_compression_executions_started_at", "compression_executions", ["started_at"]
    )
    op.create_index(
        "ix_compression_executions_started_at_brin",
        "compression_executions",
        ["started_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "ix_compression_executions_negative_savings",
        "compression_executions",
        ["tokens_saved"],
        postgresql_where=sa.text("tokens_saved < 0"),
    )
    op.create_index(
        "uq_compression_executions_actual_attempt",
        "compression_executions",
        ["request_id", "attempt_number"],
        unique=True,
        postgresql_where=sa.text("is_simulated = false"),
    )

    op.create_table(
        "compression_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column("ccr_hash", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=255), nullable=True),
        sa.Column("original_tokens", sa.Integer(), nullable=True),
        sa.Column("compressed_tokens", sa.Integer(), nullable=True),
        sa.Column("original_bytes", sa.Integer(), nullable=True),
        sa.Column("compressed_bytes", sa.Integer(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column(
            "storage_policy",
            sa.String(length=32),
            server_default="hash_only",
            nullable=False,
        ),
        sa.Column("original_content", sa.Text(), nullable=True),
        sa.Column("compressed_content", sa.Text(), nullable=True),
        sa.Column("original_content_ref", sa.String(length=1024), nullable=True),
        sa.Column("compressed_content_ref", sa.String(length=1024), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "storage_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_compression_chunks_ordinal"),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["compression_executions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id", "ordinal", name="uq_compression_chunks_execution_ordinal"
        ),
    )
    op.create_index(
        "ix_compression_chunks_execution", "compression_chunks", ["execution_id"]
    )
    op.create_index(
        "ix_compression_chunks_ccr_hash", "compression_chunks", ["ccr_hash"]
    )
    op.create_index(
        "ix_compression_chunks_content_hash", "compression_chunks", ["content_hash"]
    )
    op.create_index(
        "ix_compression_chunks_retention_expires_at",
        "compression_chunks",
        ["retention_expires_at"],
    )

    op.create_table(
        "provider_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_call_key", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("litellm_call_id", sa.String(length=255), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_total", sa.Numeric(18, 8), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "raw_response_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["compression_executions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["compression_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_call_key", name="uq_provider_calls_key"),
    )
    op.create_index("ix_provider_calls_request", "provider_calls", ["request_id"])
    op.create_index("ix_provider_calls_execution", "provider_calls", ["execution_id"])
    op.create_index(
        "ix_provider_calls_provider_model", "provider_calls", ["provider", "model"]
    )
    op.create_index("ix_provider_calls_status", "provider_calls", ["status"])
    op.create_index("ix_provider_calls_started_at", "provider_calls", ["started_at"])
    op.create_index(
        "ix_provider_calls_started_at_brin",
        "provider_calls",
        ["started_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "uq_provider_calls_provider_request_id",
        "provider_calls",
        ["provider", "provider_request_id"],
        unique=True,
        postgresql_where=sa.text("provider_request_id IS NOT NULL"),
    )
    op.create_index(
        "uq_provider_calls_provider_response_id",
        "provider_calls",
        ["provider", "provider_response_id"],
        unique=True,
        postgresql_where=sa.text("provider_response_id IS NOT NULL"),
    )

    op.create_table(
        "token_usage_breakdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("measurement_source", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("newly_processed_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "raw_usage", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_call_id IS NOT NULL OR execution_id IS NOT NULL",
            name="ck_token_usage_has_parent",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["compression_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["provider_call_id"], ["provider_calls.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_token_usage_provider_call", "token_usage_breakdowns", ["provider_call_id"]
    )
    op.create_index(
        "ix_token_usage_execution", "token_usage_breakdowns", ["execution_id"]
    )
    op.create_index(
        "ix_token_usage_measurement_source",
        "token_usage_breakdowns",
        ["measurement_source"],
    )

    op.create_table(
        "cache_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cache_system", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("hit", sa.Boolean(), nullable=True),
        sa.Column("tokens_read", sa.Integer(), nullable=True),
        sa.Column("tokens_written", sa.Integer(), nullable=True),
        sa.Column("key_hash", sa.String(length=255), nullable=True),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "activity_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["compression_chunks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["compression_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["provider_call_id"], ["provider_calls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["compression_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cache_activities_request", "cache_activities", ["request_id"])
    op.create_index(
        "ix_cache_activities_execution", "cache_activities", ["execution_id"]
    )
    op.create_index(
        "ix_cache_activities_provider_call", "cache_activities", ["provider_call_id"]
    )
    op.create_index("ix_cache_activities_chunk", "cache_activities", ["chunk_id"])
    op.create_index(
        "ix_cache_activities_system_operation",
        "cache_activities",
        ["cache_system", "operation"],
    )
    op.create_index(
        "ix_cache_activities_occurred_at", "cache_activities", ["occurred_at"]
    )
    op.create_index(
        "ix_cache_activities_occurred_at_brin",
        "cache_activities",
        ["occurred_at"],
        postgresql_using="brin",
    )

    op.create_table(
        "chunk_retrieval_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ccr_hash", sa.String(length=255), nullable=False),
        sa.Column("retrieval_source", sa.String(length=64), nullable=False),
        sa.Column("query_hash", sa.String(length=255), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "success", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=64), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["compression_chunks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chunk_retrieval_events_chunk", "chunk_retrieval_events", ["chunk_id"]
    )
    op.create_index(
        "ix_chunk_retrieval_events_ccr_hash", "chunk_retrieval_events", ["ccr_hash"]
    )
    op.create_index(
        "ix_chunk_retrieval_events_retrieved_at",
        "chunk_retrieval_events",
        ["retrieved_at"],
    )
    op.create_index(
        "ix_chunk_retrieval_events_retrieved_at_brin",
        "chunk_retrieval_events",
        ["retrieved_at"],
        postgresql_using="brin",
    )

    op.create_table(
        "pricing_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_token_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column("cached_input_token_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column("cache_write_token_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column("output_token_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column("reasoning_token_rate", sa.Numeric(18, 12), nullable=True),
        sa.Column(
            "source_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "model",
            "currency",
            "effective_from",
            name="uq_pricing_snapshots_identity_effective",
        ),
    )
    op.create_index(
        "ix_pricing_snapshots_provider_model",
        "pricing_snapshots",
        ["provider", "model"],
    )
    op.create_index(
        "ix_pricing_snapshots_effective",
        "pricing_snapshots",
        ["effective_from", "effective_to"],
    )

    op.create_table(
        "simulation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("strategy_name", sa.String(length=128), nullable=True),
        sa.Column(
            "config_overrides",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pricing_overrides",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "selected_filter",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("simulation_key", name="uq_simulation_runs_key"),
    )
    op.create_index("ix_simulation_runs_status", "simulation_runs", ["status"])
    op.create_index("ix_simulation_runs_started_at", "simulation_runs", ["started_at"])

    op.create_table(
        "simulation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("simulated_original_tokens", sa.Integer(), nullable=True),
        sa.Column("simulated_compressed_tokens", sa.Integer(), nullable=True),
        sa.Column("simulated_tokens_saved", sa.Integer(), nullable=True),
        sa.Column("simulated_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("baseline_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "diff_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["simulation_run_id"], ["simulation_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_chunk_id"], ["compression_chunks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_execution_id"], ["compression_executions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_request_id"], ["compression_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_simulation_results_run", "simulation_results", ["simulation_run_id"]
    )
    op.create_index(
        "ix_simulation_results_source_request",
        "simulation_results",
        ["source_request_id"],
    )
    op.create_index(
        "ix_simulation_results_source_execution",
        "simulation_results",
        ["source_execution_id"],
    )

    op.create_table(
        "cost_calculations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("simulation_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pricing_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("calculation_kind", sa.String(length=64), nullable=False),
        sa.Column("input_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("cached_input_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("cache_write_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("reasoning_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("total_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "currency", sa.String(length=8), server_default="USD", nullable=False
        ),
        sa.Column(
            "assumptions",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_call_id IS NOT NULL OR execution_id IS NOT NULL OR simulation_result_id IS NOT NULL",
            name="ck_cost_calculations_has_parent",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["compression_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pricing_snapshot_id"], ["pricing_snapshots.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["provider_call_id"], ["provider_calls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["simulation_result_id"], ["simulation_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_calculations_provider_call", "cost_calculations", ["provider_call_id"]
    )
    op.create_index(
        "ix_cost_calculations_execution", "cost_calculations", ["execution_id"]
    )
    op.create_index(
        "ix_cost_calculations_simulation_result",
        "cost_calculations",
        ["simulation_result_id"],
    )

    op.create_table(
        "retention_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("content_ttl_days", sa.Integer(), nullable=True),
        sa.Column("metadata_ttl_days", sa.Integer(), nullable=True),
        sa.Column("derived_stats_ttl_days", sa.Integer(), nullable=True),
        sa.Column("simulation_ttl_days", sa.Integer(), nullable=True),
        sa.Column("archive_after_days", sa.Integer(), nullable=True),
        sa.Column(
            "policy_metadata",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_retention_policies_name"),
    )


def downgrade() -> None:
    op.drop_table("retention_policies")
    op.drop_table("cost_calculations")
    op.drop_table("simulation_results")
    op.drop_table("simulation_runs")
    op.drop_table("pricing_snapshots")
    op.drop_table("chunk_retrieval_events")
    op.drop_table("cache_activities")
    op.drop_table("token_usage_breakdowns")
    op.drop_table("provider_calls")
    op.drop_table("compression_chunks")
    op.drop_table("compression_executions")
    op.drop_table("compression_config_snapshots")
    op.drop_table("compression_requests")
    op.drop_table("analytics_ingestion_events")
