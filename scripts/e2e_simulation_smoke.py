from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import asyncpg
import httpx
from e2e_analytics_smoke import _payload

DEFAULT_BACKEND_URL = "http://127.0.0.1:8010"
DEFAULT_DB_DSN = "postgresql://analytics:analytics@127.0.0.1:55432/analytics"


def _simulation_payload(marker: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _payload(marker)
    suffix = marker.rsplit("-", 1)[-1]
    provider = f"simulation-provider-{suffix}"
    model = f"simulation-model-{suffix}"
    strategy = f"simulation-source-strategy-{suffix}"
    payload["event"]["source"] = "simulation-e2e-smoke"
    payload["request"]["provider_hint"] = provider
    payload["request"]["model_hint"] = model
    payload["config"]["strategy_name"] = strategy
    payload["provider_calls"][0]["provider"] = provider
    payload["provider_calls"][0]["model"] = model
    command = {
        "simulation_key": f"{marker}-simulation",
        "name": f"{marker} replay",
        "strategy_name": "alternate-ratio-smoke",
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
            "limit": 5,
        },
    }
    return payload, command


async def _db_spot_check(dsn: str, simulation_key: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            """
            SELECT
              sr.status,
              count(res.id)::int AS result_count,
              sum(res.simulated_tokens_saved)::int AS simulated_tokens_saved,
              bool_and(e.is_simulated = false) AS production_unchanged
            FROM simulation_runs sr
            JOIN simulation_results res ON res.simulation_run_id = sr.id
            JOIN compression_executions e ON e.id = res.source_execution_id
            WHERE sr.simulation_key = $1
            GROUP BY sr.id
            """,
            simulation_key,
        )
    finally:
        await conn.close()


def _fail(message: str) -> int:
    print(f"simulation_smoke=failed {message}", file=sys.stderr)
    return 1


async def main() -> int:
    backend_url = os.environ.get("ANALYTICS_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip(
        "/"
    )
    db_dsn = os.environ.get("ANALYTICS_POSTGRES_DSN", DEFAULT_DB_DSN)
    marker = os.environ.get(
        "SIMULATION_SMOKE_MARKER", f"simulation-smoke-{int(time.time())}"
    )
    ingest_payload, simulation_command = _simulation_payload(marker)
    simulation_key = simulation_command["simulation_key"]
    request_key = f"{marker}-request"

    async with httpx.AsyncClient(timeout=20.0) as client:
        ready = await client.get(f"{backend_url}/ready")
        ingest = await client.post(
            f"{backend_url}/ingest/compression", json=ingest_payload
        )
        run = await client.post(
            f"{backend_url}/simulations/runs", json=simulation_command
        )
        duplicate = await client.post(
            f"{backend_url}/simulations/runs", json=simulation_command
        )
        detail = await client.get(f"{backend_url}/simulations/runs/{simulation_key}")
        runs = await client.get(f"{backend_url}/simulations/runs", params={"limit": 5})
        record_detail = await client.get(
            f"{backend_url}/records/compression/{request_key}"
        )

    responses = [ready, ingest, run, duplicate, detail, runs, record_detail]
    if any(response.status_code >= 400 for response in responses):
        return _fail(
            "bad_status "
            f"ready={ready.status_code} ingest={ingest.status_code} "
            f"run={run.status_code} duplicate={duplicate.status_code} "
            f"detail={detail.status_code} runs={runs.status_code} "
            f"record_detail={record_detail.status_code}"
        )

    run_data = run.json()
    duplicate_data = duplicate.json()
    detail_data = detail.json()
    record_data = record_detail.json()
    db_row = await _db_spot_check(db_dsn, simulation_key)
    if db_row is None:
        return _fail("db_spot_check_missing")

    result = detail_data["results"][0]
    production_execution = record_data["executions"][0]
    if run_data["result_count"] != 1 or detail_data["result_count"] != 1:
        return _fail("unexpected_result_count")
    if not duplicate_data["duplicate"] or duplicate_data["result_count"] != 1:
        return _fail("duplicate_not_idempotent")
    if result["simulated_compressed_tokens"] != 600:
        return _fail("simulated_compressed_tokens_mismatch")
    if result["simulated_tokens_saved"] != 600:
        return _fail("simulated_tokens_saved_mismatch")
    if production_execution["tokens_saved"] != 450:
        return _fail("production_execution_mutated")
    if db_row["status"] != "succeeded" or db_row["result_count"] != 1:
        return _fail("db_simulation_status_mismatch")
    if not db_row["production_unchanged"]:
        return _fail("db_production_not_actual")

    print(
        "simulation_smoke=ok "
        f"marker={marker} simulation_key={simulation_key} "
        f"result_count={detail_data['result_count']} "
        f"simulated_tokens_saved={result['simulated_tokens_saved']} "
        f"production_tokens_saved={production_execution['tokens_saved']} "
        f"duplicate={duplicate_data['duplicate']} "
        f"db_results={db_row['result_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
