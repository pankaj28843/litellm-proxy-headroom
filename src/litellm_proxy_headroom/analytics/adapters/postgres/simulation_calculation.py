from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class SimulationCalculation:
    simulated_original_tokens: int | None
    simulated_compressed_tokens: int | None
    simulated_tokens_saved: int | None
    simulated_cost: Decimal | None
    baseline_cost: Decimal | None
    diff_metadata: dict[str, Any]


def calculate_simulation(
    *,
    original_tokens: int | None,
    compressed_tokens: int | None,
    tokens_saved: int | None,
    provider_input_tokens: int | None,
    cached_input_tokens: int | None,
    cache_write_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    measured_cost: Decimal | None,
    config_overrides: dict[str, Any],
    pricing_overrides: dict[str, Any],
) -> SimulationCalculation:
    simulated_original = original_tokens
    simulated_compressed = _simulated_compressed_tokens(
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        config_overrides=config_overrides,
    )
    simulated_saved = (
        None
        if simulated_original is None or simulated_compressed is None
        else simulated_original - simulated_compressed
    )
    baseline_cost = measured_cost or _token_cost(
        input_tokens=provider_input_tokens or compressed_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        pricing_overrides=pricing_overrides,
    )
    simulated_cost = _token_cost(
        input_tokens=simulated_compressed,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        pricing_overrides=pricing_overrides,
    )
    return SimulationCalculation(
        simulated_original_tokens=simulated_original,
        simulated_compressed_tokens=simulated_compressed,
        simulated_tokens_saved=simulated_saved,
        simulated_cost=simulated_cost,
        baseline_cost=baseline_cost,
        diff_metadata={
            "baseline_compressed_tokens": compressed_tokens,
            "baseline_tokens_saved": tokens_saved,
            "token_savings_delta": _delta(simulated_saved, tokens_saved),
            "cost_delta": _decimal_delta(simulated_cost, baseline_cost),
            "config_overrides": config_overrides,
            "pricing_overrides": pricing_overrides,
        },
    )


def _simulated_compressed_tokens(
    *,
    original_tokens: int | None,
    compressed_tokens: int | None,
    config_overrides: dict[str, Any],
) -> int | None:
    if original_tokens is None:
        return None
    threshold = _int(config_overrides.get("min_original_tokens"))
    if threshold is not None and original_tokens < threshold:
        return original_tokens
    ratio = _decimal(config_overrides.get("compression_ratio"))
    if ratio is not None:
        return max(int((Decimal(original_tokens) * ratio).to_integral_value()), 0)
    multiplier = _decimal(config_overrides.get("compressed_tokens_multiplier"))
    if multiplier is not None and compressed_tokens is not None:
        return max(
            int((Decimal(compressed_tokens) * multiplier).to_integral_value()), 0
        )
    delta = _int(config_overrides.get("tokens_saved_delta"))
    if delta is not None and compressed_tokens is not None:
        return max(compressed_tokens - delta, 0)
    return compressed_tokens


def _token_cost(
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    cache_write_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    pricing_overrides: dict[str, Any],
) -> Decimal | None:
    rates = {
        "input_token_rate": input_tokens,
        "cached_input_token_rate": cached_input_tokens,
        "cache_write_token_rate": cache_write_tokens,
        "output_token_rate": output_tokens,
        "reasoning_token_rate": reasoning_tokens,
    }
    total = Decimal("0")
    any_rate = False
    for rate_key, token_count in rates.items():
        rate = _decimal(pricing_overrides.get(rate_key))
        if rate is not None and token_count is not None:
            total += Decimal(token_count) * rate
            any_rate = True
    return total if any_rate else None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _delta(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left - right


def _decimal_delta(left: Decimal | None, right: Decimal | None) -> str | None:
    if left is None or right is None:
        return None
    return str(left - right)
