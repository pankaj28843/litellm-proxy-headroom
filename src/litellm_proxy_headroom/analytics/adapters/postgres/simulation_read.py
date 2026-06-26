from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.simulation_schemas import (
    SimulationResultSummary,
    SimulationRunDetail,
    SimulationRunPage,
    SimulationRunSummary,
)
from .models import CompressionRequestModel, SimulationResultModel, SimulationRunModel


async def get_simulation_detail(
    session: AsyncSession,
    simulation_key: str,
    *,
    duplicate: bool = False,
) -> SimulationRunDetail | None:
    run = await session.scalar(
        select(SimulationRunModel).where(
            SimulationRunModel.simulation_key == simulation_key
        )
    )
    if run is None:
        return None
    results = await _result_summaries(session, run.id)
    summary = await _run_summary(session, run, duplicate=duplicate)
    return SimulationRunDetail(
        **summary.model_dump(),
        config_overrides=dict(run.config_overrides),
        pricing_overrides=dict(run.pricing_overrides),
        results=results,
    )


async def list_simulation_runs(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> SimulationRunPage:
    total = await session.scalar(select(func.count(SimulationRunModel.id)))
    runs = (
        await session.scalars(
            select(SimulationRunModel)
            .order_by(SimulationRunModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return SimulationRunPage(
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=[await _run_summary(session, run) for run in runs],
    )


async def _run_summary(
    session: AsyncSession,
    run: SimulationRunModel,
    *,
    duplicate: bool = False,
) -> SimulationRunSummary:
    row = (
        await session.execute(
            select(
                func.count(SimulationResultModel.id),
                func.coalesce(
                    func.sum(SimulationResultModel.simulated_tokens_saved), 0
                ),
                func.sum(SimulationResultModel.baseline_cost),
                func.sum(SimulationResultModel.simulated_cost),
            ).where(SimulationResultModel.simulation_run_id == run.id)
        )
    ).one()
    return SimulationRunSummary(
        simulation_id=str(run.id),
        simulation_key=run.simulation_key,
        name=run.name,
        status=run.status,
        strategy_name=run.strategy_name,
        selected_filter=dict(run.selected_filter),
        result_count=int(row[0] or 0),
        total_simulated_tokens_saved=int(row[1] or 0),
        total_baseline_cost=_decimal_str(row[2]),
        total_simulated_cost=_decimal_str(row[3]),
        duration_ms=run.duration_ms,
        started_at=run.started_at,
        ended_at=run.ended_at,
        created_at=run.created_at,
        duplicate=duplicate,
    )


async def _result_summaries(
    session: AsyncSession,
    run_id: object,
) -> list[SimulationResultSummary]:
    rows = (
        await session.execute(
            select(SimulationResultModel, CompressionRequestModel.request_key)
            .outerjoin(
                CompressionRequestModel,
                SimulationResultModel.source_request_id == CompressionRequestModel.id,
            )
            .where(SimulationResultModel.simulation_run_id == run_id)
            .order_by(SimulationResultModel.created_at)
        )
    ).all()
    return [
        SimulationResultSummary(
            result_id=str(result.id),
            source_request_id=str(result.source_request_id)
            if result.source_request_id
            else None,
            source_request_key=request_key,
            source_execution_id=str(result.source_execution_id)
            if result.source_execution_id
            else None,
            source_chunk_id=str(result.source_chunk_id)
            if result.source_chunk_id
            else None,
            simulated_original_tokens=result.simulated_original_tokens,
            simulated_compressed_tokens=result.simulated_compressed_tokens,
            simulated_tokens_saved=result.simulated_tokens_saved,
            simulated_cost=_decimal_str(result.simulated_cost),
            baseline_cost=_decimal_str(result.baseline_cost),
            token_savings_delta=result.diff_metadata.get("token_savings_delta"),
            cost_delta=result.diff_metadata.get("cost_delta"),
            error_type=result.error_type,
            created_at=result.created_at,
        )
        for result, request_key in rows
    ]


def _decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
