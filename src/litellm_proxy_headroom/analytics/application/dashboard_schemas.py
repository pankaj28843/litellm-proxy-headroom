from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SavingsDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_tokens_saved: int | None
    p50_tokens_saved: float | None
    p90_tokens_saved: float | None
    max_tokens_saved: int | None
    min_compression_ratio: float | None
    p50_compression_ratio: float | None
    p90_compression_ratio: float | None
    max_compression_ratio: float | None


class LatencyDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    avg_compression_duration_ms: float | None
    p50_compression_duration_ms: float | None
    p90_compression_duration_ms: float | None
    avg_end_to_end_request_latency_ms: float | None
    p50_end_to_end_request_latency_ms: float | None
    p90_end_to_end_request_latency_ms: float | None


class ProviderEstimateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_reported_input_tokens: int
    provider_reported_total_tokens: int
    estimated_before_input_tokens: int
    estimated_after_input_tokens: int
    estimated_before_provider_input_delta: int
    estimated_after_provider_input_delta: int


class ProviderCacheDashboardStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_reported_input_tokens: int
    provider_reported_cached_input_tokens: int
    provider_reported_uncached_input_tokens: int
    provider_cache_hit_percent: float | None
    cached_input_cost_multiplier: str
    billing_equivalent_input_tokens: float | None
    billing_equivalent_tokens_saved: float | None
    billing_equivalent_savings_percent: float | None
    raw_token_capacity_multiplier: float | None
    billing_equivalent_capacity_multiplier: float | None


class CostDashboardStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measured_provider_cost_total: str | None
    estimated_baseline_cost_total: str | None
    estimated_cost_savings: str | None
    cost_increase_provider_calls: int


class CacheDashboardStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_read_events: int
    cache_write_events: int
    cache_hit_events: int
    cache_tokens_read: int
    cache_tokens_written: int


class DashboardStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int
    executions: int
    provider_calls: int
    chunks: int
    retrievals: int
    retrievals_per_chunk: float | None
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    savings_percent: float | None
    negative_savings_executions: int
    failed_executions: int
    success_rate: float | None
    savings_distribution: SavingsDistribution
    latency_distribution: LatencyDistribution
    provider_estimate_delta: ProviderEstimateDelta
    provider_cache: ProviderCacheDashboardStats
    cost: CostDashboardStats
    cache: CacheDashboardStats
