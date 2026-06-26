from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class CompressionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProviderCallStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STREAMING = "streaming"


class MeasurementSource(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED_BEFORE = "estimated_before"
    ESTIMATED_AFTER = "estimated_after"
    SIMULATION = "simulation"


class CacheSystem(StrEnum):
    PROVIDER = "provider"
    LITELLM = "litellm"
    HEADROOM_CCR = "headroom_ccr"
    REDIS = "redis"


class CacheOperation(StrEnum):
    READ = "read"
    WRITE = "write"
    CREATE = "create"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str | None = None
    span_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None


@dataclass(frozen=True, slots=True)
class TokenCounts:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    newly_processed_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class TimeWindow:
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
