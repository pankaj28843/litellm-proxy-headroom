from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.dashboard_schemas import LatencyDistribution, SavingsDistribution
from .dashboard_helpers import float_value
from .models import CompressionExecutionModel, CompressionRequestModel


async def savings_distributions(
    session: AsyncSession,
    execution_ids: Any,
) -> SavingsDistribution:
    row = (
        await session.execute(
            select(
                func.min(CompressionExecutionModel.tokens_saved),
                func.percentile_cont(0.5).within_group(
                    CompressionExecutionModel.tokens_saved
                ),
                func.percentile_cont(0.9).within_group(
                    CompressionExecutionModel.tokens_saved
                ),
                func.max(CompressionExecutionModel.tokens_saved),
                func.min(CompressionExecutionModel.compression_ratio),
                func.percentile_cont(0.5).within_group(
                    CompressionExecutionModel.compression_ratio
                ),
                func.percentile_cont(0.9).within_group(
                    CompressionExecutionModel.compression_ratio
                ),
                func.max(CompressionExecutionModel.compression_ratio),
            ).where(CompressionExecutionModel.id.in_(execution_ids))
        )
    ).one()
    return SavingsDistribution(
        min_tokens_saved=row[0],
        p50_tokens_saved=float_value(row[1]),
        p90_tokens_saved=float_value(row[2]),
        max_tokens_saved=row[3],
        min_compression_ratio=float_value(row[4]),
        p50_compression_ratio=float_value(row[5]),
        p90_compression_ratio=float_value(row[6]),
        max_compression_ratio=float_value(row[7]),
    )


async def latency_distributions(
    session: AsyncSession,
    *,
    execution_ids: Any,
    request_ids: Any,
) -> LatencyDistribution:
    request_latency_ms = (
        func.extract(
            "epoch",
            CompressionRequestModel.ended_at - CompressionRequestModel.started_at,
        )
        * 1000
    )
    compression_row = (
        await session.execute(
            select(
                func.avg(CompressionExecutionModel.duration_ms),
                func.percentile_cont(0.5).within_group(
                    CompressionExecutionModel.duration_ms
                ),
                func.percentile_cont(0.9).within_group(
                    CompressionExecutionModel.duration_ms
                ),
            ).where(CompressionExecutionModel.id.in_(execution_ids))
        )
    ).one()
    request_row = (
        await session.execute(
            select(
                func.avg(request_latency_ms),
                func.percentile_cont(0.5).within_group(request_latency_ms),
                func.percentile_cont(0.9).within_group(request_latency_ms),
            ).where(
                CompressionRequestModel.id.in_(request_ids),
                CompressionRequestModel.started_at.is_not(None),
                CompressionRequestModel.ended_at.is_not(None),
            )
        )
    ).one()
    return LatencyDistribution(
        avg_compression_duration_ms=float_value(compression_row[0]),
        p50_compression_duration_ms=float_value(compression_row[1]),
        p90_compression_duration_ms=float_value(compression_row[2]),
        avg_end_to_end_request_latency_ms=float_value(request_row[0]),
        p50_end_to_end_request_latency_ms=float_value(request_row[1]),
        p90_end_to_end_request_latency_ms=float_value(request_row[2]),
    )
