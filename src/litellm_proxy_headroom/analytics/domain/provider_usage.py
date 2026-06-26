from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .types import (
    CacheOperation,
    CacheSystem,
    MeasurementSource,
    Money,
    ProviderCallStatus,
    TimeWindow,
    TokenCounts,
    TraceContext,
)


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class ProviderCall:
    provider_call_key: str
    request_key: str
    provider: ProviderIdentity
    status: ProviderCallStatus
    execution_attempt: int | None = None
    litellm_call_id: str | None = None
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    timing: TimeWindow = field(default_factory=TimeWindow)
    cost: Money | None = None
    error_type: str | None = None
    error_message: str | None = None
    raw_response_metadata: dict[str, Any] = field(default_factory=dict)
    trace: TraceContext = field(default_factory=TraceContext)


@dataclass(frozen=True, slots=True)
class TokenUsageBreakdown:
    measurement_source: MeasurementSource
    tokens: TokenCounts
    provider_call_key: str | None = None
    request_key: str | None = None
    execution_attempt: int | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CacheActivity:
    cache_system: CacheSystem
    operation: CacheOperation
    occurred_at: datetime
    hit: bool | None = None
    request_key: str | None = None
    execution_attempt: int | None = None
    provider_call_key: str | None = None
    ccr_hash: str | None = None
    tokens_read: int | None = None
    tokens_written: int | None = None
    key_hash: str | None = None
    ttl_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
