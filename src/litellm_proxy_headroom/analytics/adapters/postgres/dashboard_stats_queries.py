from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.dashboard_schemas import DashboardStats
from ...application.query_filters import AnalyticsFilters
from .dashboard_cache_queries import cache_dashboard_stats
from .dashboard_counts import dashboard_counts
from .dashboard_distribution_queries import (
    latency_distributions,
    savings_distributions,
)
from .dashboard_helpers import int_value, percent
from .dashboard_provider_queries import cost_stats, provider_estimate_delta
from .query_filters import matching_execution_rows


async def dashboard_stats(
    session: AsyncSession,
    filters: AnalyticsFilters | None = None,
) -> DashboardStats:
    filters = filters or AnalyticsFilters()
    matching = matching_execution_rows(filters).subquery()
    execution_ids = select(matching.c.execution_id)
    request_ids = select(matching.c.request_id).distinct()

    counts = await dashboard_counts(
        session,
        execution_ids=execution_ids,
        request_ids=request_ids,
        filters=filters,
    )
    original_tokens = int_value(counts.original_tokens)
    tokens_saved = int_value(counts.tokens_saved)
    chunks = int_value(counts.chunks)
    retrievals = int_value(counts.retrievals)
    executions = int_value(counts.executions)
    return DashboardStats(
        requests=int_value(counts.requests),
        executions=executions,
        provider_calls=int_value(counts.provider_calls),
        chunks=chunks,
        retrievals=retrievals,
        retrievals_per_chunk=round(retrievals / chunks, 6) if chunks else None,
        original_tokens=original_tokens,
        compressed_tokens=int_value(counts.compressed_tokens),
        tokens_saved=tokens_saved,
        savings_percent=percent(tokens_saved, original_tokens),
        negative_savings_executions=int_value(counts.negative_savings),
        failed_executions=int_value(counts.failed),
        success_rate=percent(int_value(counts.succeeded), executions),
        savings_distribution=await savings_distributions(session, execution_ids),
        latency_distribution=await latency_distributions(
            session,
            execution_ids=execution_ids,
            request_ids=request_ids,
        ),
        provider_estimate_delta=await provider_estimate_delta(
            session,
            execution_ids=execution_ids,
            filters=filters,
        ),
        cost=await cost_stats(session, execution_ids=execution_ids, filters=filters),
        cache=await cache_dashboard_stats(session, execution_ids),
    )
