#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

DEFAULT_DSN = "postgresql://analytics:analytics@127.0.0.1:55432/analytics"
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:28010"
DEFAULT_CACHED_INPUT_COST_MULTIPLIER = Decimal("0.10")

TIME_BOUNDS_SQL = "select now() as db_now"

SUMMARY_SQL = """
with filtered_requests as (
  select
    cr.id,
    cr.request_key,
    cr.incoming_route,
    cr.provider_hint,
    cr.model_hint,
    coalesce(cr.started_at, cr.created_at) as request_time,
    cr.request_metadata
  from compression_requests cr
  where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
    and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
    and (
      $3::text is null
      or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
    )
),
filtered_executions as (
  select
    ce.id,
    ce.request_id,
    ce.status,
    ce.original_tokens,
    ce.compressed_tokens,
    ce.tokens_saved,
    ce.duration_ms,
    ce.transforms,
    coalesce(ce.started_at, ce.created_at) as execution_time
  from compression_executions ce
  join filtered_requests fr on fr.id = ce.request_id
  where ce.is_simulated = false
),
filtered_provider_calls as (
  select
    pc.id,
    pc.request_id,
    pc.execution_id,
    pc.provider,
    pc.model,
    pc.status,
    pc.cost_total,
    coalesce(pc.started_at, pc.created_at) as provider_time
  from provider_calls pc
  join filtered_requests fr on fr.id = pc.request_id
),
provider_usage as (
  select
    count(distinct pc.id)::int as provider_calls,
    count(distinct pc.id) filter (where pc.status = 'succeeded')::int
      as provider_calls_succeeded,
    count(distinct tu.id) filter (
      where tu.measurement_source = 'provider_reported'
    )::int as provider_reported_usage_rows,
    coalesce(sum(tu.input_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_input_tokens,
    coalesce(sum(tu.cached_input_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_cached_input_tokens,
    coalesce(sum(tu.newly_processed_input_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_newly_processed_input_tokens,
    coalesce(sum(tu.output_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_output_tokens,
    coalesce(sum(tu.reasoning_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_reasoning_tokens,
    coalesce(sum(tu.total_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_total_tokens,
    sum(pc.cost_total) as provider_cost_total
  from filtered_provider_calls pc
  left join token_usage_breakdowns tu on tu.provider_call_id = pc.id
),
cache_usage as (
  select
    count(*) filter (where ca.operation = 'read')::int as cache_read_events,
    count(*) filter (where ca.operation = 'write')::int as cache_write_events,
    count(*) filter (where ca.hit is true)::int as cache_hit_events,
    coalesce(sum(ca.tokens_read), 0)::bigint as cache_tokens_read,
    coalesce(sum(ca.tokens_written), 0)::bigint as cache_tokens_written
  from cache_activities ca
  join filtered_requests fr on fr.id = ca.request_id
)
select
  (select count(*)::int from filtered_requests) as requests,
  (select count(*)::int from filtered_executions) as executions,
  (select count(*)::int from filtered_executions where status = 'succeeded')
    as executions_succeeded,
  (select count(*)::int from filtered_executions where status = 'skipped')
    as executions_skipped,
  (select count(*)::int from filtered_executions where status = 'failed')
    as executions_failed,
  (select count(*)::int from filtered_executions where coalesce(tokens_saved, 0) < 0)
    as negative_savings_executions,
  (select coalesce(sum(original_tokens), 0)::bigint from filtered_executions)
    as original_tokens,
  (select coalesce(sum(compressed_tokens), 0)::bigint from filtered_executions)
    as compressed_tokens,
  (select coalesce(sum(tokens_saved), 0)::bigint from filtered_executions)
    as tokens_saved,
  (select count(distinct incoming_route)::int from filtered_requests)
    as distinct_routes,
  (select count(distinct provider_hint)::int from filtered_requests)
    as distinct_provider_hints,
  (select count(distinct model_hint)::int from filtered_requests)
    as distinct_model_hints,
  provider_usage.*,
  cache_usage.*
from provider_usage, cache_usage
"""

LATEST_SESSION_SQL = """
with request_events as (
  select
    cr.id,
    coalesce(cr.started_at, cr.created_at) as event_time
  from compression_requests cr
  where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
    and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
    and (
      $3::text is null
      or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
    )
),
ordered_events as (
  select
    id,
    event_time,
    case
      when lag(event_time) over (order by event_time) is null then 1
      when event_time - lag(event_time) over (order by event_time)
        > make_interval(mins => $4::int) then 1
      else 0
    end as starts_new_session
  from request_events
),
sessionized as (
  select
    id,
    event_time,
    sum(starts_new_session) over (order by event_time rows unbounded preceding)
      as session_index
  from ordered_events
)
select
  session_index::int,
  min(event_time) as started_at,
  max(event_time) as ended_at,
  count(*)::int as requests
from sessionized
group by session_index
order by ended_at desc
limit 1
"""

BUCKETS_SQL = """
with filtered_requests as (
  select
    cr.id,
    coalesce(cr.started_at, cr.created_at) as request_time,
    cr.request_metadata
  from compression_requests cr
  where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
    and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
    and (
      $3::text is null
      or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
    )
),
execution_buckets as (
  select
    date_trunc('minute', coalesce(ce.started_at, ce.created_at)) as bucket,
    count(*)::int as executions,
    count(*) filter (where ce.status = 'succeeded')::int as succeeded_executions,
    count(*) filter (where ce.status = 'skipped')::int as skipped_executions,
    count(*) filter (where ce.status = 'failed')::int as failed_executions,
    coalesce(sum(ce.original_tokens), 0)::bigint as original_tokens,
    coalesce(sum(ce.compressed_tokens), 0)::bigint as compressed_tokens,
    coalesce(sum(ce.tokens_saved), 0)::bigint as tokens_saved,
    count(*) filter (where coalesce(ce.tokens_saved, 0) < 0)::int
      as negative_savings_executions
  from compression_executions ce
  join filtered_requests fr on fr.id = ce.request_id
  where ce.is_simulated = false
  group by 1
),
provider_buckets as (
  select
    date_trunc('minute', coalesce(pc.started_at, pc.created_at)) as bucket,
    count(distinct pc.id)::int as provider_calls,
    count(distinct pc.id) filter (where pc.status = 'succeeded')::int
      as provider_calls_succeeded,
    coalesce(sum(tu.input_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_input_tokens,
    coalesce(sum(tu.cached_input_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_cached_input_tokens,
    coalesce(sum(tu.output_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_output_tokens,
    coalesce(sum(tu.reasoning_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_reasoning_tokens,
    coalesce(sum(tu.total_tokens) filter (
      where tu.measurement_source = 'provider_reported'
    ), 0)::bigint as provider_total_tokens
  from provider_calls pc
  join filtered_requests fr on fr.id = pc.request_id
  left join token_usage_breakdowns tu on tu.provider_call_id = pc.id
  group by 1
),
cache_buckets as (
  select
    date_trunc('minute', ca.occurred_at) as bucket,
    count(*) filter (where ca.operation = 'read')::int as cache_read_events,
    count(*) filter (where ca.operation = 'write')::int as cache_write_events,
    count(*) filter (where ca.hit is true)::int as cache_hit_events,
    coalesce(sum(ca.tokens_read), 0)::bigint as cache_tokens_read,
    coalesce(sum(ca.tokens_written), 0)::bigint as cache_tokens_written
  from cache_activities ca
  join filtered_requests fr on fr.id = ca.request_id
  group by 1
),
bucket_index as (
  select bucket from execution_buckets
  union
  select bucket from provider_buckets
  union
  select bucket from cache_buckets
)
select
  bi.bucket,
  coalesce(eb.executions, 0)::int as executions,
  coalesce(eb.succeeded_executions, 0)::int as succeeded_executions,
  coalesce(eb.skipped_executions, 0)::int as skipped_executions,
  coalesce(eb.failed_executions, 0)::int as failed_executions,
  coalesce(eb.original_tokens, 0)::bigint as original_tokens,
  coalesce(eb.compressed_tokens, 0)::bigint as compressed_tokens,
  coalesce(eb.tokens_saved, 0)::bigint as tokens_saved,
  coalesce(eb.negative_savings_executions, 0)::int as negative_savings_executions,
  coalesce(pb.provider_calls, 0)::int as provider_calls,
  coalesce(pb.provider_calls_succeeded, 0)::int as provider_calls_succeeded,
  coalesce(pb.provider_input_tokens, 0)::bigint as provider_input_tokens,
  coalesce(pb.provider_cached_input_tokens, 0)::bigint
    as provider_cached_input_tokens,
  coalesce(pb.provider_output_tokens, 0)::bigint as provider_output_tokens,
  coalesce(pb.provider_reasoning_tokens, 0)::bigint as provider_reasoning_tokens,
  coalesce(pb.provider_total_tokens, 0)::bigint as provider_total_tokens,
  coalesce(cb.cache_read_events, 0)::int as cache_read_events,
  coalesce(cb.cache_write_events, 0)::int as cache_write_events,
  coalesce(cb.cache_hit_events, 0)::int as cache_hit_events,
  coalesce(cb.cache_tokens_read, 0)::bigint as cache_tokens_read,
  coalesce(cb.cache_tokens_written, 0)::bigint as cache_tokens_written
from bucket_index bi
left join execution_buckets eb on eb.bucket = bi.bucket
left join provider_buckets pb on pb.bucket = bi.bucket
left join cache_buckets cb on cb.bucket = bi.bucket
order by bi.bucket
"""

STATUS_SQL = """
with filtered_requests as (
  select cr.id
  from compression_requests cr
  where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
    and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
    and (
      $3::text is null
      or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
    )
)
select
  ce.status,
  coalesce(ce.transforms->>'skip_reason', '') as skip_reason,
  count(*)::int as executions,
  coalesce(sum(ce.original_tokens), 0)::bigint as original_tokens,
  coalesce(sum(ce.compressed_tokens), 0)::bigint as compressed_tokens,
  coalesce(sum(ce.tokens_saved), 0)::bigint as tokens_saved
from compression_executions ce
join filtered_requests fr on fr.id = ce.request_id
where ce.is_simulated = false
group by ce.status, coalesce(ce.transforms->>'skip_reason', '')
order by executions desc, ce.status, skip_reason
"""

AFFINITY_SQL = """
select
  coalesce(cr.request_metadata->>'provider_session_affinity_source', '') as source,
  coalesce(cr.request_metadata->>'provider_session_affinity_hash', '') as hash,
  count(*)::int as requests,
  min(coalesce(cr.started_at, cr.created_at)) as first_seen,
  max(coalesce(cr.started_at, cr.created_at)) as last_seen
from compression_requests cr
where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
  and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
  and (
    $3::text is null
    or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
  )
group by 1, 2
order by requests desc, last_seen desc
limit 20
"""

RECENT_REQUESTS_SQL = """
select
  cr.request_key,
  coalesce(cr.started_at, cr.created_at) as request_time,
  cr.incoming_route,
  cr.provider_hint,
  cr.model_hint,
  coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') as client,
  coalesce(cr.request_metadata->>'litellm_proxy_project', '') as project,
  coalesce(cr.request_metadata->>'litellm_proxy_run_marker', '') as run_marker,
  coalesce(cr.request_metadata->>'provider_session_affinity_source', '') as
    affinity_source,
  coalesce(cr.request_metadata->>'provider_session_affinity_hash', '') as
    affinity_hash,
  ce.status as compression_status,
  ce.original_tokens,
  ce.compressed_tokens,
  ce.tokens_saved,
  coalesce(ce.transforms->>'skip_reason', '') as skip_reason,
  pc.status as provider_status,
  pc.provider,
  pc.model as provider_model,
  tu.input_tokens as provider_input_tokens,
  tu.cached_input_tokens as provider_cached_input_tokens,
  tu.output_tokens as provider_output_tokens,
  tu.reasoning_tokens as provider_reasoning_tokens,
  tu.total_tokens as provider_total_tokens
from compression_requests cr
left join compression_executions ce on ce.request_id = cr.id
left join provider_calls pc on pc.request_id = cr.id
left join token_usage_breakdowns tu
  on tu.provider_call_id = pc.id
  and tu.measurement_source = 'provider_reported'
where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
  and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
  and (
    $3::text is null
    or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
  )
order by coalesce(cr.started_at, cr.created_at) desc
limit $4::int
"""

NEGATIVE_SCAN_SQL = """
with filtered_requests as (
  select
    cr.id,
    cr.request_key,
    coalesce(cr.started_at, cr.created_at) as request_time,
    cr.incoming_route,
    cr.model_hint,
    cr.request_metadata
  from compression_requests cr
  where coalesce(cr.started_at, cr.created_at) >= $1::timestamptz
    and coalesce(cr.started_at, cr.created_at) < $2::timestamptz
    and (
      $3::text is null
      or coalesce(cr.request_metadata->>'litellm_proxy_client', 'unknown') = $3::text
    )
)
select
  fr.request_key,
  fr.request_time,
  fr.incoming_route,
  fr.model_hint,
  ce.status,
  ce.original_tokens,
  ce.compressed_tokens,
  ce.tokens_saved,
  coalesce(ce.transforms->>'skip_reason', '') as skip_reason,
  coalesce(fr.request_metadata->>'provider_session_affinity_hash', '') as
    affinity_hash
from compression_executions ce
join filtered_requests fr on fr.id = ce.request_id
where ce.is_simulated = false
  and coalesce(ce.tokens_saved, 0) < 0
order by fr.request_time
limit 50
"""

SQL_QUERIES: dict[str, str] = {
    "time_bounds": TIME_BOUNDS_SQL,
    "summary": SUMMARY_SQL,
    "latest_session": LATEST_SESSION_SQL,
    "minute_buckets": BUCKETS_SQL,
    "status_breakdown": STATUS_SQL,
    "session_affinity": AFFINITY_SQL,
    "recent_requests": RECENT_REQUESTS_SQL,
    "negative_savings_scan": NEGATIVE_SCAN_SQL,
}


@dataclass(frozen=True)
class ReportPaths:
    output_dir: Path
    json_path: Path
    markdown_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a recent Codex/LiteLLM compression and provider-cache "
            "report from the analytics PostgreSQL database."
        )
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("ANALYTICS_REPORT_DSN", DEFAULT_DSN),
        help="PostgreSQL DSN for analytics DB.",
    )
    parser.add_argument("--client", default="codex", help="Proxy client to report.")
    parser.add_argument("--hours", type=float, default=1.0, help="Window size.")
    parser.add_argument("--since", help="ISO timestamp override for window start.")
    parser.add_argument("--until", help="ISO timestamp override for window end.")
    parser.add_argument(
        "--session-gap-minutes",
        type=int,
        default=5,
        help="Gap used to detect the latest continuous request session.",
    )
    parser.add_argument(
        "--recent-limit",
        type=int,
        default=30,
        help="Recent safe request rows to include.",
    )
    parser.add_argument(
        "--cached-input-cost-multiplier",
        default=os.environ.get(
            "ANALYTICS_CACHED_INPUT_COST_MULTIPLIER",
            str(DEFAULT_CACHED_INPUT_COST_MULTIPLIER),
        ),
        help="Billing-equivalent multiplier for provider cached input.",
    )
    parser.add_argument(
        "--dashboard-url",
        default=os.environ.get("ANALYTICS_BACKEND_URL", DEFAULT_DASHBOARD_URL),
        help="Analytics backend base URL for optional dashboard snapshot.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory. Defaults to tmp/codex-proxy-session-report/<stamp>.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    multiplier = _safe_multiplier(args.cached_input_cost_multiplier)
    paths = _report_paths(args.out_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    conn = await asyncpg.connect(args.dsn)
    try:
        db_now = await conn.fetchval(TIME_BOUNDS_SQL)
        assert isinstance(db_now, datetime)
        window_end = _parse_datetime(args.until) if args.until else db_now
        window_start = (
            _parse_datetime(args.since)
            if args.since
            else window_end - timedelta(hours=args.hours)
        )
        client_filter = args.client or None
        params = [window_start, window_end, client_filter]

        summary = dict(await conn.fetchrow(SUMMARY_SQL, *params) or {})
        latest_session = await conn.fetchrow(
            LATEST_SESSION_SQL,
            window_start,
            window_end,
            client_filter,
            args.session_gap_minutes,
        )
        buckets = [
            _with_derived_bucket_metrics(dict(row), multiplier)
            for row in await conn.fetch(BUCKETS_SQL, *params)
        ]
        status_breakdown = [
            _with_raw_savings_metrics(dict(row))
            for row in await conn.fetch(STATUS_SQL, *params)
        ]
        affinity = [dict(row) for row in await conn.fetch(AFFINITY_SQL, *params)]
        recent_requests = [
            _with_recent_metrics(dict(row))
            for row in await conn.fetch(
                RECENT_REQUESTS_SQL,
                window_start,
                window_end,
                client_filter,
                max(args.recent_limit, 1),
            )
        ]
        negative_rows = [
            _with_raw_savings_metrics(dict(row))
            for row in await conn.fetch(NEGATIVE_SCAN_SQL, *params)
        ]
    finally:
        await conn.close()

    summary = _with_summary_metrics(summary, multiplier)
    narrative = _build_narrative(summary, buckets, negative_rows)
    dashboard_snapshot = _fetch_dashboard_snapshot(args.dashboard_url)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC),
        "client": args.client,
        "window": {
            "start": window_start,
            "end": window_end,
            "hours": round((window_end - window_start).total_seconds() / 3600, 6),
            "db_now": db_now,
        },
        "latest_session": dict(latest_session) if latest_session else None,
        "parameters": {
            "session_gap_minutes": args.session_gap_minutes,
            "recent_limit": args.recent_limit,
            "cached_input_cost_multiplier": str(multiplier),
            "dashboard_url": args.dashboard_url,
        },
        "version_source_surface": {
            "docs": [
                {
                    "tenant": "uv",
                    "url_prefix": "https://docs.astral.sh",
                    "used_for": "uv run script execution",
                },
                {
                    "tenant": "postgresql",
                    "url_prefix": "https://www.postgresql.org",
                    "used_for": "date_trunc, aggregate sums, current time",
                },
                {
                    "tenant": "litellm",
                    "url_prefix": "https://docs.litellm.ai",
                    "used_for": "callback/provider usage context",
                },
            ],
            "local_dependencies": {
                "asyncpg": "0.31.0 from uv.lock; pyproject requires asyncpg>=0.30.0",
            },
            "gap": (
                "asyncpg docs were not available through docsearch; the script "
                "uses existing project dependency and standard asyncpg fetch APIs."
            ),
        },
        "narrative": narrative,
        "summary": summary,
        "minute_buckets": buckets,
        "status_breakdown": status_breakdown,
        "session_affinity": affinity,
        "recent_requests": recent_requests,
        "negative_savings_scan": negative_rows,
        "dashboard_snapshot": dashboard_snapshot,
        "sql": SQL_QUERIES,
    }

    paths.json_path.write_text(_to_json(report), encoding="utf-8")
    paths.markdown_path.write_text(_to_markdown(report), encoding="utf-8")
    print(paths.json_path)
    print(paths.markdown_path)
    return 0


def _report_paths(out_dir: Path | None) -> ReportPaths:
    if out_dir is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("tmp") / "codex-proxy-session-report" / stamp
    return ReportPaths(
        output_dir=out_dir,
        json_path=out_dir / "report.json",
        markdown_path=out_dir / "report.md",
    )


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _safe_multiplier(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except Exception:
        return DEFAULT_CACHED_INPUT_COST_MULTIPLIER
    if value < 0 or value > 1:
        return DEFAULT_CACHED_INPUT_COST_MULTIPLIER
    return value


def _percent(part: int | Decimal | None, whole: int | Decimal | None) -> float | None:
    if part is None or whole is None:
        return None
    whole_decimal = Decimal(whole)
    if whole_decimal <= 0:
        return None
    return round(float((Decimal(part) / whole_decimal) * Decimal(100)), 4)


def _ratio(part: int | Decimal | None, whole: int | Decimal | None) -> float | None:
    if part is None or whole is None:
        return None
    whole_decimal = Decimal(whole)
    if whole_decimal <= 0:
        return None
    return round(float(Decimal(part) / whole_decimal), 6)


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _billing_equivalent(
    provider_input: int,
    provider_cached: int,
    multiplier: Decimal,
) -> Decimal | None:
    if provider_input <= 0:
        return None
    cached = min(provider_cached, provider_input)
    uncached = provider_input - cached
    return Decimal(uncached) + (Decimal(cached) * multiplier)


def _with_summary_metrics(
    row: Mapping[str, Any],
    multiplier: Decimal,
) -> dict[str, Any]:
    result = dict(row)
    original = _int(result.get("original_tokens"))
    tokens_saved = _int(result.get("tokens_saved"))
    provider_input = _int(result.get("provider_input_tokens"))
    provider_cached = min(
        _int(result.get("provider_cached_input_tokens")), provider_input
    )
    billing = _billing_equivalent(provider_input, provider_cached, multiplier)
    billing_delta = (
        Decimal(original) - billing if billing is not None and original else None
    )
    result["provider_cached_input_tokens"] = provider_cached
    result["provider_uncached_input_tokens"] = max(provider_input - provider_cached, 0)
    result["raw_savings_percent"] = _percent(tokens_saved, original)
    result["provider_cache_hit_percent"] = _percent(provider_cached, provider_input)
    result["provider_cache_hit_ratio"] = _ratio(provider_cached, provider_input)
    result["billing_equivalent_input_tokens"] = (
        round(float(billing), 6) if billing is not None else None
    )
    result["billing_equivalent_tokens_saved"] = (
        round(float(billing_delta), 6) if billing_delta is not None else None
    )
    result["billing_equivalent_savings_percent"] = (
        _percent(billing_delta, original) if billing_delta is not None else None
    )
    result["billing_equivalent_capacity_multiplier"] = (
        round(float(Decimal(original) / billing), 6)
        if billing is not None and billing > 0 and original
        else None
    )
    return result


def _with_raw_savings_metrics(row: dict[str, Any]) -> dict[str, Any]:
    row["raw_savings_percent"] = _percent(
        _int(row.get("tokens_saved")), _int(row.get("original_tokens"))
    )
    return row


def _with_derived_bucket_metrics(
    row: dict[str, Any],
    multiplier: Decimal,
) -> dict[str, Any]:
    row = _with_raw_savings_metrics(row)
    provider_input = _int(row.get("provider_input_tokens"))
    provider_cached = min(_int(row.get("provider_cached_input_tokens")), provider_input)
    original = _int(row.get("original_tokens"))
    billing = _billing_equivalent(provider_input, provider_cached, multiplier)
    billing_delta = (
        Decimal(original) - billing if billing is not None and original else None
    )
    row["provider_cached_input_tokens"] = provider_cached
    row["provider_uncached_input_tokens"] = max(provider_input - provider_cached, 0)
    row["provider_cache_hit_percent"] = _percent(provider_cached, provider_input)
    row["billing_equivalent_input_tokens"] = (
        round(float(billing), 6) if billing is not None else None
    )
    row["billing_equivalent_savings_percent"] = (
        _percent(billing_delta, original) if billing_delta is not None else None
    )
    return row


def _with_recent_metrics(row: dict[str, Any]) -> dict[str, Any]:
    row = _with_raw_savings_metrics(row)
    row["provider_cache_hit_percent"] = _percent(
        _int(row.get("provider_cached_input_tokens")),
        _int(row.get("provider_input_tokens")),
    )
    return row


def _build_narrative(
    summary: Mapping[str, Any],
    buckets: Sequence[Mapping[str, Any]],
    negative_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    first_cache_70 = next(
        (
            bucket
            for bucket in buckets
            if (bucket.get("provider_cache_hit_percent") or 0) >= 70
        ),
        None,
    )
    first_cache_95 = next(
        (
            bucket
            for bucket in buckets
            if (bucket.get("provider_cache_hit_percent") or 0) >= 95
        ),
        None,
    )
    raw_buckets = [
        bucket for bucket in buckets if bucket.get("raw_savings_percent") is not None
    ]
    billing_buckets = [
        bucket
        for bucket in buckets
        if bucket.get("billing_equivalent_savings_percent") is not None
    ]
    worst_raw = min(
        raw_buckets,
        key=lambda item: item.get("raw_savings_percent") or 0,
        default=None,
    )
    worst_billing = min(
        billing_buckets,
        key=lambda item: item.get("billing_equivalent_savings_percent") or 0,
        default=None,
    )
    return {
        "verdict": _verdict(summary),
        "first_provider_cache_ge_70": _bucket_marker(first_cache_70),
        "first_provider_cache_ge_95": _bucket_marker(first_cache_95),
        "worst_raw_savings_bucket": _bucket_marker(worst_raw),
        "worst_billing_equivalent_bucket": _bucket_marker(worst_billing),
        "negative_savings_observed": bool(negative_rows),
        "negative_savings_note": (
            "Negative local token-delta rows were found in this window."
            if negative_rows
            else (
                "No negative local token-delta rows were found in this "
                "queried window. A previously observed -3000% dashboard value "
                "would need an older/different window or a dashboard formula "
                "artifact to reproduce."
            )
        ),
    }


def _verdict(summary: Mapping[str, Any]) -> str:
    executions = _int(summary.get("executions"))
    saved = _int(summary.get("tokens_saved"))
    cached = _int(summary.get("provider_cached_input_tokens"))
    input_tokens = _int(summary.get("provider_input_tokens"))
    if executions <= 0:
        return "No matching Codex compression executions were found."
    if saved > 0 and cached > 0:
        return (
            "Codex is routing through LiteLLM with compression rows and "
            "provider-reported cached input in the same window. This is a "
            "diagnostic correlation, not direct-vs-proxy usefulness proof."
        )
    if saved > 0:
        return (
            "The local compression token delta is positive, but provider cache "
            "was absent; no provider-credit usefulness claim is made."
        )
    if input_tokens > 0:
        return "Provider calls exist, but compression did not save local tokens."
    return "Compression rows exist, but provider-reported usage is absent."


def _bucket_marker(bucket: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if bucket is None:
        return None
    keys = (
        "bucket",
        "executions",
        "original_tokens",
        "tokens_saved",
        "raw_savings_percent",
        "provider_calls",
        "provider_input_tokens",
        "provider_cached_input_tokens",
        "provider_cache_hit_percent",
        "billing_equivalent_savings_percent",
    )
    return {key: bucket.get(key) for key in keys}


def _fetch_dashboard_snapshot(base_url: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/dashboard/partials/summary?preset=1h&data_scope=real"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"available": False, "url": url, "error": str(exc)}
    return {
        "available": True,
        "url": url,
        "scope": (
            "unfiltered dashboard partial for real data over the dashboard's "
            "last-hour preset; SQL report rows remain client-filtered"
        ),
        "text_excerpt": _html_text_excerpt(text),
    }


def _html_text_excerpt(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


def _to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _to_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    narrative = report["narrative"]
    window = report["window"]
    lines: list[str] = [
        "# Codex Proxy Session Report",
        "",
        f"Generated: `{report['generated_at'].isoformat()}`",
        f"Client: `{report['client']}`",
        f"Window: `{window['start'].isoformat()}` to `{window['end'].isoformat()}`",
        "",
        "## Verdict",
        "",
        str(narrative["verdict"]),
        "",
        str(narrative["negative_savings_note"]),
        "",
        "## Key Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Requests | {_fmt_int(summary['requests'])} |",
        f"| Executions | {_fmt_int(summary['executions'])} |",
        f"| Succeeded / skipped / failed | {_fmt_int(summary['executions_succeeded'])} / {_fmt_int(summary['executions_skipped'])} / {_fmt_int(summary['executions_failed'])} |",
        f"| Original tokens | {_fmt_int(summary['original_tokens'])} |",
        f"| Compressed tokens | {_fmt_int(summary['compressed_tokens'])} |",
        f"| Local token delta | {_fmt_int(summary['tokens_saved'])} |",
        f"| Local delta percent | {_fmt_percent(summary['raw_savings_percent'])} |",
        f"| Provider calls | {_fmt_int(summary['provider_calls'])} |",
        f"| Provider input tokens | {_fmt_int(summary['provider_input_tokens'])} |",
        f"| Provider cached input tokens | {_fmt_int(summary['provider_cached_input_tokens'])} |",
        f"| Provider cache hit | {_fmt_percent(summary['provider_cache_hit_percent'])} |",
        f"| Billing-equivalent input tokens | {_fmt_float(summary['billing_equivalent_input_tokens'])} |",
        f"| Billing input estimate delta | {_fmt_percent(summary['billing_equivalent_savings_percent'])} |",
        "",
        "## Timeline Findings",
        "",
        _narrative_bullet(
            "First minute above 70% provider cache",
            narrative["first_provider_cache_ge_70"],
        ),
        _narrative_bullet(
            "First minute above 95% provider cache",
            narrative["first_provider_cache_ge_95"],
        ),
        _narrative_bullet(
            "Worst local-delta minute", narrative["worst_raw_savings_bucket"]
        ),
        _narrative_bullet(
            "Worst billing-input-estimate minute",
            narrative["worst_billing_equivalent_bucket"],
        ),
        "",
        "## Minute Buckets",
        "",
        "| Minute | Exec | Local delta | Local delta % | Provider input | Cached input | Cache hit | Billing input delta % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for bucket in report["minute_buckets"]:
        lines.append(
            "| "
            f"{bucket['bucket'].isoformat()} | "
            f"{_fmt_int(bucket['executions'])} | "
            f"{_fmt_int(bucket['tokens_saved'])} | "
            f"{_fmt_percent(bucket['raw_savings_percent'])} | "
            f"{_fmt_int(bucket['provider_input_tokens'])} | "
            f"{_fmt_int(bucket['provider_cached_input_tokens'])} | "
            f"{_fmt_percent(bucket['provider_cache_hit_percent'])} | "
            f"{_fmt_percent(bucket['billing_equivalent_savings_percent'])} |"
        )
    lines.extend(
        [
            "",
            "## Status Breakdown",
            "",
            "| Status | Skip reason | Executions | Original | Compressed | Delta | Delta % |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["status_breakdown"]:
        lines.append(
            "| "
            f"{row['status']} | "
            f"{row['skip_reason'] or '-'} | "
            f"{_fmt_int(row['executions'])} | "
            f"{_fmt_int(row['original_tokens'])} | "
            f"{_fmt_int(row['compressed_tokens'])} | "
            f"{_fmt_int(row['tokens_saved'])} | "
            f"{_fmt_percent(row['raw_savings_percent'])} |"
        )
    lines.extend(
        [
            "",
            "## Session Affinity Evidence",
            "",
            "| Source | Hash | Requests | First seen | Last seen |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in report["session_affinity"]:
        lines.append(
            "| "
            f"{row['source'] or '-'} | "
            f"`{row['hash'] or '-'}` | "
            f"{_fmt_int(row['requests'])} | "
            f"{_fmt_time(row['first_seen'])} | "
            f"{_fmt_time(row['last_seen'])} |"
        )
    lines.extend(
        [
            "",
            "## Negative Savings Scan",
            "",
        ]
    )
    if report["negative_savings_scan"]:
        lines.extend(
            [
                "| Time | Request | Status | Original | Compressed | Delta | Delta % |",
                "|---|---|---|---:|---:|---:|---:|",
            ]
        )
        for row in report["negative_savings_scan"]:
            lines.append(
                "| "
                f"{_fmt_time(row['request_time'])} | "
                f"`{row['request_key']}` | "
                f"{row['status']} | "
                f"{_fmt_int(row['original_tokens'])} | "
                f"{_fmt_int(row['compressed_tokens'])} | "
                f"{_fmt_int(row['tokens_saved'])} | "
                f"{_fmt_percent(row['raw_savings_percent'])} |"
            )
    else:
        lines.append("No negative local token-delta executions matched this window.")
    lines.extend(
        [
            "",
            "## Recent Safe Request Rows",
            "",
            "| Time | Route | Model | Compression | Delta | Provider input | Cached | Cache hit | Affinity |",
            "|---|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["recent_requests"]:
        lines.append(
            "| "
            f"{_fmt_time(row['request_time'])} | "
            f"`{row['incoming_route'] or '-'}` | "
            f"`{row['provider_model'] or row['model_hint'] or '-'}` | "
            f"{row['compression_status'] or '-'} | "
            f"{_fmt_int(row['tokens_saved'])} | "
            f"{_fmt_int(row['provider_input_tokens'])} | "
            f"{_fmt_int(row['provider_cached_input_tokens'])} | "
            f"{_fmt_percent(row['provider_cache_hit_percent'])} | "
            f"`{(row['affinity_hash'] or '-')[:12]}` |"
        )
    lines.extend(
        [
            "",
            "## Dashboard Snapshot",
            "",
            (
                "Scope: unfiltered dashboard partial for real data over the "
                "dashboard last-hour preset. The SQL report above remains "
                f"client-filtered to `{report['client']}`."
            ),
            "",
            (
                report["dashboard_snapshot"]["text_excerpt"]
                if report["dashboard_snapshot"]["available"]
                else f"Unavailable: {report['dashboard_snapshot']['error']}"
            ),
            "",
            "## Version And Source Surface",
            "",
            (
                "Version/source-surface: docs tenants `uv` from "
                "`https://docs.astral.sh`, `postgresql` from "
                "`https://www.postgresql.org`, and `litellm` from "
                "`https://docs.litellm.ai`; local `asyncpg` is 0.31.0 from "
                "`uv.lock`; gap: asyncpg docs were not available through "
                "docsearch, so the script uses the existing project dependency."
            ),
            "",
            "## SQL Queries",
            "",
        ]
    )
    for name, sql in report["sql"].items():
        lines.extend([f"### {name}", "", "```sql", sql.strip(), "```", ""])
    return "\n".join(lines)


def _narrative_bullet(label: str, value: Mapping[str, Any] | None) -> str:
    if not value:
        return f"- {label}: not observed."
    return (
        f"- {label}: `{_fmt_time(value['bucket'])}`, "
        f"cache `{_fmt_percent(value['provider_cache_hit_percent'])}`, "
        f"local delta `{_fmt_percent(value['raw_savings_percent'])}`, "
        f"billing input delta `{_fmt_percent(value['billing_equivalent_savings_percent'])}`."
    )


def _fmt_int(value: Any) -> str:
    return f"{_int(value):,}"


def _fmt_float(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.2f}"


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.2f}%"


def _fmt_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
