from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SimulationResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    source_request_id: str | None
    source_request_key: str | None
    source_execution_id: str | None
    source_chunk_id: str | None
    simulated_original_tokens: int | None
    simulated_compressed_tokens: int | None
    simulated_tokens_saved: int | None
    simulated_cost: str | None
    baseline_cost: str | None
    token_savings_delta: int | None
    cost_delta: str | None
    error_type: str | None
    created_at: datetime


class SimulationRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: str
    simulation_key: str
    name: str
    status: str
    strategy_name: str | None
    selected_filter: dict[str, object]
    result_count: int
    total_simulated_tokens_saved: int
    total_baseline_cost: str | None
    total_simulated_cost: str | None
    duration_ms: int | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    duplicate: bool = False


class SimulationRunDetail(SimulationRunSummary):
    model_config = ConfigDict(extra="forbid")

    config_overrides: dict[str, object]
    pricing_overrides: dict[str, object]
    results: list[SimulationResultSummary]


class SimulationRunPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[SimulationRunSummary]
