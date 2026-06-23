from decimal import Decimal
from types import SimpleNamespace

from litellm_proxy_headroom.analytics.adapters.litellm.usage_mapping import (
    provider_response_metadata,
    response_cost,
    token_usage_from_response,
)


def test_litellm_usage_mapping_normalizes_cache_reasoning_and_cost() -> None:
    response = SimpleNamespace(
        id="provider-response-1",
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 150,
            "total_tokens": 1150,
            "prompt_tokens_details": {
                "cached_tokens": 250,
                "cache_creation_tokens": 75,
            },
            "completion_tokens_details": {"reasoning_tokens": 40},
        },
        _hidden_params={
            "response_cost": 0.012345,
            "api_key": "should-not-leak",
        },
    )

    usage = token_usage_from_response(response)

    assert usage is not None
    assert usage.input_tokens == 1000
    assert usage.cached_input_tokens == 250
    assert usage.newly_processed_input_tokens == 750
    assert usage.cache_write_tokens == 75
    assert usage.output_tokens == 150
    assert usage.reasoning_tokens == 40
    assert response_cost(response) == Decimal("0.012345")
    assert provider_response_metadata(response)["_hidden_params"]["api_key"] == (
        "[REDACTED]"
    )


def test_response_cost_ignores_missing_or_invalid_cost() -> None:
    assert response_cost({}) is None
    assert response_cost({"response_cost": "not-a-number"}) is None
