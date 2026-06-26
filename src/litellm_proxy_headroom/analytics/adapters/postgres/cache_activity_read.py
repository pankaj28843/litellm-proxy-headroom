from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.read_models import CacheActivitySummary
from .models import CacheActivityModel


async def cache_activities_for_request(
    session: AsyncSession,
    request_id: object,
) -> list[CacheActivitySummary]:
    rows = (
        await session.scalars(
            select(CacheActivityModel)
            .where(CacheActivityModel.request_id == request_id)
            .order_by(CacheActivityModel.occurred_at)
        )
    ).all()
    return [
        CacheActivitySummary(
            cache_activity_id=str(activity.id),
            cache_system=activity.cache_system,
            operation=activity.operation,
            hit=activity.hit,
            tokens_read=activity.tokens_read,
            tokens_written=activity.tokens_written,
            key_hash=activity.key_hash,
            ttl_seconds=activity.ttl_seconds,
            occurred_at=activity.occurred_at,
        )
        for activity in rows
    ]
