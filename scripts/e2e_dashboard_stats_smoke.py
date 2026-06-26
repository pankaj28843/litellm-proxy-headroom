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
SENSITIVE_CONTENT = "compressed analytics smoke content"


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
    provider_cached_input_tokens: int = 0,
    status: str = "succeeded",
    tenant_id: str | None = None,
    team_id: str | None = None,
) -> dict[str, Any]:
    payload = _payload(marker)
    started = datetime.now(UTC).replace(microsecond=0)
    ended = started + timedelta(milliseconds=duration_ms + 50)
    tokens_saved = original_tokens - compressed_tokens
    ratio = compressed_tokens / original_tokens
    payload["event"]["source"] = "dashboard-stats-e2e-smoke"
    payload["request"]["provider_hint"] = provider
    payload["request"]["model_hint"] = model
    payload["request"]["tenant_id"] = tenant_id
    payload["request"]["team_id"] = team_id
    payload["request"]["started_at"] = started.isoformat()
    payload["request"]["ended_at"] = ended.isoformat()
    payload["config"]["strategy_name"] = strategy
    payload["execution"]["status"] = status
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
    provider_cached_input_tokens = min(provider_cached_input_tokens, compressed_tokens)
    provider_uncached_input_tokens = compressed_tokens - provider_cached_input_tokens
    provider_call["token_usage"] = [
        {
            "measurement_source": "provider_reported",
            "input_tokens": compressed_tokens,
            "cached_input_tokens": provider_cached_input_tokens,
            "newly_processed_input_tokens": provider_uncached_input_tokens,
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


def _simulation_command(
    marker: str,
    *,
    provider: str,
    model: str,
    strategy: str,
    tenant_id: str,
    team_id: str,
) -> dict[str, Any]:
    return {
        "simulation_key": f"{marker}-simulation",
        "name": f"{marker} dashboard replay",
        "strategy_name": "dashboard-smoke-ratio",
        "config_overrides": {"compression_ratio": "0.5"},
        "pricing_overrides": {
            "input_token_rate": "0.000001",
            "cached_input_token_rate": "0.0000002",
            "cache_write_token_rate": "0.0000005",
            "output_token_rate": "0.000002",
            "reasoning_token_rate": "0.000003",
        },
        "selected_filter": {
            "provider": provider,
            "model": model,
            "strategy": strategy,
            "tenant_id": tenant_id,
            "team_id": team_id,
            "limit": 5,
        },
    }


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
            ),
            selected_chunks AS (
              SELECT ch.id
              FROM compression_chunks ch
              JOIN selected_executions e ON e.id = ch.execution_id
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
              (SELECT count(*)::int FROM selected_executions
                WHERE status = 'failed') AS failed_count,
              (SELECT count(*)::int
                FROM chunk_retrieval_events cre
                JOIN selected_chunks ch ON ch.id = cre.chunk_id) AS retrievals,
              (SELECT sum(cost_total)::text FROM selected_calls) AS measured_cost,
              (SELECT sum(cc.total_cost)::text
                FROM cost_calculations cc
                JOIN selected_calls sc ON sc.id = cc.provider_call_id
                WHERE cc.calculation_kind = 'estimated') AS estimated_cost,
              (SELECT sum(tu.input_tokens)::int
                FROM token_usage_breakdowns tu
                JOIN selected_calls sc ON sc.id = tu.provider_call_id
                WHERE tu.measurement_source = 'provider_reported') AS provider_input,
              (SELECT sum(tu.cached_input_tokens)::int
                FROM token_usage_breakdowns tu
                JOIN selected_calls sc ON sc.id = tu.provider_call_id
                WHERE tu.measurement_source = 'provider_reported') AS provider_cached,
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
    tenant_id = f"dashboard-tenant-{suffix}"
    team_id = f"dashboard-team-{suffix}"
    secondary_provider = f"dashboard-secondary-provider-{suffix}"
    secondary_model = f"dashboard-secondary-model-{suffix}"
    secondary_strategy = f"dashboard-secondary-strategy-{suffix}"
    filters = {
        "data_scope": "test",
        "preset": "all",
        "provider": provider,
        "model": model,
        "strategy": strategy,
        "tenant_id": tenant_id,
        "team_id": team_id,
    }
    simulation_command = _simulation_command(
        marker,
        provider=provider,
        model=model,
        strategy=strategy,
        tenant_id=tenant_id,
        team_id=team_id,
    )
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
            provider_cached_input_tokens=240,
            tenant_id=tenant_id,
            team_id=team_id,
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
            provider_cached_input_tokens=450,
            tenant_id=tenant_id,
            team_id=team_id,
        ),
        _record_payload(
            f"{marker}-failed",
            provider=provider,
            model=model,
            strategy=strategy,
            original_tokens=500,
            compressed_tokens=500,
            duration_ms=80,
            provider_cost="0.00800000",
            estimated_baseline_cost="0.01000000",
            estimated_after_input_tokens=500,
            provider_cached_input_tokens=50,
            status="failed",
            tenant_id=tenant_id,
            team_id=team_id,
        ),
        _record_payload(
            f"{marker}-secondary",
            provider=secondary_provider,
            model=secondary_model,
            strategy=secondary_strategy,
            original_tokens=700,
            compressed_tokens=350,
            duration_ms=90,
            provider_cost="0.00600000",
            estimated_baseline_cost="0.01400000",
            estimated_after_input_tokens=360,
            tenant_id=tenant_id,
            team_id=team_id,
        ),
    ]

    async with httpx.AsyncClient(timeout=20.0) as client:
        ready = await client.get(f"{backend_url}/ready")
        ingests = [
            await client.post(f"{backend_url}/ingest/compression", json=payload)
            for payload in payloads
        ]
        retrieval = await client.get(
            f"{backend_url}/chunks/{marker}-positive-ccr",
            params={"source": "dashboard-smoke"},
        )
        simulation = await client.post(
            f"{backend_url}/simulations/runs",
            json=simulation_command,
        )
        dashboard = await client.get(
            f"{backend_url}/stats/dashboard",
            params=filters,
        )
        breakdown = await client.get(
            f"{backend_url}/stats/breakdown",
            params={**filters, "group_by": "provider"},
        )
        unfiltered_provider_breakdown = await client.get(
            f"{backend_url}/stats/breakdown",
            params={"data_scope": "test", "group_by": "provider", "limit": 25},
        )
        records = await client.get(
            f"{backend_url}/records/compression",
            params={**filters, "limit": 10},
        )
        dashboard_html = await client.get(f"{backend_url}/dashboard", params=filters)
        partial_live = await client.get(
            f"{backend_url}/dashboard/partials/live",
            params=filters,
        )
        partial_records = await client.get(
            f"{backend_url}/dashboard/partials/records",
            params=filters,
        )
        partial_simulations = await client.get(
            f"{backend_url}/dashboard/partials/simulations",
            params=filters,
        )
        static_css = await client.get(f"{backend_url}/dashboard/static/dashboard.css")
        simulation_runs = await client.get(
            f"{backend_url}/simulations/runs",
            params={"limit": 10},
        )
        metrics = await client.get(f"{backend_url}/metrics")

    if ready.status_code >= 400 or any(
        response.status_code >= 400 for response in ingests
    ):
        return _fail(
            f"bad_ingest_status ready={ready.status_code} "
            f"ingests={[response.status_code for response in ingests]}"
        )
    checked_responses = {
        "retrieval": retrieval,
        "simulation": simulation,
        "dashboard": dashboard,
        "breakdown": breakdown,
        "unfiltered_provider_breakdown": unfiltered_provider_breakdown,
        "records": records,
        "dashboard_html": dashboard_html,
        "partial_live": partial_live,
        "partial_records": partial_records,
        "partial_simulations": partial_simulations,
        "static_css": static_css,
        "simulation_runs": simulation_runs,
        "metrics": metrics,
    }
    bad = {
        name: response.status_code
        for name, response in checked_responses.items()
        if response.status_code >= 400
    }
    if bad:
        return _fail(
            "bad_status "
            + " ".join(f"{name}={status}" for name, status in sorted(bad.items()))
        )

    db_row = await _db_spot_check(db_dsn, provider)
    if db_row is None:
        return _fail("db_spot_check_missing")
    data = dashboard.json()
    provider_delta = data["provider_estimate_delta"]
    provider_cache = data["provider_cache"]
    cost = data["cost"]
    distribution = data["savings_distribution"]
    usefulness = data["usefulness"]
    records_data = records.json()
    simulation_data = simulation.json()
    simulation_runs_data = simulation_runs.json()
    provider_values = {
        row["value"] for row in unfiltered_provider_breakdown.json()["rows"]
    }
    html_surfaces = {
        "dashboard_html": dashboard_html.text,
        "partial_live": partial_live.text,
        "partial_records": partial_records.text,
        "partial_simulations": partial_simulations.text,
    }

    if data["requests"] != 3 or data["executions"] != 3:
        return _fail(f"unexpected_counts dashboard={data}")
    if data["tokens_saved"] != db_row["tokens_saved"] or data["tokens_saved"] != 300:
        return _fail("tokens_saved_mismatch")
    if data["negative_savings_executions"] != 1:
        return _fail("negative_savings_missing")
    if data["failed_executions"] != 1 or db_row["failed_count"] != 1:
        return _fail("failed_execution_missing")
    if data["retrievals"] != db_row["retrievals"] or data["retrievals"] < 1:
        return _fail("retrieval_count_mismatch")
    if (
        distribution["min_tokens_saved"] != -100
        or distribution["max_tokens_saved"] != 400
    ):
        return _fail("distribution_bounds_mismatch")
    if provider_delta["provider_reported_input_tokens"] != db_row["provider_input"]:
        return _fail("provider_input_mismatch")
    if (
        provider_cache["provider_reported_cached_input_tokens"]
        != db_row["provider_cached"]
    ):
        return _fail("provider_cached_input_mismatch")
    if provider_cache["provider_reported_cached_input_tokens"] != 740:
        return _fail("provider_cached_input_unexpected")
    if provider_cache["provider_reported_uncached_input_tokens"] != 1260:
        return _fail("provider_uncached_input_unexpected")
    if provider_cache["provider_cache_hit_percent"] != 37.0:
        return _fail("provider_cache_hit_mismatch")
    if provider_cache["cached_input_cost_multiplier"] != "0.10":
        return _fail("cached_input_multiplier_mismatch")
    if provider_cache["billing_equivalent_input_tokens"] != 1334.0:
        return _fail("billing_equivalent_input_mismatch")
    if provider_cache["billing_equivalent_savings_percent"] != 42.0:
        return _fail("billing_equivalent_savings_mismatch")
    if (
        usefulness["status"] != "unproven"
        or usefulness["cache_evidence_scope"]
        != "whole Codex turn/provider-call sequence"
    ):
        return _fail("usefulness_status_mismatch")
    if provider_delta["estimated_before_provider_input_delta"] != 300:
        return _fail("estimated_before_delta_mismatch")
    if provider_delta["estimated_after_provider_input_delta"] != 20:
        return _fail("estimated_after_delta_mismatch")
    if cost["measured_provider_cost_total"] != db_row["measured_cost"]:
        return _fail("measured_cost_mismatch")
    if cost["estimated_baseline_cost_total"] != db_row["estimated_cost"]:
        return _fail("estimated_cost_mismatch")
    if cost["estimated_cost_savings"] != "-0.00200000":
        return _fail("estimated_cost_savings_mismatch")
    if cost["cost_increase_provider_calls"] != 1:
        return _fail("cost_increase_count_mismatch")
    if breakdown.json()["rows"][0]["value"] != provider:
        return _fail("breakdown_provider_missing")
    if provider not in provider_values or secondary_provider not in provider_values:
        return _fail("multiple_provider_breakdown_missing")
    if (
        records_data["total"] != 3
        or records_data["items"][0]["request_key"] not in dashboard_html.text
    ):
        return _fail("records_dashboard_mismatch")
    if simulation_data["simulation_key"] != simulation_command["simulation_key"]:
        return _fail("simulation_key_mismatch")
    if not any(
        item["simulation_key"] == simulation_command["simulation_key"]
        for item in simulation_runs_data["items"]
    ):
        return _fail("simulation_run_list_missing")
    for surface_name, html in html_surfaces.items():
        if (
            f"{marker}-positive-request" not in html
            and surface_name != "partial_simulations"
        ):
            return _fail(f"{surface_name}_missing_marker_record")
        if surface_name in {"dashboard_html", "partial_live"} and (
            "Provider cache hit" not in html
            or "Local token delta" not in html
            or "Billing input estimate" not in html
        ):
            return _fail(f"{surface_name}_missing_provider_cache_metrics")
        if surface_name in {"dashboard_html", "partial_live"} and (
            "Primary usefulness unproven" not in html
            or "whole Codex turn/provider-call sequence" not in html
        ):
            return _fail(f"{surface_name}_missing_usefulness_status")
        if SENSITIVE_CONTENT in html or "provider_shape" in html:
            return _fail(f"{surface_name}_leaked_sensitive_content")
    if simulation_command["simulation_key"] not in partial_simulations.text:
        return _fail("partial_simulations_missing_marker")
    if "--surface" not in static_css.text:
        return _fail("static_css_missing_dashboard_rules")
    if "litellm_proxy_analytics_tokens_saved_total" not in metrics.text:
        return _fail("metrics_missing_tokens_saved")

    print(
        "dashboard_stats_smoke=ok "
        f"marker={marker} provider={provider} "
        f"requests={data['requests']} tokens_saved={data['tokens_saved']} "
        f"negative_savings={data['negative_savings_executions']} "
        f"failed={data['failed_executions']} retrievals={data['retrievals']} "
        f"provider_delta_after={provider_delta['estimated_after_provider_input_delta']} "
        f"provider_cache_hit={provider_cache['provider_cache_hit_percent']} "
        f"billing_input_delta={provider_cache['billing_equivalent_savings_percent']} "
        f"cost_delta={cost['estimated_cost_savings']} "
        f"min_saved={distribution['min_tokens_saved']} "
        f"max_saved={distribution['max_tokens_saved']} "
        f"records_total={records_data['total']} "
        f"simulation_key={simulation_command['simulation_key']} "
        f"db_retrievals={db_row['retrievals']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
