from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from ...application.commands import SimulationRunCommand
from ...application.services import SimulationService
from ...application.simulation_schemas import SimulationRunDetail, SimulationRunPage
from ..otel.simulation import get_simulation_telemetry
from ..otel.telemetry import get_analytics_telemetry
from ..postgres.simulation_read import get_simulation_detail, list_simulation_runs
from ..postgres.simulation_store import SimulationPostgresStore
from .deps import SessionDep

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("/runs", response_model=SimulationRunDetail)
async def run_simulation(
    command: SimulationRunCommand,
    session: SessionDep,
) -> SimulationRunDetail:
    telemetry = get_analytics_telemetry()
    simulation_telemetry = get_simulation_telemetry()
    started = time.perf_counter()
    with telemetry.start_span(
        "litellm.proxy.analytics.simulation.run",
        {"litellm.proxy.analytics.operation": "simulation"},
    ):
        try:
            detail = await SimulationService(
                SimulationPostgresStore(session)
            ).run_simulation(command)
        except Exception as exc:
            telemetry.mark_span_error(exc)
            duration_ms = max(int((time.perf_counter() - started) * 1000), 0)
            simulation_telemetry.record_run(
                duration_ms=duration_ms,
                result_count=0,
                status="failed",
            )
            raise
    duration_ms = max(int((time.perf_counter() - started) * 1000), 0)
    simulation_telemetry.record_run(
        duration_ms=duration_ms,
        result_count=detail.result_count,
        status=detail.status,
    )
    return detail


@router.get("/runs", response_model=SimulationRunPage)
async def simulation_runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SimulationRunPage:
    return await list_simulation_runs(session, limit=limit, offset=offset)


@router.get("/runs/{simulation_key}", response_model=SimulationRunDetail)
async def simulation_detail(
    simulation_key: str,
    session: SessionDep,
) -> SimulationRunDetail:
    detail = await get_simulation_detail(session, simulation_key)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="simulation run not found",
        )
    return detail
