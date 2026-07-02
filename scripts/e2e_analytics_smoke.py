from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

DEFAULT_BACKEND_URL = "http://127.0.0.1:28010"


def _payload(marker: str) -> dict[str, Any]:
    return {
        "event": {
            "source": "analytics-e2e-smoke",
            "event_type": "compression_result",
            "event_key": marker,
            "raw_payload": {"marker": marker, "provider_specific": {"cached": True}},
        },
        "request": {
            "request_key": f"{marker}-request",
            "source_system": "litellm-proxy",
            "incoming_route": "/v1/chat/completions",
            "provider_hint": "openai",
            "model_hint": "gpt-smoke",
            "metadata": {"analytics_data_scope": "test", "smoke": True},
        },
        "config": {
            "config_hash": f"{marker}-config",
            "strategy_name": "smoke-strategy",
            "strategy_version": "1",
            "raw_config": {"threshold": 100},
        },
        "execution": {
            "attempt_number": 1,
            "status": "succeeded",
            "original_tokens": 1200,
            "compressed_tokens": 750,
            "tokens_saved": 450,
            "compression_ratio": "0.625",
        },
        "chunks": [
            {
                "ordinal": 0,
                "ccr_hash": f"{marker}-ccr",
                "content_hash": f"{marker}-content",
                "original_tokens": 1200,
                "compressed_tokens": 750,
                "storage_policy": "plaintext",
                "compressed_content": "compressed analytics smoke content",
                "metadata": {"source": "e2e-smoke"},
            }
        ],
        "provider_calls": [
            {
                "provider_call_key": f"{marker}-provider-call",
                "execution_attempt": 1,
                "provider": "openai",
                "model": "gpt-smoke",
                "status": "succeeded",
                "provider_request_id": f"{marker}-provider-request",
                "provider_response_id": f"{marker}-provider-response",
                "raw_response_metadata": {"usage_source": "smoke"},
                "token_usage": [
                    {
                        "measurement_source": "provider_reported",
                        "input_tokens": 750,
                        "cached_input_tokens": 120,
                        "newly_processed_input_tokens": 630,
                        "cache_write_tokens": 0,
                        "output_tokens": 42,
                        "reasoning_tokens": 7,
                        "total_tokens": 799,
                        "raw_usage": {"provider_shape": "smoke"},
                    }
                ],
            }
        ],
        "cache_activities": [
            {
                "cache_system": "provider",
                "operation": "read",
                "hit": True,
                "provider_call_key": f"{marker}-provider-call",
                "ccr_hash": f"{marker}-ccr",
                "tokens_read": 120,
            },
            {
                "cache_system": "headroom_ccr",
                "operation": "write",
                "hit": None,
                "ccr_hash": f"{marker}-ccr",
                "tokens_written": 750,
            },
        ],
    }


def _status(response: httpx.Response) -> int:
    return response.status_code


def main() -> int:
    backend_url = os.environ.get("ANALYTICS_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip(
        "/"
    )
    marker = os.environ.get(
        "ANALYTICS_SMOKE_MARKER", f"analytics-smoke-{int(time.time())}"
    )
    payload = _payload(marker)

    with httpx.Client(timeout=20.0) as client:
        health = client.get(f"{backend_url}/health")
        ready = client.get(f"{backend_url}/ready")
        ingest = client.post(f"{backend_url}/ingest/compression", json=payload)
        duplicate = client.post(f"{backend_url}/ingest/compression", json=payload)
        chunk = client.get(f"{backend_url}/chunks/{marker}-ccr")
        stats = client.get(f"{backend_url}/stats", params={"data_scope": "test"})
        metrics = client.get(f"{backend_url}/metrics")
        dashboard = client.get(
            f"{backend_url}/dashboard",
            params={"data_scope": "test"},
        )

    if any(
        response.status_code >= 400
        for response in [
            health,
            ready,
            ingest,
            duplicate,
            chunk,
            stats,
            metrics,
            dashboard,
        ]
    ):
        print(
            "analytics_smoke=failed "
            f"health={_status(health)} ready={_status(ready)} "
            f"ingest={_status(ingest)} duplicate={_status(duplicate)} "
            f"chunk={_status(chunk)} stats={_status(stats)} "
            f"metrics={_status(metrics)} dashboard={_status(dashboard)}",
            file=sys.stderr,
        )
        return 1

    ingest_data = ingest.json()
    duplicate_data = duplicate.json()
    chunk_data = chunk.json()
    stats_data = stats.json()

    if not duplicate_data.get("duplicate"):
        print("analytics_smoke=failed duplicate=false", file=sys.stderr)
        return 1
    if chunk_data.get("ccr_hash") != f"{marker}-ccr":
        print("analytics_smoke=failed chunk_mismatch", file=sys.stderr)
        return 1
    if stats_data.get("tokens_saved", 0) < 450:
        print("analytics_smoke=failed stats_tokens_saved_low", file=sys.stderr)
        return 1
    if "litellm_proxy_analytics_tokens_saved_total" not in metrics.text:
        print("analytics_smoke=failed metrics_missing_tokens_saved", file=sys.stderr)
        return 1

    print(
        "analytics_smoke=ok "
        f"marker={marker} event_id={ingest_data.get('event_id')} "
        f"duplicate={duplicate_data.get('duplicate')} "
        f"chunk={chunk_data.get('ccr_hash')} "
        f"requests={stats_data.get('requests')} "
        f"tokens_saved={stats_data.get('tokens_saved')} "
        f"retrievals={stats_data.get('retrievals')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
