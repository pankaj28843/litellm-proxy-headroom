from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .types import CompressionStatus, TimeWindow, TraceContext


@dataclass(frozen=True, slots=True)
class CompressionStrategy:
    name: str
    version: str | None = None
    algorithm: str | None = None


@dataclass(frozen=True, slots=True)
class CompressionConfigSnapshot:
    config_hash: str
    strategy: CompressionStrategy
    target_model: str | None = None
    token_budget: int | None = None
    trigger_reason: str | None = None
    raw_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompressionRequest:
    request_key: str
    source_system: str
    tenant_id: str | None = None
    team_id: str | None = None
    user_id: str | None = None
    incoming_route: str | None = None
    provider_hint: str | None = None
    model_hint: str | None = None
    external_request_id: str | None = None
    trace: TraceContext = field(default_factory=TraceContext)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompressionExecution:
    request_key: str
    config_hash: str
    attempt_number: int
    status: CompressionStatus
    is_simulated: bool = False
    timing: TimeWindow = field(default_factory=TimeWindow)
    original_tokens: int | None = None
    compressed_tokens: int | None = None
    tokens_saved: int | None = None
    compression_ratio: Decimal | None = None
    transforms: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    trace: TraceContext = field(default_factory=TraceContext)


@dataclass(frozen=True, slots=True)
class CompressionEffectiveness:
    original_tokens: int
    compressed_tokens: int

    @property
    def tokens_saved(self) -> int:
        return self.original_tokens - self.compressed_tokens

    @property
    def ratio(self) -> Decimal:
        if self.original_tokens == 0:
            return Decimal("0")
        return Decimal(self.compressed_tokens) / Decimal(self.original_tokens)
