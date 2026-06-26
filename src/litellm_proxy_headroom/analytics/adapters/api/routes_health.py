from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from ...application.schemas import AnalyticsHealth
from ..postgres.session import session_scope
from .deps import session_factory

router = APIRouter()


@router.get("/health", response_model=AnalyticsHealth)
async def health() -> AnalyticsHealth:
    return AnalyticsHealth(status="healthy", database_ready=True)


@router.get("/ready", response_model=AnalyticsHealth)
async def ready(request: Request, response: Response) -> AnalyticsHealth:
    try:
        async with session_scope(session_factory(request)) as session:
            await session.execute(text("select 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return AnalyticsHealth(status="unavailable", database_ready=False)
    return AnalyticsHealth(status="ready", database_ready=True)
