from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .types import Money


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    provider: str
    model: str
    currency: str
    effective_from: datetime
    input_token_rate: Decimal | None = None
    cached_input_token_rate: Decimal | None = None
    cache_write_token_rate: Decimal | None = None
    output_token_rate: Decimal | None = None
    reasoning_token_rate: Decimal | None = None
    effective_to: datetime | None = None
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CostCalculation:
    calculation_kind: str
    total_cost: Money
    input_cost: Money | None = None
    cached_input_cost: Money | None = None
    cache_write_cost: Money | None = None
    output_cost: Money | None = None
    reasoning_cost: Money | None = None
    provider_call_key: str | None = None
    request_key: str | None = None
    execution_attempt: int | None = None
    pricing_snapshot_key: str | None = None
    assumptions: dict[str, Any] = field(default_factory=dict)
