from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.query_filters import AnalyticsFilters
from .models import (
    CompressionChunkModel,
    CompressionConfigSnapshotModel,
    CompressionExecutionModel,
    CompressionRequestModel,
    ProviderCallModel,
    TokenUsageBreakdownModel,
)
from .query_filters import execution_conditions


def filters_from_selection(selection: dict[str, Any]) -> AnalyticsFilters:
    return AnalyticsFilters(
        started_from=_datetime(selection.get("from")),
        started_to=_datetime(selection.get("to")),
        provider=_string(selection.get("provider")),
        model=_string(selection.get("model")),
        strategy=_string(selection.get("strategy")),
        tenant_id=_string(selection.get("tenant_id")),
        team_id=_string(selection.get("team_id")),
        status=_string(selection.get("status")),
        negative_savings=selection.get("negative_savings")
        if isinstance(selection.get("negative_savings"), bool)
        else None,
    )


def selection_limit(selection: dict[str, Any]) -> int:
    raw_limit = selection.get("limit", 100)
    try:
        return min(max(int(raw_limit), 1), 1000)
    except (TypeError, ValueError):
        return 100


async def selected_execution_rows(
    session: AsyncSession,
    selection: dict[str, Any],
):
    filters = filters_from_selection(selection)
    first_chunk_id = (
        select(CompressionChunkModel.id)
        .where(CompressionChunkModel.execution_id == CompressionExecutionModel.id)
        .order_by(CompressionChunkModel.ordinal)
        .limit(1)
        .scalar_subquery()
    )
    measured_cost = (
        select(func.sum(ProviderCallModel.cost_total))
        .where(ProviderCallModel.execution_id == CompressionExecutionModel.id)
        .scalar_subquery()
    )
    token_sums = _provider_token_sums()
    conditions = execution_conditions(filters)
    request_key = _string(selection.get("request_key"))
    if request_key:
        conditions.append(CompressionRequestModel.request_key == request_key)
    return (
        await session.execute(
            select(
                CompressionRequestModel.id.label("request_id"),
                CompressionRequestModel.request_key,
                CompressionExecutionModel.id.label("execution_id"),
                first_chunk_id.label("chunk_id"),
                CompressionExecutionModel.original_tokens,
                CompressionExecutionModel.compressed_tokens,
                CompressionExecutionModel.tokens_saved,
                measured_cost.label("measured_cost"),
                token_sums["input"].label("provider_input_tokens"),
                token_sums["cached"].label("cached_input_tokens"),
                token_sums["cache_write"].label("cache_write_tokens"),
                token_sums["output"].label("output_tokens"),
                token_sums["reasoning"].label("reasoning_tokens"),
            )
            .select_from(CompressionExecutionModel)
            .join(
                CompressionRequestModel,
                CompressionExecutionModel.request_id == CompressionRequestModel.id,
            )
            .join(
                CompressionConfigSnapshotModel,
                CompressionExecutionModel.config_snapshot_id
                == CompressionConfigSnapshotModel.id,
            )
            .where(*conditions)
            .order_by(CompressionExecutionModel.created_at.desc())
            .limit(selection_limit(selection))
        )
    ).all()


def _provider_token_sums() -> dict[str, Any]:
    def token_sum(column: Any) -> Any:
        return (
            select(func.sum(column))
            .select_from(TokenUsageBreakdownModel)
            .join(
                ProviderCallModel,
                TokenUsageBreakdownModel.provider_call_id == ProviderCallModel.id,
            )
            .where(
                ProviderCallModel.execution_id == CompressionExecutionModel.id,
                TokenUsageBreakdownModel.measurement_source == "provider_reported",
            )
            .scalar_subquery()
        )

    return {
        "input": token_sum(TokenUsageBreakdownModel.input_tokens),
        "cached": token_sum(TokenUsageBreakdownModel.cached_input_tokens),
        "cache_write": token_sum(TokenUsageBreakdownModel.cache_write_tokens),
        "output": token_sum(TokenUsageBreakdownModel.output_tokens),
        "reasoning": token_sum(TokenUsageBreakdownModel.reasoning_tokens),
    }


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
