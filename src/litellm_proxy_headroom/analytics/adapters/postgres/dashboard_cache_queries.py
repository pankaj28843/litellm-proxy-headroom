from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.dashboard_schemas import CacheDashboardStats
from .dashboard_helpers import int_value
from .models import CacheActivityModel


async def cache_dashboard_stats(
    session: AsyncSession,
    execution_ids: Any,
) -> CacheDashboardStats:
    row = (
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
    return CacheDashboardStats(
        cache_read_events=int_value(row[0]),
        cache_write_events=int_value(row[1]),
        cache_hit_events=int_value(row[2]),
        cache_tokens_read=int_value(row[3]),
        cache_tokens_written=int_value(row[4]),
    )
