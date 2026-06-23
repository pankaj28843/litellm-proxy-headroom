from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
from e2e_analytics_smoke import _payload

DEFAULT_BACKEND_URL = "http://127.0.0.1:8010"
DEFAULT_DB_DSN = "postgresql://analytics:analytics@127.0.0.1:55432/analytics"


def _record_payload(
    marker: str,
    *,
    provider: str,
    model: str,
    strategy: str,
    original_tokens: int,
    compressed_tokens: int,
    duration_ms: int,
    provider_cost: str,
    estimated_baseline_cost: str,
    estimated_after_input_tokens: int,
) -> dict[str, Any]:
    payload = _payload(marker)
    started = datetime.now(UTC).replace(microsecond=0)
    ended = started + timedelta(milliseconds=duration_ms + 50)
    tokens_saved = original_tokens - compressed_tokens
    ratio = compressed_tokens / original_tokens
    payload["event"]["source"] = "dashboard-stats-e2e-smoke"
    payload["request"]["provider_hint"] = provider
    payload["request"]["model_hint"] = model
    payload["request"]["started_at"] = started.isoformat()
    payload["request"]["ended_at"] = ended.isoformat()
    payload["config"]["strategy_name"] = strategy
    payload["execution"]["duration_ms"] = duration_ms
    payload["execution"]["original_tokens"] = original_tokens
    payload["execution"]["compressed_tokens"] = compressed_tokens
    payload["execution"]["tokens_saved"] = tokens_saved
    payload["execution"]["compression_ratio"] = str(round(ratio, 6))
    payload["chunks"][0]["original_tokens"] = original_tokens
    payload["chunks"][0]["compressed_tokens"] = compressed_tokens
    provider_call = payload["provider_calls"][0]
    provider_call["provider"] = provider
    provider_call["model"] = model
    provider_call["cost_total"] = provider_cost
    provider_call["currency"] = "USD"
    provider_call["token_usage"] = [
        {
            "measurement_source": "provider_reported",
            "input_tokens": compressed_tokens,
            "cached_input_tokens": 0,
            "newly_processed_input_tokens": compressed_tokens,
            "cache_write_tokens": 0,
            "output_tokens": 40,
            "reasoning_tokens": 5,
            "total_tokens": compressed_tokens + 45,
            "raw_usage": {"shape": "dashboard-smoke-provider"},
        },
        {
            "measurement_source": "estimated_before",
            "input_tokens": original_tokens,
            "total_tokens": original_tokens + 45,
        },
        {
            "measurement_source": "estimated_after",
            "input_tokens": estimated_after_input_tokens,
            "total_tokens": estimated_after_input_tokens + 45,
        },
    ]
    provider_call["cost_calculations"] = [
        {
            "calculation_kind": "estimated",
            "total_cost": estimated_baseline_cost,
            "currency": "USD",
            "assumptions": {"scenario": "uncompressed_baseline"},
        }
    ]
    payload["cache_activities"][0]["tokens_read"] = 10
    payload["cache_activities"][1]["tokens_written"] = compressed_tokens
    return payload


async def _db_spot_check(dsn: str, provider: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            """
            WITH selected_calls AS (
              SELECT pc.id, pc.execution_id, pc.cost_total
              FROM provider_calls pc
              WHERE pc.provider = $1
            ),
            selected_executions AS (
              SELECT DISTINCT e.*
              FROM compression_executions e
              JOIN selected_calls sc ON sc.execution_id = e.id
            ),
            selected_requests AS (
              SELECT DISTINCT r.id
              FROM compression_requests r
              JOIN selected_executions e ON e.request_id = r.id
            )
            SELECT
              (SELECT count(*)::int FROM selected_requests) AS requests,
              (SELECT count(*)::int FROM selected_executions) AS executions,
              (SELECT sum(original_tokens)::int FROM selected_executions)
                AS original_tokens,
              (SELECT sum(compressed_tokens)::int FROM selected_executions)
                AS compressed_tokens,
              (SELECT sum(tokens_saved)::int FROM selected_executions)
                AS tokens_saved,
              (SELECT count(*)::int FROM selected_executions
                WHERE tokens_saved < 0) AS negative_count,
              (SELECT sum(cost_total)::text FROM selected_calls) AS measured_cost,
              (SELECT sum(cc.total_cost)::text
                FROM cost_calculations cc
                JOIN selected_calls sc ON sc.id = cc.provider_call_id
                WHERE cc.calculation_kind = 'estimated') AS estimated_cost,
              (SELECT sum(tu.input_tokens)::int
                FROM token_usage_breakdowns tu
                JOIN selected_calls sc ON sc.id = tu.provider_call_id
                WHERE tu.measurement_source = 'provider_reported') AS provider_input,
              (SELECT sum(tu.input_tokens)::int
                FROM token_usage_breakdowns tu
                JOIN selected_calls sc ON sc.id = tu.provider_call_id
                WHERE tu.measurement_source = 'estimated_before')
                AS estimated_before_input,
              (SELECT sum(tu.input_tokens)::int
                FROM token_usage_breakdowns tu
                JOIN selected_calls sc ON sc.id = tu.provider_call_id
                WHERE tu.measurement_source = 'estimated_after')
                AS estimated_after_input
            """,
            provider,
        )
    finally:
        await conn.close()


def _fail(message: str) -> int:
    print(f"dashboard_stats_smoke=failed {message}", file=sys.stderr)
    return 1


async def main() -> int:
    backend_url = os.environ.get("ANALYTICS_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip(
        "/"
    )
    db_dsn = os.environ.get("ANALYTICS_POSTGRES_DSN", DEFAULT_DB_DSN)
    marker = os.environ.get(
        "DASHBOARD_STATS_SMOKE_MARKER",
        f"dashboard-stats-smoke-{int(time.time())}",
    )
    suffix = marker.rsplit("-", 1)[-1]
    provider = f"dashboard-provider-{suffix}"
    model = f"dashboard-model-{suffix}"
    strategy = f"dashboard-strategy-{suffix}"
    payloads = [
        _record_payload(
            f"{marker}-positive",
            provider=provider,
            model=model,
            strategy=strategy,
            original_tokens=1000,
            compressed_tokens=600,
            duration_ms=100,
            provider_cost="0.01000000",
            estimated_baseline_cost="0.02000000",
            estimated_after_input_tokens=620,
        ),
        _record_payload(
            f"{marker}-negative",
            provider=provider,
            model=model,
            strategy=strategy,
            original_tokens=800,
            compressed_tokens=900,
            duration_ms=200,
            provider_cost="0.03000000",
            estimated_baseline_cost="0.01600000",
            estimated_after_input_tokens=900,
        ),
    ]

    async with httpx.AsyncClient(timeout=20.0) as client:
        ready = await client.get(f"{backend_url}/ready")
        ingests = [
            await client.post(f"{backend_url}/ingest/compression", json=payload)
            for payload in payloads
        ]
        dashboard = await client.get(
            f"{backend_url}/stats/dashboard",
            params={"provider": provider, "model": model, "strategy": strategy},
        )
        breakdown = await client.get(
            f"{backend_url}/stats/breakdown",
            params={
                "provider": provider,
                "model": model,
                "strategy": strategy,
                "group_by": "provider",
            },
        )

    if ready.status_code >= 400 or any(
        response.status_code >= 400 for response in ingests
    ):
        return _fail(
            f"bad_ingest_status ready={ready.status_code} "
            f"ingests={[response.status_code for response in ingests]}"
        )
    if dashboard.status_code >= 400 or breakdown.status_code >= 400:
        return _fail(
            f"bad_stats_status dashboard={dashboard.status_code} "
            f"breakdown={breakdown.status_code}"
        )

    db_row = await _db_spot_check(db_dsn, provider)
    if db_row is None:
        return _fail("db_spot_check_missing")
    data = dashboard.json()
    provider_delta = data["provider_estimate_delta"]
    cost = data["cost"]
    distribution = data["savings_distribution"]

    if data["requests"] != 2 or data["executions"] != 2:
        return _fail(f"unexpected_counts dashboard={data}")
    if data["tokens_saved"] != db_row["tokens_saved"] or data["tokens_saved"] != 300:
        return _fail("tokens_saved_mismatch")
    if data["negative_savings_executions"] != 1:
        return _fail("negative_savings_missing")
    if (
        distribution["min_tokens_saved"] != -100
        or distribution["max_tokens_saved"] != 400
    ):
        return _fail("distribution_bounds_mismatch")
    if provider_delta["provider_reported_input_tokens"] != db_row["provider_input"]:
        return _fail("provider_input_mismatch")
    if provider_delta["estimated_before_provider_input_delta"] != 300:
        return _fail("estimated_before_delta_mismatch")
    if provider_delta["estimated_after_provider_input_delta"] != 20:
        return _fail("estimated_after_delta_mismatch")
    if cost["measured_provider_cost_total"] != db_row["measured_cost"]:
        return _fail("measured_cost_mismatch")
    if cost["estimated_baseline_cost_total"] != db_row["estimated_cost"]:
        return _fail("estimated_cost_mismatch")
    if cost["estimated_cost_savings"] != "-0.00400000":
        return _fail("estimated_cost_savings_mismatch")
    if cost["cost_increase_provider_calls"] != 1:
        return _fail("cost_increase_count_mismatch")
    if breakdown.json()["rows"][0]["value"] != provider:
        return _fail("breakdown_provider_missing")

    print(
        "dashboard_stats_smoke=ok "
        f"marker={marker} provider={provider} "
        f"requests={data['requests']} tokens_saved={data['tokens_saved']} "
        f"negative_savings={data['negative_savings_executions']} "
        f"provider_delta_after={provider_delta['estimated_after_provider_input_delta']} "
        f"cost_savings={cost['estimated_cost_savings']} "
        f"min_saved={distribution['min_tokens_saved']} "
        f"max_saved={distribution['max_tokens_saved']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
