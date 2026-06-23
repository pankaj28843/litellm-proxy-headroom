from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.query_filters import AnalyticsFilters
from ...application.read_models import StatsBreakdown, StatsBreakdownRow
from ...application.schemas import CompressionStats
from .models import (
    CacheActivityModel,
    ChunkRetrievalEventModel,
    CompressionChunkModel,
    CompressionConfigSnapshotModel,
    CompressionExecutionModel,
    CompressionRequestModel,
    ProviderCallModel,
    TokenUsageBreakdownModel,
)
from .query_filters import (
    execution_conditions,
    matching_execution_rows,
    provider_call_conditions,
)


def _int(value: Any) -> int:
    return int(value or 0)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _percent(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round((part / whole) * 100, 4)


async def compression_stats(
    session: AsyncSession,
    filters: AnalyticsFilters | None = None,
) -> CompressionStats:
    filters = filters or AnalyticsFilters()
    matching = matching_execution_rows(filters).subquery()
    execution_ids = select(matching.c.execution_id)
    request_ids = select(matching.c.request_id).distinct()

    token_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(CompressionExecutionModel.original_tokens), 0),
                func.coalesce(func.sum(CompressionExecutionModel.compressed_tokens), 0),
                func.coalesce(func.sum(CompressionExecutionModel.tokens_saved), 0),
                func.count(CompressionExecutionModel.id).filter(
                    CompressionExecutionModel.status == "failed"
                ),
                func.count(CompressionExecutionModel.id).filter(
                    CompressionExecutionModel.tokens_saved < 0
                ),
                func.avg(CompressionExecutionModel.duration_ms),
                func.count(CompressionExecutionModel.id).filter(
                    CompressionExecutionModel.status == "succeeded"
                ),
            ).where(CompressionExecutionModel.id.in_(execution_ids))
        )
    ).one()
    requests = await session.scalar(
        select(func.count()).select_from(request_ids.subquery())
    )
    executions = await session.scalar(select(func.count()).select_from(matching))
    chunks = await session.scalar(
        select(func.count(CompressionChunkModel.id)).where(
            CompressionChunkModel.execution_id.in_(execution_ids)
        )
    )
    provider_calls = await session.scalar(
        select(func.count(ProviderCallModel.id)).where(
            ProviderCallModel.execution_id.in_(execution_ids),
            *provider_call_conditions(filters),
        )
    )
    retrievals = await session.scalar(
        select(func.count(ChunkRetrievalEventModel.id))
        .select_from(ChunkRetrievalEventModel)
        .join(
            CompressionChunkModel,
            ChunkRetrievalEventModel.chunk_id == CompressionChunkModel.id,
        )
        .where(CompressionChunkModel.execution_id.in_(execution_ids))
    )
    provider_usage = (
        await session.execute(
            select(
                func.coalesce(func.sum(TokenUsageBreakdownModel.input_tokens), 0),
                func.coalesce(
                    func.sum(TokenUsageBreakdownModel.cached_input_tokens), 0
                ),
                func.coalesce(
                    func.sum(TokenUsageBreakdownModel.newly_processed_input_tokens),
                    0,
                ),
                func.coalesce(func.sum(TokenUsageBreakdownModel.cache_write_tokens), 0),
                func.coalesce(func.sum(TokenUsageBreakdownModel.output_tokens), 0),
                func.coalesce(func.sum(TokenUsageBreakdownModel.reasoning_tokens), 0),
                func.coalesce(func.sum(TokenUsageBreakdownModel.total_tokens), 0),
            )
            .select_from(TokenUsageBreakdownModel)
            .join(
                ProviderCallModel,
                TokenUsageBreakdownModel.provider_call_id == ProviderCallModel.id,
            )
            .where(
                ProviderCallModel.execution_id.in_(execution_ids),
                TokenUsageBreakdownModel.measurement_source == "provider_reported",
                *provider_call_conditions(filters),
            )
        )
    ).one()
    cache_row = (
        await session.execute(
            select(
                func.count(CacheActivityModel.id).filter(
                    CacheActivityModel.operation == "read"
                ),
                func.count(CacheActivityModel.id).filter(
                    CacheActivityModel.operation.in_(["write", "create"])
                ),
                func.count(CacheActivityModel.id).filter(
                    CacheActivityModel.hit.is_(True)
                ),
                func.coalesce(func.sum(CacheActivityModel.tokens_read), 0),
                func.coalesce(func.sum(CacheActivityModel.tokens_written), 0),
            ).where(CacheActivityModel.execution_id.in_(execution_ids))
        )
    ).one()

    original_tokens = _int(token_row[0])
    compressed_tokens = _int(token_row[1])
    tokens_saved = _int(token_row[2])
    execution_count = _int(executions)

    return CompressionStats(
        requests=_int(requests),
        executions=execution_count,
        chunks=_int(chunks),
        provider_calls=_int(provider_calls),
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        tokens_saved=tokens_saved,
        retrievals=_int(retrievals),
        savings_percent=_percent(tokens_saved, original_tokens),
        compression_ratio=(
            round(compressed_tokens / original_tokens, 6)
            if original_tokens > 0
            else None
        ),
        failures=_int(token_row[3]),
        negative_savings_executions=_int(token_row[4]),
        avg_compression_duration_ms=_float(token_row[5]),
        success_rate=_percent(_int(token_row[6]), execution_count),
        provider_input_tokens=_int(provider_usage[0]),
        cached_input_tokens=_int(provider_usage[1]),
        newly_processed_input_tokens=_int(provider_usage[2]),
        cache_write_tokens=_int(provider_usage[3]),
        provider_output_tokens=_int(provider_usage[4]),
        provider_reasoning_tokens=_int(provider_usage[5]),
        provider_total_tokens=_int(provider_usage[6]),
        cache_read_events=_int(cache_row[0]),
        cache_write_events=_int(cache_row[1]),
        cache_hit_events=_int(cache_row[2]),
        cache_tokens_read=_int(cache_row[3]),
        cache_tokens_written=_int(cache_row[4]),
    )


async def compression_stats_breakdown(
    session: AsyncSession,
    filters: AnalyticsFilters,
    *,
    group_by: str,
    limit: int = 50,
) -> StatsBreakdown:
    dimension = {
        "provider": func.coalesce(CompressionRequestModel.provider_hint, "unknown"),
        "model": func.coalesce(CompressionRequestModel.model_hint, "unknown"),
        "strategy": CompressionConfigSnapshotModel.strategy_name,
        "tenant": func.coalesce(CompressionRequestModel.tenant_id, "unknown"),
        "team": func.coalesce(CompressionRequestModel.team_id, "unknown"),
        "status": CompressionExecutionModel.status,
    }[group_by]
    tokens_saved_sum = func.coalesce(
        func.sum(CompressionExecutionModel.tokens_saved), 0
    )
    rows = (
        await session.execute(
            select(
                dimension.label("value"),
                func.count(func.distinct(CompressionRequestModel.id)).label("requests"),
                func.count(CompressionExecutionModel.id).label("executions"),
                func.coalesce(
                    func.sum(CompressionExecutionModel.original_tokens), 0
                ).label("original_tokens"),
                func.coalesce(
                    func.sum(CompressionExecutionModel.compressed_tokens), 0
                ).label("compressed_tokens"),
                tokens_saved_sum.label("tokens_saved"),
                func.count(CompressionExecutionModel.id)
                .filter(CompressionExecutionModel.tokens_saved < 0)
                .label("negative_savings_executions"),
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
            .where(*execution_conditions(filters))
            .group_by(dimension)
            .order_by(tokens_saved_sum.desc())
            .limit(limit)
        )
    ).all()
    return StatsBreakdown(
        group_by=group_by,
        rows=[
            StatsBreakdownRow(
                value=str(row.value),
                requests=_int(row.requests),
                executions=_int(row.executions),
                original_tokens=_int(row.original_tokens),
                compressed_tokens=_int(row.compressed_tokens),
                tokens_saved=_int(row.tokens_saved),
                negative_savings_executions=_int(row.negative_savings_executions),
            )
            for row in rows
        ],
    )
