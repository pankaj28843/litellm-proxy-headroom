import importlib.util
import sys
from pathlib import Path


def _record_payload(*args, **kwargs):
    spec = importlib.util.spec_from_file_location(
        "e2e_dashboard_stats_smoke",
        Path("scripts/e2e_dashboard_stats_smoke.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    sys.path.insert(0, str(Path("scripts").resolve()))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._record_payload(*args, **kwargs)


def test_dashboard_smoke_payload_models_estimates_cost_and_negative_savings() -> None:
    payload = _record_payload(
        "dashboard-test",
        provider="dashboard-provider",
        model="dashboard-model",
        strategy="dashboard-strategy",
        original_tokens=800,
        compressed_tokens=900,
        duration_ms=200,
        provider_cost="0.03000000",
        estimated_baseline_cost="0.01600000",
        estimated_after_input_tokens=900,
        provider_cached_input_tokens=225,
    )

    provider_call = payload["provider_calls"][0]
    token_usage_by_source = {
        usage["measurement_source"]: usage for usage in provider_call["token_usage"]
    }

    assert payload["request"]["metadata"]["analytics_data_scope"] == "test"
    assert payload["request"]["metadata"]["smoke"] is True
    assert payload["execution"]["tokens_saved"] == -100
    assert payload["execution"]["compression_ratio"] == "1.125"
    assert payload["config"]["strategy_name"] == "dashboard-strategy"
    assert provider_call["provider"] == "dashboard-provider"
    assert provider_call["cost_total"] == "0.03000000"
    assert provider_call["cost_calculations"][0]["total_cost"] == "0.01600000"
    assert token_usage_by_source["provider_reported"]["input_tokens"] == 900
    assert token_usage_by_source["provider_reported"]["cached_input_tokens"] == 225
    assert (
        token_usage_by_source["provider_reported"]["newly_processed_input_tokens"]
        == 675
    )
    assert token_usage_by_source["estimated_before"]["input_tokens"] == 800
    assert token_usage_by_source["estimated_after"]["input_tokens"] == 900
