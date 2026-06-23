from decimal import Decimal

from litellm_proxy_headroom.analytics.adapters.postgres.simulation_calculation import (
    calculate_simulation,
)


def test_simulation_applies_alternate_compression_ratio_and_pricing() -> None:
    result = calculate_simulation(
        original_tokens=1200,
        compressed_tokens=750,
        tokens_saved=450,
        provider_input_tokens=750,
        cached_input_tokens=120,
        cache_write_tokens=0,
        output_tokens=42,
        reasoning_tokens=7,
        measured_cost=None,
        config_overrides={"compression_ratio": "0.5"},
        pricing_overrides={
            "input_token_rate": "0.000001",
            "cached_input_token_rate": "0.0000002",
            "cache_write_token_rate": "0.0000005",
            "output_token_rate": "0.000002",
            "reasoning_token_rate": "0.000003",
        },
    )

    assert result.simulated_original_tokens == 1200
    assert result.simulated_compressed_tokens == 600
    assert result.simulated_tokens_saved == 600
    assert result.diff_metadata["token_savings_delta"] == 150
    assert result.baseline_cost == Decimal("0.0008790")
    assert result.simulated_cost == Decimal("0.0007290")
    assert result.diff_metadata["cost_delta"] == "-0.0001500"


def test_simulation_threshold_can_skip_small_historical_executions() -> None:
    result = calculate_simulation(
        original_tokens=300,
        compressed_tokens=120,
        tokens_saved=180,
        provider_input_tokens=120,
        cached_input_tokens=None,
        cache_write_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        measured_cost=Decimal("0.02"),
        config_overrides={"min_original_tokens": 500, "compression_ratio": "0.5"},
        pricing_overrides={},
    )

    assert result.simulated_compressed_tokens == 300
    assert result.simulated_tokens_saved == 0
    assert result.baseline_cost == Decimal("0.02")
    assert result.simulated_cost is None
    assert result.diff_metadata["token_savings_delta"] == -180


def test_simulation_can_model_negative_savings() -> None:
    result = calculate_simulation(
        original_tokens=100,
        compressed_tokens=80,
        tokens_saved=20,
        provider_input_tokens=80,
        cached_input_tokens=None,
        cache_write_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        measured_cost=None,
        config_overrides={"compression_ratio": "1.25"},
        pricing_overrides={"input_token_rate": "0.01"},
    )

    assert result.simulated_compressed_tokens == 125
    assert result.simulated_tokens_saved == -25
    assert result.diff_metadata["token_savings_delta"] == -45
    assert result.simulated_cost == Decimal("1.25")
