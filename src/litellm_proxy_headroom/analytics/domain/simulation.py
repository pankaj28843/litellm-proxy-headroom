from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .types import TraceContext


class SimulationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SimulationRun:
    simulation_key: str
    name: str
    status: SimulationStatus = SimulationStatus.PENDING
    strategy_name: str | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)
    pricing_overrides: dict[str, Any] = field(default_factory=dict)
    selected_filter: dict[str, Any] = field(default_factory=dict)
    trace: TraceContext = field(default_factory=TraceContext)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class SimulationResult:
    simulation_key: str
    source_request_key: str | None = None
    source_execution_attempt: int | None = None
    source_chunk_hash: str | None = None
    simulated_original_tokens: int | None = None
    simulated_compressed_tokens: int | None = None
    simulated_tokens_saved: int | None = None
    simulated_cost: Decimal | None = None
    baseline_cost: Decimal | None = None
    diffs: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
