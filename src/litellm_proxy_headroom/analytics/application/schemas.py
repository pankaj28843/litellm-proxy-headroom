from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AnalyticsHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    database_ready: bool


class CompressionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int
    executions: int
    chunks: int
    provider_calls: int
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    retrievals: int
    savings_percent: float | None = None
    compression_ratio: float | None = None
    failures: int = 0
    negative_savings_executions: int = 0
    avg_compression_duration_ms: float | None = None
    success_rate: float | None = None
    provider_input_tokens: int = 0
    cached_input_tokens: int = 0
    newly_processed_input_tokens: int = 0
    cache_write_tokens: int = 0
    provider_output_tokens: int = 0
    provider_reasoning_tokens: int = 0
    provider_total_tokens: int = 0
    cache_read_events: int = 0
    cache_write_events: int = 0
    cache_hit_events: int = 0
    cache_tokens_read: int = 0
    cache_tokens_written: int = 0
