from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from starlette.responses import PlainTextResponse

from ...application.dashboard_schemas import DashboardStats
from ...application.read_models import StatsBreakdown
from ...application.schemas import CompressionStats
from ..postgres.dashboard_stats_queries import dashboard_stats
from ..postgres.stats_queries import compression_stats, compression_stats_breakdown
from .deps import SessionDep
from .query_params import AnalyticsFiltersDep

router = APIRouter()


@router.get("/stats", response_model=CompressionStats)
async def stats(session: SessionDep, filters: AnalyticsFiltersDep) -> CompressionStats:
    return await compression_stats(session, filters)


@router.get("/stats/breakdown", response_model=StatsBreakdown)
async def stats_breakdown(
    session: SessionDep,
    filters: AnalyticsFiltersDep,
    group_by: Literal["provider", "model", "strategy", "tenant", "team", "status"],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> StatsBreakdown:
    return await compression_stats_breakdown(
        session,
        filters,
        group_by=group_by,
        limit=limit,
    )


@router.get("/stats/dashboard", response_model=DashboardStats)
async def stats_dashboard(
    session: SessionDep,
    filters: AnalyticsFiltersDep,
) -> DashboardStats:
    return await dashboard_stats(session, filters)


@router.get("/metrics")
async def metrics(session: SessionDep) -> PlainTextResponse:
    snapshot = await compression_stats(session)
    lines = [
        "# HELP litellm_proxy_analytics_requests_total Compression requests stored.",
        "# TYPE litellm_proxy_analytics_requests_total counter",
        f"litellm_proxy_analytics_requests_total {snapshot.requests}",
        "# HELP litellm_proxy_analytics_executions_total Compression executions stored.",
        "# TYPE litellm_proxy_analytics_executions_total counter",
        f"litellm_proxy_analytics_executions_total {snapshot.executions}",
        "# HELP litellm_proxy_analytics_chunks_total Compression chunks stored.",
        "# TYPE litellm_proxy_analytics_chunks_total counter",
        f"litellm_proxy_analytics_chunks_total {snapshot.chunks}",
        "# HELP litellm_proxy_analytics_provider_calls_total Provider calls stored.",
        "# TYPE litellm_proxy_analytics_provider_calls_total counter",
        f"litellm_proxy_analytics_provider_calls_total {snapshot.provider_calls}",
        "# HELP litellm_proxy_analytics_tokens_saved_total Tokens saved by compression.",
        "# TYPE litellm_proxy_analytics_tokens_saved_total counter",
        f"litellm_proxy_analytics_tokens_saved_total {snapshot.tokens_saved}",
        "# HELP litellm_proxy_analytics_retrievals_total CCR retrievals recorded.",
        "# TYPE litellm_proxy_analytics_retrievals_total counter",
        f"litellm_proxy_analytics_retrievals_total {snapshot.retrievals}",
    ]
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
