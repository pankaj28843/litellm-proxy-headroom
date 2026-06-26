from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.query_filters import AnalyticsFilters
from ...application.read_models import CompressionRecordPage, CompressionRecordSummary
from .models import (
    ChunkRetrievalEventModel,
    CompressionChunkModel,
    CompressionConfigSnapshotModel,
    CompressionExecutionModel,
    CompressionRequestModel,
    ProviderCallModel,
)
from .query_filters import execution_conditions, execution_time, matching_execution_rows


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


async def list_compression_records(
    session: AsyncSession,
    filters: AnalyticsFilters,
    *,
    limit: int,
    offset: int,
) -> CompressionRecordPage:
    base = matching_execution_rows(filters).subquery()
    total = await session.scalar(select(func.count()).select_from(base))
    rows = (await session.execute(_records_statement(filters, limit, offset))).all()
    return CompressionRecordPage(
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=[
            CompressionRecordSummary(
                request_id=str(row.request_id),
                request_key=row.request_key,
                execution_id=str(row.execution_id),
                attempt_number=row.attempt_number,
                status=row.status,
                is_simulated=row.is_simulated,
                started_at=row.started_at,
                tenant_id=row.tenant_id,
                team_id=row.team_id,
                provider=row.provider,
                model=row.model,
                strategy_name=row.strategy_name,
                strategy_version=row.strategy_version,
                original_tokens=row.original_tokens,
                compressed_tokens=row.compressed_tokens,
                tokens_saved=row.tokens_saved,
                compression_ratio=_float(row.compression_ratio),
                duration_ms=row.duration_ms,
                provider_calls=int(row.provider_calls or 0),
                chunks=int(row.chunks or 0),
                retrievals=int(row.retrievals or 0),
            )
            for row in rows
        ],
    )


def _records_statement(filters: AnalyticsFilters, limit: int, offset: int):
    provider = _latest_provider_field(ProviderCallModel.provider)
    model = _latest_provider_field(ProviderCallModel.model)
    return (
        select(
            CompressionRequestModel.id.label("request_id"),
            CompressionRequestModel.request_key,
            CompressionRequestModel.tenant_id,
            CompressionRequestModel.team_id,
            func.coalesce(provider, CompressionRequestModel.provider_hint).label(
                "provider"
            ),
            func.coalesce(model, CompressionRequestModel.model_hint).label("model"),
            CompressionExecutionModel.id.label("execution_id"),
            CompressionExecutionModel.attempt_number,
            CompressionExecutionModel.status,
            CompressionExecutionModel.is_simulated,
            execution_time().label("started_at"),
            CompressionExecutionModel.original_tokens,
            CompressionExecutionModel.compressed_tokens,
            CompressionExecutionModel.tokens_saved,
            CompressionExecutionModel.compression_ratio,
            CompressionExecutionModel.duration_ms,
            CompressionConfigSnapshotModel.strategy_name,
            CompressionConfigSnapshotModel.strategy_version,
            _provider_call_count().label("provider_calls"),
            _chunk_count().label("chunks"),
            _retrieval_count().label("retrievals"),
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
        .order_by(execution_time().desc(), CompressionExecutionModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )


def _latest_provider_field(column):
    return (
        select(column)
        .where(ProviderCallModel.execution_id == CompressionExecutionModel.id)
        .order_by(ProviderCallModel.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )


def _provider_call_count():
    return (
        select(func.count(ProviderCallModel.id))
        .where(
            or_(
                ProviderCallModel.execution_id == CompressionExecutionModel.id,
                and_(
                    ProviderCallModel.execution_id.is_(None),
                    ProviderCallModel.request_id == CompressionRequestModel.id,
                ),
            )
        )
        .scalar_subquery()
    )


def _chunk_count():
    return (
        select(func.count(CompressionChunkModel.id))
        .where(CompressionChunkModel.execution_id == CompressionExecutionModel.id)
        .scalar_subquery()
    )


def _retrieval_count():
    return (
        select(func.count(ChunkRetrievalEventModel.id))
        .select_from(ChunkRetrievalEventModel)
        .join(
            CompressionChunkModel,
            ChunkRetrievalEventModel.chunk_id == CompressionChunkModel.id,
        )
        .where(CompressionChunkModel.execution_id == CompressionExecutionModel.id)
        .scalar_subquery()
    )
