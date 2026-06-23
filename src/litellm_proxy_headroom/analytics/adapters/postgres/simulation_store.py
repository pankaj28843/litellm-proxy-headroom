from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.commands import SimulationRunCommand, TraceContextCommand
from ...application.simulation_schemas import SimulationRunDetail
from .models import SimulationResultModel, SimulationRunModel
from .simulation_calculation import calculate_simulation
from .simulation_read import get_simulation_detail
from .simulation_selection import selected_execution_rows


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _trace_columns(trace: TraceContextCommand) -> dict[str, str | None]:
    return {"trace_id": trace.trace_id, "span_id": trace.span_id}


class SimulationPostgresStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run_simulation(
        self, command: SimulationRunCommand
    ) -> SimulationRunDetail:
        run_id, duplicate = await self._ensure_run(command)
        if duplicate:
            detail = await get_simulation_detail(
                self._session, command.simulation_key, duplicate=True
            )
            if detail is None:
                raise RuntimeError("simulation conflict did not return existing row")
            return detail

        started = time.perf_counter()
        rows = await selected_execution_rows(self._session, command.selected_filter)
        for row in rows:
            calculation = calculate_simulation(
                original_tokens=row.original_tokens,
                compressed_tokens=row.compressed_tokens,
                tokens_saved=row.tokens_saved,
                provider_input_tokens=row.provider_input_tokens,
                cached_input_tokens=row.cached_input_tokens,
                cache_write_tokens=row.cache_write_tokens,
                output_tokens=row.output_tokens,
                reasoning_tokens=row.reasoning_tokens,
                measured_cost=row.measured_cost,
                config_overrides=command.config_overrides,
                pricing_overrides=command.pricing_overrides,
            )
            self._session.add(
                SimulationResultModel(
                    simulation_run_id=run_id,
                    source_request_id=row.request_id,
                    source_execution_id=row.execution_id,
                    source_chunk_id=row.chunk_id,
                    simulated_original_tokens=calculation.simulated_original_tokens,
                    simulated_compressed_tokens=calculation.simulated_compressed_tokens,
                    simulated_tokens_saved=calculation.simulated_tokens_saved,
                    simulated_cost=calculation.simulated_cost,
                    baseline_cost=calculation.baseline_cost,
                    diff_metadata=calculation.diff_metadata,
                )
            )
        await self._mark_succeeded(
            run_id,
            duration_ms=max(int((time.perf_counter() - started) * 1000), 0),
        )
        detail = await get_simulation_detail(self._session, command.simulation_key)
        if detail is None:
            raise RuntimeError("simulation run missing after insert")
        return detail

    async def _ensure_run(
        self,
        command: SimulationRunCommand,
    ) -> tuple[object, bool]:
        started_at = _utcnow()
        stmt = (
            insert(SimulationRunModel)
            .values(
                simulation_key=command.simulation_key,
                name=command.name,
                status="running",
                strategy_name=command.strategy_name,
                config_overrides=command.config_overrides,
                pricing_overrides=command.pricing_overrides,
                selected_filter=command.selected_filter,
                started_at=started_at,
                **_trace_columns(command.trace),
            )
            .on_conflict_do_nothing(index_elements=[SimulationRunModel.simulation_key])
            .returning(SimulationRunModel.id)
        )
        run_id = await self._session.scalar(stmt)
        if run_id is not None:
            return run_id, False
        existing_id = await self._session.scalar(
            insert(SimulationRunModel)
            .values(
                simulation_key=command.simulation_key,
                name=command.name,
                status="running",
            )
            .on_conflict_do_nothing(index_elements=[SimulationRunModel.simulation_key])
            .returning(SimulationRunModel.id)
        )
        if existing_id is not None:
            return existing_id, False
        row = await get_simulation_detail(self._session, command.simulation_key)
        if row is None:
            raise RuntimeError("simulation conflict did not return existing row")
        return row.simulation_id, True

    async def _mark_succeeded(self, run_id: object, *, duration_ms: int) -> None:
        await self._session.execute(
            update(SimulationRunModel)
            .where(SimulationRunModel.id == run_id)
            .values(status="succeeded", ended_at=_utcnow(), duration_ms=duration_ms)
        )
