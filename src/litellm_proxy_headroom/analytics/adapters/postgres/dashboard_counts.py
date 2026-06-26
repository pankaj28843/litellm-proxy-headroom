from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.query_filters import AnalyticsFilters
from .models import (
    ChunkRetrievalEventModel,
    CompressionChunkModel,
    CompressionExecutionModel,
    ProviderCallModel,
)
from .query_filters import provider_call_conditions


@dataclass(frozen=True, slots=True)
class DashboardCounts:
    requests: int
    executions: int
    provider_calls: int
    chunks: int
    retrievals: int
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    negative_savings: int
    failed: int
    succeeded: int


async def dashboard_counts(
    session: AsyncSession,
    *,
    execution_ids: Any,
    request_ids: Any,
    filters: AnalyticsFilters,
) -> DashboardCounts:
    row = (
        await session.execute(
            select(
                select(func.count())
                .select_from(request_ids.subquery())
                .scalar_subquery(),
                select(func.count())
                .select_from(execution_ids.subquery())
                .scalar_subquery(),
                _provider_call_count(execution_ids, filters),
                _chunk_count(execution_ids),
                _retrieval_count(execution_ids),
                func.coalesce(func.sum(CompressionExecutionModel.original_tokens), 0),
                func.coalesce(func.sum(CompressionExecutionModel.compressed_tokens), 0),
                func.coalesce(func.sum(CompressionExecutionModel.tokens_saved), 0),
                func.count(CompressionExecutionModel.id).filter(
                    CompressionExecutionModel.tokens_saved < 0
                ),
                func.count(CompressionExecutionModel.id).filter(
                    CompressionExecutionModel.status == "failed"
                ),
                func.count(CompressionExecutionModel.id).filter(
                    CompressionExecutionModel.status == "succeeded"
                ),
            ).where(CompressionExecutionModel.id.in_(execution_ids))
        )
    ).one()
    return DashboardCounts(*[int(value or 0) for value in row])


def _provider_call_count(execution_ids: Any, filters: AnalyticsFilters) -> Any:
    return (
        select(func.count(ProviderCallModel.id))
        .where(
            ProviderCallModel.execution_id.in_(execution_ids),
            *provider_call_conditions(filters),
        )
        .scalar_subquery()
    )


def _chunk_count(execution_ids: Any) -> Any:
    return (
        select(func.count(CompressionChunkModel.id))
        .where(CompressionChunkModel.execution_id.in_(execution_ids))
        .scalar_subquery()
    )


def _retrieval_count(execution_ids: Any) -> Any:
    return (
        select(func.count(ChunkRetrievalEventModel.id))
        .select_from(ChunkRetrievalEventModel)
        .join(
            CompressionChunkModel,
            ChunkRetrievalEventModel.chunk_id == CompressionChunkModel.id,
        )
        .where(CompressionChunkModel.execution_id.in_(execution_ids))
        .scalar_subquery()
    )
