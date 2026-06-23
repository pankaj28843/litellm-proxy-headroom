from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.dashboard_schemas import CostDashboardStats, ProviderEstimateDelta
from ...application.query_filters import AnalyticsFilters
from .dashboard_helpers import decimal_str, int_value
from .models import CostCalculationModel, ProviderCallModel, TokenUsageBreakdownModel
from .query_filters import provider_call_conditions


async def provider_estimate_delta(
    session: AsyncSession,
    *,
    execution_ids: Any,
    filters: AnalyticsFilters,
) -> ProviderEstimateDelta:
    row = (
        await session.execute(
            select(
                _usage_sum("provider_reported", TokenUsageBreakdownModel.input_tokens),
                _usage_sum("provider_reported", TokenUsageBreakdownModel.total_tokens),
                _usage_sum("estimated_before", TokenUsageBreakdownModel.input_tokens),
                _usage_sum("estimated_after", TokenUsageBreakdownModel.input_tokens),
            )
            .select_from(TokenUsageBreakdownModel)
            .join(
                ProviderCallModel,
                TokenUsageBreakdownModel.provider_call_id == ProviderCallModel.id,
            )
            .where(
                ProviderCallModel.execution_id.in_(execution_ids),
                *provider_call_conditions(filters),
            )
        )
    ).one()
    provider_input = int_value(row[0])
    estimated_before = int_value(row[2])
    estimated_after = int_value(row[3])
    return ProviderEstimateDelta(
        provider_reported_input_tokens=provider_input,
        provider_reported_total_tokens=int_value(row[1]),
        estimated_before_input_tokens=estimated_before,
        estimated_after_input_tokens=estimated_after,
        estimated_before_provider_input_delta=estimated_before - provider_input,
        estimated_after_provider_input_delta=estimated_after - provider_input,
    )


async def cost_stats(
    session: AsyncSession,
    *,
    execution_ids: Any,
    filters: AnalyticsFilters,
) -> CostDashboardStats:
    estimated = await session.scalar(
        select(func.sum(CostCalculationModel.total_cost))
        .select_from(CostCalculationModel)
        .join(
            ProviderCallModel,
            CostCalculationModel.provider_call_id == ProviderCallModel.id,
        )
        .where(
            ProviderCallModel.execution_id.in_(execution_ids),
            CostCalculationModel.calculation_kind == "estimated",
            *provider_call_conditions(filters),
        )
    )
    measured = await session.scalar(
        select(func.sum(ProviderCallModel.cost_total)).where(
            ProviderCallModel.execution_id.in_(execution_ids),
            *provider_call_conditions(filters),
        )
    )
    savings = (
        estimated - measured if estimated is not None and measured is not None else None
    )
    return CostDashboardStats(
        measured_provider_cost_total=decimal_str(measured),
        estimated_baseline_cost_total=decimal_str(estimated),
        estimated_cost_savings=decimal_str(savings),
        cost_increase_provider_calls=int_value(
            await _cost_increase_provider_calls(session, execution_ids, filters)
        ),
    )


def _usage_sum(measurement_source: str, column: Any) -> Any:
    return func.coalesce(
        func.sum(column).filter(
            TokenUsageBreakdownModel.measurement_source == measurement_source
        ),
        0,
    )


async def _cost_increase_provider_calls(
    session: AsyncSession,
    execution_ids: Any,
    filters: AnalyticsFilters,
) -> int | None:
    return await session.scalar(
        select(func.count(func.distinct(ProviderCallModel.id)))
        .select_from(ProviderCallModel)
        .join(
            CostCalculationModel,
            CostCalculationModel.provider_call_id == ProviderCallModel.id,
        )
        .where(
            ProviderCallModel.execution_id.in_(execution_ids),
            CostCalculationModel.calculation_kind == "estimated",
            ProviderCallModel.cost_total.is_not(None),
            CostCalculationModel.total_cost.is_not(None),
            ProviderCallModel.cost_total > CostCalculationModel.total_cost,
            *provider_call_conditions(filters),
        )
    )
