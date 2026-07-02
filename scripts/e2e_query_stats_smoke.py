from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import asyncpg
import httpx
from e2e_analytics_smoke import _payload

DEFAULT_BACKEND_URL = "http://127.0.0.1:28010"
DEFAULT_DB_DSN = "postgresql://analytics:analytics@127.0.0.1:55432/analytics"
SENSITIVE_CONTENT = "compressed analytics smoke content"


def _custom_payload(marker: str) -> tuple[dict[str, Any], dict[str, str]]:
    payload = _payload(marker)
    suffix = marker.rsplit("-", 1)[-1]
    provider = f"query-smoke-provider-{suffix}"
    model = f"gpt-query-smoke-{suffix}"
    strategy = f"query-smoke-strategy-{suffix}"
    payload["event"]["source"] = "query-stats-e2e-smoke"
    payload["request"]["provider_hint"] = provider
    payload["request"]["model_hint"] = model
    payload["config"]["strategy_name"] = strategy
    payload["provider_calls"][0]["provider"] = provider
    payload["provider_calls"][0]["model"] = model
    return payload, {"provider": provider, "model": model, "strategy": strategy}


async def _db_spot_check(dsn: str, request_key: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            """
            SELECT
              e.original_tokens,
              e.compressed_tokens,
              e.tokens_saved,
              pc.provider,
              pc.model,
              tub.input_tokens,
              tub.cached_input_tokens,
              tub.output_tokens,
              tub.reasoning_tokens,
              count(DISTINCT ch.id)::int AS chunks
            FROM compression_requests r
            JOIN compression_executions e ON e.request_id = r.id
            JOIN provider_calls pc ON pc.execution_id = e.id
            JOIN token_usage_breakdowns tub ON tub.provider_call_id = pc.id
            LEFT JOIN compression_chunks ch ON ch.execution_id = e.id
            WHERE r.request_key = $1
            GROUP BY e.id, pc.id, tub.id
            """,
            request_key,
        )
    finally:
        await conn.close()


def _fail(message: str) -> int:
    print(f"query_stats_smoke=failed {message}", file=sys.stderr)
    return 1


async def main() -> int:
    backend_url = os.environ.get("ANALYTICS_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip(
        "/"
    )
    db_dsn = os.environ.get("ANALYTICS_POSTGRES_DSN", DEFAULT_DB_DSN)
    marker = os.environ.get(
        "QUERY_STATS_SMOKE_MARKER", f"query-stats-smoke-{int(time.time())}"
    )
    payload, filters = _custom_payload(marker)
    filters = {**filters, "data_scope": "test"}
    request_key = f"{marker}-request"

    async with httpx.AsyncClient(timeout=20.0) as client:
        health = await client.get(f"{backend_url}/health")
        ready = await client.get(f"{backend_url}/ready")
        ingest = await client.post(f"{backend_url}/ingest/compression", json=payload)
        stats = await client.get(f"{backend_url}/stats", params=filters)
        records = await client.get(
            f"{backend_url}/records/compression",
            params={**filters, "limit": 10},
        )
        detail = await client.get(f"{backend_url}/records/compression/{request_key}")
        breakdown = await client.get(
            f"{backend_url}/stats/breakdown",
            params={**filters, "group_by": "provider"},
        )
        metrics = await client.get(f"{backend_url}/metrics")

    responses = [health, ready, ingest, stats, records, detail, breakdown, metrics]
    if any(response.status_code >= 400 for response in responses):
        return _fail(
            "bad_status "
            f"health={health.status_code} ready={ready.status_code} "
            f"ingest={ingest.status_code} stats={stats.status_code} "
            f"records={records.status_code} detail={detail.status_code} "
            f"breakdown={breakdown.status_code} metrics={metrics.status_code}"
        )

    db_row = await _db_spot_check(db_dsn, request_key)
    if db_row is None:
        return _fail("db_spot_check_missing")

    stats_data = stats.json()
    records_data = records.json()
    detail_data = detail.json()
    breakdown_data = breakdown.json()

    if stats_data["requests"] != 1 or stats_data["executions"] != 1:
        return _fail(f"unexpected_stats_counts stats={stats_data}")
    if stats_data["tokens_saved"] != db_row["tokens_saved"]:
        return _fail("stats_tokens_saved_mismatch")
    if stats_data["provider_input_tokens"] != db_row["input_tokens"]:
        return _fail("stats_provider_input_mismatch")
    if (
        records_data["total"] != 1
        or records_data["items"][0]["request_key"] != request_key
    ):
        return _fail("records_page_mismatch")
    if detail_data["request_key"] != request_key or len(detail_data["executions"]) != 1:
        return _fail("detail_mismatch")
    if (
        SENSITIVE_CONTENT in detail.text
        or "usage_source" in detail.text
        or "provider_shape" in detail.text
    ):
        return _fail("detail_leaked_content_or_raw_metadata")
    breakdown_rows = breakdown_data["rows"]
    if not breakdown_rows or breakdown_rows[0]["value"] != filters["provider"]:
        return _fail("breakdown_provider_missing")
    if "litellm_proxy_analytics_tokens_saved_total" not in metrics.text:
        return _fail("metrics_missing_tokens_saved")

    print(
        "query_stats_smoke=ok "
        f"marker={marker} request_key={request_key} "
        f"requests={stats_data['requests']} tokens_saved={stats_data['tokens_saved']} "
        f"provider_input={stats_data['provider_input_tokens']} "
        f"records_total={records_data['total']} "
        f"breakdown_value={breakdown_rows[0]['value']} "
        f"db_chunks={db_row['chunks']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
