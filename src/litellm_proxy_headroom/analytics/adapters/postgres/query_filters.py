from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, or_, select

from ...application.query_filters import AnalyticsFilters
from .models import (
    CompressionConfigSnapshotModel,
    CompressionExecutionModel,
    CompressionRequestModel,
    ProviderCallModel,
)


def execution_time() -> Any:
    return func.coalesce(
        CompressionExecutionModel.started_at,
        CompressionRequestModel.started_at,
        CompressionRequestModel.created_at,
    )


def execution_conditions(filters: AnalyticsFilters) -> list[Any]:
    conditions: list[Any] = [CompressionExecutionModel.is_simulated.is_(False)]
    if filters.started_from is not None:
        conditions.append(execution_time() >= filters.started_from)
    if filters.started_to is not None:
        conditions.append(execution_time() <= filters.started_to)
    if filters.strategy:
        conditions.append(
            CompressionConfigSnapshotModel.strategy_name == filters.strategy
        )
    if filters.tenant_id:
        conditions.append(CompressionRequestModel.tenant_id == filters.tenant_id)
    if filters.team_id:
        conditions.append(CompressionRequestModel.team_id == filters.team_id)
    if filters.status:
        conditions.append(CompressionExecutionModel.status == filters.status)
    if filters.negative_savings is True:
        conditions.append(CompressionExecutionModel.tokens_saved < 0)
    elif filters.negative_savings is False:
        conditions.append(
            or_(
                CompressionExecutionModel.tokens_saved.is_(None),
                CompressionExecutionModel.tokens_saved >= 0,
            )
        )
    if filters.provider:
        provider_exists = (
            select(ProviderCallModel.id)
            .where(
                ProviderCallModel.request_id == CompressionRequestModel.id,
                ProviderCallModel.provider == filters.provider,
            )
            .exists()
        )
        conditions.append(
            or_(
                CompressionRequestModel.provider_hint == filters.provider,
                provider_exists,
            )
        )
    if filters.model:
        model_exists = (
            select(ProviderCallModel.id)
            .where(
                ProviderCallModel.request_id == CompressionRequestModel.id,
                ProviderCallModel.model == filters.model,
            )
            .exists()
        )
        conditions.append(
            or_(CompressionRequestModel.model_hint == filters.model, model_exists)
        )
    return conditions


def matching_execution_rows(filters: AnalyticsFilters) -> Select[Any]:
    return (
        select(
            CompressionExecutionModel.id.label("execution_id"),
            CompressionExecutionModel.request_id.label("request_id"),
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
    )


def provider_call_conditions(filters: AnalyticsFilters) -> list[Any]:
    conditions: list[Any] = []
    if filters.provider:
        conditions.append(ProviderCallModel.provider == filters.provider)
    if filters.model:
        conditions.append(ProviderCallModel.model == filters.model)
    return conditions
