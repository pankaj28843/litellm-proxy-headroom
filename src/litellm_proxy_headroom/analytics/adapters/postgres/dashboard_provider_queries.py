from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.dashboard_schemas import (
    CostDashboardStats,
    ProviderCacheDashboardStats,
    ProviderEstimateDelta,
)
from ...application.query_filters import AnalyticsFilters
from .dashboard_helpers import decimal_str, int_value
from .models import CostCalculationModel, ProviderCallModel, TokenUsageBreakdownModel
from .query_filters import provider_call_conditions

DEFAULT_CACHED_INPUT_COST_MULTIPLIER = Decimal("0.10")


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


async def provider_cache_stats(
    session: AsyncSession,
    *,
    execution_ids: Any,
    filters: AnalyticsFilters,
    fallback_baseline_input_tokens: int,
) -> ProviderCacheDashboardStats:
    row = (
        await session.execute(
            select(
                _usage_sum("provider_reported", TokenUsageBreakdownModel.input_tokens),
                _usage_sum(
                    "provider_reported",
                    TokenUsageBreakdownModel.cached_input_tokens,
                ),
                _usage_sum("estimated_before", TokenUsageBreakdownModel.input_tokens),
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
    provider_cached = min(int_value(row[1]), provider_input)
    provider_uncached = provider_input - provider_cached
    baseline_input = int_value(row[2]) or fallback_baseline_input_tokens
    cached_multiplier = _cached_input_cost_multiplier()
    billing_equivalent_input = (
        Decimal(provider_uncached) + (Decimal(provider_cached) * cached_multiplier)
        if provider_input
        else None
    )
    billing_equivalent_saved = (
        Decimal(baseline_input) - billing_equivalent_input
        if billing_equivalent_input is not None and baseline_input
        else None
    )
    return ProviderCacheDashboardStats(
        provider_reported_input_tokens=provider_input,
        provider_reported_cached_input_tokens=provider_cached,
        provider_reported_uncached_input_tokens=provider_uncached,
        provider_cache_hit_percent=(
            _percent_decimal(Decimal(provider_cached), Decimal(provider_input))
            if provider_input
            else None
        ),
        cached_input_cost_multiplier=str(cached_multiplier),
        billing_equivalent_input_tokens=_float_decimal(billing_equivalent_input),
        billing_equivalent_tokens_saved=_float_decimal(billing_equivalent_saved),
        billing_equivalent_savings_percent=(
            _percent_decimal(billing_equivalent_saved, Decimal(baseline_input))
            if billing_equivalent_saved is not None and baseline_input
            else None
        ),
        raw_token_capacity_multiplier=(
            round(baseline_input / provider_input, 6)
            if baseline_input and provider_input
            else None
        ),
        billing_equivalent_capacity_multiplier=(
            round(float(Decimal(baseline_input) / billing_equivalent_input), 6)
            if baseline_input
            and billing_equivalent_input is not None
            and billing_equivalent_input > 0
            else None
        ),
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


def _cached_input_cost_multiplier() -> Decimal:
    raw = os.getenv("ANALYTICS_CACHED_INPUT_COST_MULTIPLIER", "").strip()
    if not raw:
        return DEFAULT_CACHED_INPUT_COST_MULTIPLIER
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return DEFAULT_CACHED_INPUT_COST_MULTIPLIER
    if value < 0 or value > 1:
        return DEFAULT_CACHED_INPUT_COST_MULTIPLIER
    return value


def _float_decimal(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _percent_decimal(part: Decimal, whole: Decimal) -> float | None:
    if whole <= 0:
        return None
    return round(float((part / whole) * Decimal(100)), 4)


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
