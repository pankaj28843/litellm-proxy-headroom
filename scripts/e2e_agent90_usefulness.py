from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from headroom.agent_savings import get_agent_savings_profile

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "agent90-usefulness"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_MODEL_VERBOSITY = "medium"
DEFAULT_SAVINGS_PROFILE = "agent-90"
DEFAULT_LITELLM_CALLBACK = "HeadroomCallback"
DEFAULT_LITELLM_URL = "http://127.0.0.1:4000"
DEFAULT_ANALYTICS_URL = "http://127.0.0.1:8010"
DEFAULT_CACHED_INPUT_COST_MULTIPLIER = 0.10
DEFAULT_MAX_CACHE_RATIO_DROP = 0.05
MARKER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")
SAVINGS_PROFILE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
REASONING_EFFORT_VALUES = {"minimal", "low", "medium", "high", "xhigh"}
MODEL_VERBOSITY_VALUES = {"low", "medium", "high"}
PROXY_RUN_MARKER_ENV = "LITELLM_PROXY_RUN_MARKER"
CODEX_LITELLM_MODEL_ENV = "CODEX_LITELLM_MODEL"
CODEX_LITELLM_REASONING_EFFORT_ENV = "CODEX_LITELLM_REASONING_EFFORT"
CODEX_LITELLM_MODEL_VERBOSITY_ENV = "CODEX_LITELLM_MODEL_VERBOSITY"
CODEX_LITELLM_CLIENT_ENV = "CODEX_LITELLM_CLIENT"
CODEX_LITELLM_BASE_URL_ENV = "CODEX_LITELLM_BASE_URL"
CODEX_LITELLM_ANALYTICS_URL_ENV = "CODEX_LITELLM_ANALYTICS_URL"
LITELLM_MASTER_KEY_ENV = "LITELLM_MASTER_KEY"
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)
TOKEN_COUNT_RE = r"(?P<value>(?:\d{1,3}(?:[.,_\u00a0 ]\d{3})+|\d+))(?![.,_\u00a0 ]\d)"
COST_VALUE_RE = r"(?P<value>\d+(?:[.,]\d+)?)"
TOKEN_FIELD_PATTERNS = {
    "input_tokens": (
        re.compile(
            rf"\binput(?:[\s_-]*tokens?)?\b['\"]?\s*[:=]\s*{TOKEN_COUNT_RE}",
            re.IGNORECASE,
        ),
    ),
    "cached_input_tokens": (
        re.compile(
            rf"\bcached(?:[\s_-]*input)?(?:[\s_-]*tokens?)?\b['\"]?\s*[:=]\s*{TOKEN_COUNT_RE}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\(\s*\+\s*{TOKEN_COUNT_RE}\s+cached(?:\s+input)?(?:\s+tokens?)?\s*\)",
            re.IGNORECASE,
        ),
    ),
    "output_tokens": (
        re.compile(
            rf"\boutput(?:[\s_-]*tokens?)?\b['\"]?\s*[:=]\s*{TOKEN_COUNT_RE}",
            re.IGNORECASE,
        ),
    ),
    "reasoning_tokens": (
        re.compile(
            rf"\breasoning(?:[\s_-]*tokens?)?\b['\"]?\s*[:=]\s*{TOKEN_COUNT_RE}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\breasoning(?:[\s_-]*tokens?)?\b\s+{TOKEN_COUNT_RE}",
            re.IGNORECASE,
        ),
    ),
    "total_tokens": (
        re.compile(
            rf"\btotal(?:[\s_-]*tokens?)?\b['\"]?\s*[:=]\s*{TOKEN_COUNT_RE}",
            re.IGNORECASE,
        ),
    ),
}
COST_PATTERNS = (
    re.compile(
        rf"\bcost\b[^\d$\n]{{0,32}}(?:USD\s*)?\$?\s*{COST_VALUE_RE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:USD\s+\$?|\$)\s*{COST_VALUE_RE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{COST_VALUE_RE}\s*USD\b",
        re.IGNORECASE,
    ),
)


def _usage_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _parse_codex_json_usage(line: str) -> dict[str, int]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict) or payload.get("type") != "turn.completed":
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}

    parsed: dict[str, int] = {}
    field_map = {
        "input_tokens": "input_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "output_tokens": "output_tokens",
        "reasoning_output_tokens": "reasoning_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "total_tokens": "total_tokens",
    }
    for usage_field, summary_field in field_map.items():
        value = _usage_int(usage.get(usage_field))
        if value is not None:
            parsed[summary_field] = value

    input_tokens = parsed.get("input_tokens")
    output_tokens = parsed.get("output_tokens")
    if (
        "total_tokens" not in parsed
        and input_tokens is not None
        and output_tokens is not None
    ):
        parsed["total_tokens"] = input_tokens + output_tokens
    return parsed


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _default_marker() -> str:
    return f"agent90-usefulness-{int(time.time())}"


def _validate_marker(marker: str) -> str:
    if not MARKER_RE.match(marker):
        raise argparse.ArgumentTypeError(
            "marker may contain only letters, digits, underscore, dot, colon, or dash"
        )
    return marker


def _validate_model(model: str) -> str:
    if not MODEL_RE.match(model):
        raise argparse.ArgumentTypeError(
            "model may contain only letters, digits, underscore, dot, colon, slash, or dash"
        )
    return model


def _validate_reasoning_effort(effort: str) -> str:
    if effort not in REASONING_EFFORT_VALUES:
        raise argparse.ArgumentTypeError(
            "reasoning effort must be one of minimal, low, medium, high, or xhigh"
        )
    return effort


def _validate_model_verbosity(verbosity: str) -> str:
    if verbosity not in MODEL_VERBOSITY_VALUES:
        raise argparse.ArgumentTypeError(
            "model verbosity must be one of low, medium, or high"
        )
    return verbosity


def _validate_savings_profile(profile: str) -> str:
    if not SAVINGS_PROFILE_RE.match(profile):
        raise argparse.ArgumentTypeError(
            "savings profile may contain only letters, digits, underscore, dot, colon, or dash"
        )
    try:
        return get_agent_savings_profile(profile).name
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _validate_http_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise argparse.ArgumentTypeError(
            "URL must be an http(s) base URL without credentials, query, or fragment"
        )
    return url


def _parse_token_count(value: str) -> int:
    normalized = re.sub(r"[.,_\s\u00a0]", "", value)
    return int(normalized)


def _parse_cost_usd(value: str) -> str | None:
    normalized = value.strip().replace("$", "").replace("USD", "").replace("usd", "")
    normalized = normalized.replace(" ", "")
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None
    if parsed < 0:
        return None
    return format(parsed, "f")


def parse_token_summary(streams: dict[str, str]) -> dict[str, Any]:
    values: dict[str, int] = {}
    field_sources: dict[str, dict[str, Any]] = {}
    json_usage_event_count = 0
    json_usage_latest: dict[str, int] = {}
    json_usage_sources: dict[str, list[dict[str, Any]]] = {}
    cost_usd: str | None = None
    cost_source: dict[str, Any] | None = None
    source_lines: list[dict[str, Any]] = []

    for stream_name, text in streams.items():
        for line_number, line in enumerate(text.splitlines(), start=1):
            parsed_fields: set[str] = set()
            json_usage = _parse_codex_json_usage(line)
            if json_usage:
                json_usage_event_count += 1
                for field, value in json_usage.items():
                    json_usage_latest[field] = value
                    json_usage_sources.setdefault(field, []).append(
                        {
                            "stream": stream_name,
                            "line_number": line_number,
                        }
                    )
                    parsed_fields.add(field)

            if not json_usage:
                for field, patterns in TOKEN_FIELD_PATTERNS.items():
                    for pattern in patterns:
                        for match in pattern.finditer(line):
                            if field == "input_tokens":
                                prefix = line[
                                    max(0, match.start() - 16) : match.start()
                                ]
                                if "cached" in prefix.lower():
                                    continue
                            values[field] = _parse_token_count(match.group("value"))
                            field_sources[field] = {
                                "stream": stream_name,
                                "line_number": line_number,
                            }
                            parsed_fields.add(field)

            if re.search(r"\b(cost|usd)\b|\$", line, re.IGNORECASE):
                for pattern in COST_PATTERNS:
                    match = pattern.search(line)
                    if match is None:
                        continue
                    parsed_cost = _parse_cost_usd(match.group("value"))
                    if parsed_cost is None:
                        continue
                    cost_usd = parsed_cost
                    cost_source = {
                        "stream": stream_name,
                        "line_number": line_number,
                    }
                    parsed_fields.add("cost_usd")
                    break

            if parsed_fields:
                source_lines.append(
                    {
                        "stream": stream_name,
                        "line_number": line_number,
                        "fields": sorted(parsed_fields),
                        "text": line,
                    }
                )

    if json_usage_event_count:
        for field, value in json_usage_latest.items():
            values[field] = value
            field_sources[field] = {
                "stream": "latest_cumulative",
                "event_type": "turn.completed",
                "event_count": json_usage_event_count,
                "line_sources": json_usage_sources[field],
                "latest_line_source": json_usage_sources[field][-1],
            }

    missing_fields = [field for field in TOKEN_FIELDS if field not in values]
    return {
        **{field: values.get(field) for field in TOKEN_FIELDS},
        "usage_source": (
            "codex_json_turn_completed_cumulative_latest"
            if json_usage_event_count
            else "text_summary"
        ),
        "json_turn_completed_count": json_usage_event_count,
        "cost_usd": cost_usd,
        "cost_complete": cost_usd is not None,
        "cost_source": cost_source,
        "complete": not missing_fields,
        "missing_fields": missing_fields,
        "field_sources": field_sources,
        "source_lines": source_lines,
    }


def _token_summary_lines(summary: dict[str, Any]) -> str:
    lines = [source["text"] for source in summary["source_lines"]]
    return "\n".join(lines) + ("\n" if lines else "")


def _summary_values(summary: dict[str, Any] | None) -> dict[str, int | None]:
    if summary is None:
        return {field: None for field in TOKEN_FIELDS}
    return {field: summary.get(field) for field in TOKEN_FIELDS}


def _newly_processed_input_tokens(summary: dict[str, Any] | None) -> int | None:
    if summary is None:
        return None
    input_tokens = summary.get("input_tokens")
    cached_input_tokens = summary.get("cached_input_tokens")
    if input_tokens is None or cached_input_tokens is None:
        return None
    return max(int(input_tokens) - int(cached_input_tokens), 0)


def _cached_input_ratio(summary: dict[str, Any] | None) -> float | None:
    if summary is None:
        return None
    input_tokens = summary.get("input_tokens")
    cached_input_tokens = summary.get("cached_input_tokens")
    if input_tokens in (None, 0) or cached_input_tokens is None:
        return None
    return round(float(cached_input_tokens) / float(input_tokens), 6)


def _billing_equivalent_input_tokens(
    summary: dict[str, Any] | None, *, cached_input_multiplier: float
) -> float | None:
    newly_processed = _newly_processed_input_tokens(summary)
    if summary is None or newly_processed is None:
        return None
    cached_input_tokens = summary.get("cached_input_tokens")
    if cached_input_tokens is None:
        return None
    return round(
        newly_processed + (float(cached_input_tokens) * cached_input_multiplier), 6
    )


def _derived_summary(summary: dict[str, Any] | None) -> dict[str, int | float | None]:
    return {
        "newly_processed_input_tokens": _newly_processed_input_tokens(summary),
        "cached_input_ratio": _cached_input_ratio(summary),
        "billing_equivalent_input_tokens": _billing_equivalent_input_tokens(
            summary,
            cached_input_multiplier=DEFAULT_CACHED_INPUT_COST_MULTIPLIER,
        ),
    }


def _delta(
    proxy_value: int | float | None, direct_value: int | float | None
) -> float | None:
    if proxy_value is None or direct_value is None:
        return None
    return round(float(proxy_value) - float(direct_value), 6)


def _evaluate_usefulness(comparison: dict[str, Any]) -> dict[str, Any]:
    fail_reasons: list[str] = []
    missing_reasons: list[str] = []
    checks: dict[str, Any] = {}

    if comparison["status"] != "complete":
        missing_reasons.append("token_summary_incomplete")
    else:
        total_delta = comparison["delta_proxy_minus_direct"]["total_tokens"]
        checks["total_tokens_not_worse"] = {
            "ok": total_delta is not None and total_delta <= 0,
            "delta_proxy_minus_direct": total_delta,
        }
        if not checks["total_tokens_not_worse"]["ok"]:
            fail_reasons.append("proxy_total_tokens_worse")

        billing_delta = comparison["derived"]["delta_proxy_minus_direct"][
            "billing_equivalent_input_tokens"
        ]
        checks["billing_equivalent_input_not_worse"] = {
            "ok": billing_delta is not None and billing_delta <= 0,
            "delta_proxy_minus_direct": billing_delta,
            "cached_input_multiplier": DEFAULT_CACHED_INPUT_COST_MULTIPLIER,
        }
        if not checks["billing_equivalent_input_not_worse"]["ok"]:
            fail_reasons.append("proxy_billing_equivalent_input_worse")

        cache_ratio_delta = comparison["derived"]["delta_proxy_minus_direct"][
            "cached_input_ratio"
        ]
        checks["cache_ratio_preserved"] = {
            "ok": cache_ratio_delta is not None
            and cache_ratio_delta >= -DEFAULT_MAX_CACHE_RATIO_DROP,
            "delta_proxy_minus_direct": cache_ratio_delta,
            "max_allowed_drop": DEFAULT_MAX_CACHE_RATIO_DROP,
        }
        if not checks["cache_ratio_preserved"]["ok"]:
            fail_reasons.append("proxy_cache_ratio_drop_too_large")

    cost = comparison["cost"]
    if cost["status"] == "complete":
        cost_delta = Decimal(str(cost["delta_proxy_minus_direct_usd"]))
        checks["cost_not_worse"] = {
            "ok": cost_delta <= 0,
            "delta_proxy_minus_direct_usd": cost["delta_proxy_minus_direct_usd"],
        }
        if not checks["cost_not_worse"]["ok"]:
            fail_reasons.append("proxy_cost_worse")
    else:
        checks["cost_not_worse"] = {
            "ok": None,
            "delta_proxy_minus_direct_usd": None,
        }
        missing_reasons.append("cost_missing")

    status = "fail" if fail_reasons else "incomplete" if missing_reasons else "pass"
    return {
        "status": status,
        "checks": checks,
        "fail_reasons": fail_reasons,
        "missing_reasons": missing_reasons,
    }


def _completion_contract(comparison: dict[str, Any]) -> dict[str, Any]:
    """Final proof contract for environments where observed lane cost is absent."""

    mvp = comparison["mvp_usefulness"]
    cost = comparison["cost"]
    if mvp["fail_reasons"]:
        return {
            "status": "fail",
            "scope": "provider_usage_cache",
            "cost_status": cost["status"],
            "fail_reasons": list(mvp["fail_reasons"]),
            "missing_reasons": list(mvp["missing_reasons"]),
        }
    if comparison["status"] != "complete":
        return {
            "status": "incomplete",
            "scope": "provider_usage_cache",
            "cost_status": cost["status"],
            "fail_reasons": [],
            "missing_reasons": ["token_summary_incomplete"],
        }
    if cost["status"] == "complete":
        return {
            "status": "pass",
            "scope": "provider_usage_cache_cost",
            "cost_status": "observed",
            "fail_reasons": [],
            "missing_reasons": [],
        }
    if mvp["missing_reasons"] == ["cost_missing"]:
        return {
            "status": "pass",
            "scope": "provider_usage_cache",
            "cost_status": "unavailable",
            "fail_reasons": [],
            "missing_reasons": ["cost_missing"],
        }
    return {
        "status": "incomplete",
        "scope": "provider_usage_cache",
        "cost_status": cost["status"],
        "fail_reasons": [],
        "missing_reasons": list(mvp["missing_reasons"]),
    }


def _compare_token_summaries(results: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = {
        result["lane"]: result.get("token_summary")
        for result in results
        if result.get("lane") in {"direct", "proxy"}
    }
    direct = summaries.get("direct")
    proxy = summaries.get("proxy")

    comparison: dict[str, Any] = {
        "status": "complete"
        if direct and proxy and direct["complete"] and proxy["complete"]
        else "incomplete",
        "direct": _summary_values(direct),
        "proxy": _summary_values(proxy),
        "missing_by_lane": {
            "direct": [] if direct is None else direct["missing_fields"],
            "proxy": [] if proxy is None else proxy["missing_fields"],
        },
    }
    if direct is None:
        comparison["missing_by_lane"]["direct"] = list(TOKEN_FIELDS)
    if proxy is None:
        comparison["missing_by_lane"]["proxy"] = list(TOKEN_FIELDS)

    deltas: dict[str, int | None] = {}
    for field in TOKEN_FIELDS:
        direct_value = comparison["direct"][field]
        proxy_value = comparison["proxy"][field]
        deltas[field] = (
            proxy_value - direct_value
            if direct_value is not None and proxy_value is not None
            else None
        )
    comparison["delta_proxy_minus_direct"] = deltas
    direct_cost = None if direct is None else direct.get("cost_usd")
    proxy_cost = None if proxy is None else proxy.get("cost_usd")
    cost_delta = None
    if direct_cost is not None and proxy_cost is not None:
        cost_delta = format(Decimal(str(proxy_cost)) - Decimal(str(direct_cost)), "f")
    comparison["cost"] = {
        "status": "complete"
        if direct_cost is not None and proxy_cost is not None
        else "missing",
        "direct_usd": direct_cost,
        "proxy_usd": proxy_cost,
        "delta_proxy_minus_direct_usd": cost_delta,
        "missing_by_lane": {
            "direct": direct_cost is None,
            "proxy": proxy_cost is None,
        },
    }
    direct_derived = _derived_summary(direct)
    proxy_derived = _derived_summary(proxy)
    comparison["derived"] = {
        "cached_input_cost_multiplier": DEFAULT_CACHED_INPUT_COST_MULTIPLIER,
        "max_cache_ratio_drop": DEFAULT_MAX_CACHE_RATIO_DROP,
        "direct": direct_derived,
        "proxy": proxy_derived,
        "delta_proxy_minus_direct": {
            field: _delta(proxy_derived[field], direct_derived[field])
            for field in direct_derived
        },
    }
    comparison["mvp_usefulness"] = _evaluate_usefulness(comparison)
    comparison["completion_contract"] = _completion_contract(comparison)
    return comparison


def _task_prompt(marker: str, task_lines: int) -> str:
    line_template = (
        f"{marker} 2026-06-23T21:"
        + "{i%60:02d}"
        + ":00 ERROR component=litellm route=/v1/responses "
        + "request="
        + "{i:03d}"
        + " payload payload payload payload"
    )
    shell_command = (
        f"python3 -c 'for i in range({task_lines}): " + f'print(f"{line_template}")\''
    )
    return (
        "Do not edit files. For runtime evidence, first use the shell tool to "
        f"run exactly this read-only command: {shell_command}. After the "
        "command finishes, reply with exactly this marker and nothing else: "
        f"{marker}"
    )


def _lane_command(
    executable: str,
    workdir: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    model_verbosity: str,
) -> list[str]:
    return [
        executable,
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "-c",
        f'model_verbosity="{model_verbosity}"',
        "-a",
        "never",
        "-s",
        "read-only",
        "-C",
        str(workdir),
        "exec",
        "--json",
        prompt,
    ]


def _db_query_sql(
    started_at_placeholder: str = "<proxy_started_at_utc>",
    ended_at_placeholder: str = "<proxy_ended_at_utc>",
    marker_placeholder: str = "<marker>",
    savings_profile_placeholder: str = "<expected_savings_profile>",
    grace_seconds: int = 300,
) -> str:
    return f"""drop table if exists agent90_matched_requests;

create temporary table agent90_matched_requests as
select
  '{savings_profile_placeholder}' as expected_strategy_name,
  cr.request_key,
  cr.created_at,
  cr.incoming_route,
  cr.request_metadata->>'litellm_proxy_run_marker' as litellm_proxy_run_marker,
  cr.request_metadata->>'litellm_proxy_project' as litellm_proxy_project,
  cr.request_metadata->>'litellm_proxy_client' as litellm_proxy_client,
  case
    when cr.request_metadata->>'litellm_proxy_run_marker' = '{marker_placeholder}'
    then 'marker'
    else 'time_window'
  end as correlation_source,
  c.strategy_name,
  c.strategy_version,
  ce.status as execution_status,
  ce.original_tokens,
  ce.compressed_tokens,
  ce.tokens_saved,
  ce.compression_ratio,
  ce.transforms,
  pc.provider_call_key,
  pc.litellm_call_id,
  pc.provider_request_id,
  pc.provider_response_id,
  pc.provider,
  pc.model,
  pc.status as provider_status,
  tub.measurement_source,
  tub.input_tokens,
  tub.cached_input_tokens,
  tub.newly_processed_input_tokens,
  tub.cache_write_tokens,
  tub.output_tokens,
  tub.reasoning_tokens,
  tub.total_tokens,
  pc.cost_total,
  pc.currency
from compression_requests cr
join compression_executions ce on ce.request_id = cr.id
join compression_config_snapshots c on c.id = ce.config_snapshot_id
left join provider_calls pc on pc.execution_id = ce.id
left join token_usage_breakdowns tub on tub.provider_call_id = pc.id
where cr.incoming_route = '/v1/responses'
  and (
    cr.request_metadata->>'litellm_proxy_run_marker' = '{marker_placeholder}'
    or (
      cr.created_at >= '{started_at_placeholder}'::timestamptz
      and cr.created_at <= (
        '{ended_at_placeholder}'::timestamptz + interval '{grace_seconds} seconds'
      )
    )
  )
;

select
  'aggregate' as proof_row_type,
  '{savings_profile_placeholder}' as expected_strategy_name,
  '{marker_placeholder}' as requested_marker,
  min(created_at) as first_request_at,
  max(created_at) as last_request_at,
  count(distinct request_key) as request_count,
  count(*) as execution_row_count,
  count(provider_call_key) filter (
    where measurement_source = 'provider_reported'
  ) as provider_reported_call_count,
  string_agg(distinct model, ', ' order by model) filter (
    where model is not null
  ) as models,
  sum(input_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_input_tokens,
  sum(cached_input_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_cached_input_tokens,
  sum(
    coalesce(
      newly_processed_input_tokens,
      greatest(coalesce(input_tokens, 0) - coalesce(cached_input_tokens, 0), 0)
    )
  ) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_newly_processed_input_tokens,
  round(
    (
      sum(coalesce(cached_input_tokens, 0)) filter (
        where measurement_source = 'provider_reported'
      )
    )::numeric
    / nullif(
      (
        sum(coalesce(input_tokens, 0)) filter (
          where measurement_source = 'provider_reported'
        )
      )::numeric,
      0
    ),
    6
  ) as aggregate_cached_input_ratio,
  sum(output_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_output_tokens,
  sum(reasoning_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_reasoning_tokens,
  sum(total_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_total_tokens,
  sum(original_tokens) as aggregate_original_tokens,
  sum(compressed_tokens) as aggregate_compressed_tokens,
  sum(tokens_saved) as aggregate_tokens_saved,
  count(distinct transforms #>> '{{cache_hot_zone,stable_top_level_hash}}')
    as distinct_stable_top_level_hashes,
  count(distinct transforms #>> '{{cache_hot_zone,stable_input_prefix_hash}}')
    as distinct_stable_input_prefix_hashes,
  count(
    distinct transforms
      #>> '{{cache_hot_zone,stable_prefix_without_prompt_cache_key_hash}}'
  ) as distinct_stable_prefix_without_prompt_cache_key_hashes,
  count(
    distinct transforms
      #>> '{{cache_hot_zone,stable_top_level_field_hashes,prompt_cache_key}}'
  ) as distinct_prompt_cache_key_hashes
from agent90_matched_requests;

select
  'call' as proof_row_type,
  expected_strategy_name,
  request_key,
  created_at,
  incoming_route,
  litellm_proxy_run_marker,
  litellm_proxy_project,
  litellm_proxy_client,
  correlation_source,
  strategy_name,
  strategy_version,
  execution_status,
  original_tokens,
  compressed_tokens,
  tokens_saved,
  compression_ratio,
  transforms,
  provider_call_key,
  litellm_call_id,
  provider_request_id,
  provider_response_id,
  provider,
  model,
  provider_status,
  measurement_source,
  input_tokens,
  cached_input_tokens,
  newly_processed_input_tokens,
  cache_write_tokens,
  output_tokens,
  reasoning_tokens,
  total_tokens,
  cost_total,
  currency
from agent90_matched_requests
order by created_at desc
limit 20;

drop table if exists agent90_matched_requests;
"""


def _db_matched_requests_cte_sql(
    started_at_placeholder: str,
    ended_at_placeholder: str,
    marker_placeholder: str,
    savings_profile_placeholder: str,
    grace_seconds: int,
) -> str:
    return f"""with agent90_matched_requests as (
select
  '{savings_profile_placeholder}' as expected_strategy_name,
  cr.request_key,
  cr.created_at,
  cr.incoming_route,
  cr.request_metadata->>'litellm_proxy_run_marker' as litellm_proxy_run_marker,
  cr.request_metadata->>'litellm_proxy_project' as litellm_proxy_project,
  cr.request_metadata->>'litellm_proxy_client' as litellm_proxy_client,
  case
    when cr.request_metadata->>'litellm_proxy_run_marker' = '{marker_placeholder}'
    then 'marker'
    else 'time_window'
  end as correlation_source,
  c.strategy_name,
  c.strategy_version,
  ce.status as execution_status,
  ce.original_tokens,
  ce.compressed_tokens,
  ce.tokens_saved,
  ce.compression_ratio,
  ce.transforms,
  pc.provider_call_key,
  pc.litellm_call_id,
  pc.provider_request_id,
  pc.provider_response_id,
  pc.provider,
  pc.model,
  pc.status as provider_status,
  tub.measurement_source,
  tub.input_tokens,
  tub.cached_input_tokens,
  tub.newly_processed_input_tokens,
  tub.cache_write_tokens,
  tub.output_tokens,
  tub.reasoning_tokens,
  tub.total_tokens,
  pc.cost_total,
  pc.currency
from compression_requests cr
join compression_executions ce on ce.request_id = cr.id
join compression_config_snapshots c on c.id = ce.config_snapshot_id
left join provider_calls pc on pc.execution_id = ce.id
left join token_usage_breakdowns tub on tub.provider_call_id = pc.id
where cr.incoming_route = '/v1/responses'
  and (
    cr.request_metadata->>'litellm_proxy_run_marker' = '{marker_placeholder}'
    or (
      cr.created_at >= '{started_at_placeholder}'::timestamptz
      and cr.created_at <= (
        '{ended_at_placeholder}'::timestamptz + interval '{grace_seconds} seconds'
      )
    )
  )
)"""


def _db_aggregate_csv_sql(
    started_at_placeholder: str = "<proxy_started_at_utc>",
    ended_at_placeholder: str = "<proxy_ended_at_utc>",
    marker_placeholder: str = "<marker>",
    savings_profile_placeholder: str = "<expected_savings_profile>",
    grace_seconds: int = 300,
) -> str:
    return (
        _db_matched_requests_cte_sql(
            started_at_placeholder,
            ended_at_placeholder,
            marker_placeholder,
            savings_profile_placeholder,
            grace_seconds,
        )
        + f"""
select
  'aggregate' as proof_row_type,
  '{savings_profile_placeholder}' as expected_strategy_name,
  '{marker_placeholder}' as requested_marker,
  min(created_at) as first_request_at,
  max(created_at) as last_request_at,
  count(distinct request_key) as request_count,
  count(*) as execution_row_count,
  count(provider_call_key) filter (
    where measurement_source = 'provider_reported'
  ) as provider_reported_call_count,
  string_agg(distinct model, ', ' order by model) filter (
    where model is not null
  ) as models,
  sum(input_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_input_tokens,
  sum(cached_input_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_cached_input_tokens,
  sum(
    coalesce(
      newly_processed_input_tokens,
      greatest(coalesce(input_tokens, 0) - coalesce(cached_input_tokens, 0), 0)
    )
  ) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_newly_processed_input_tokens,
  round(
    (
      sum(coalesce(cached_input_tokens, 0)) filter (
        where measurement_source = 'provider_reported'
      )
    )::numeric
    / nullif(
      (
        sum(coalesce(input_tokens, 0)) filter (
          where measurement_source = 'provider_reported'
        )
      )::numeric,
      0
    ),
    6
  ) as aggregate_cached_input_ratio,
  sum(output_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_output_tokens,
  sum(reasoning_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_reasoning_tokens,
  sum(total_tokens) filter (
    where measurement_source = 'provider_reported'
  ) as aggregate_total_tokens,
  sum(original_tokens) as aggregate_original_tokens,
  sum(compressed_tokens) as aggregate_compressed_tokens,
  sum(tokens_saved) as aggregate_tokens_saved,
  count(distinct transforms #>> '{{cache_hot_zone,stable_top_level_hash}}')
    as distinct_stable_top_level_hashes,
  count(distinct transforms #>> '{{cache_hot_zone,stable_input_prefix_hash}}')
    as distinct_stable_input_prefix_hashes,
  count(
    distinct transforms
      #>> '{{cache_hot_zone,stable_prefix_without_prompt_cache_key_hash}}'
  ) as distinct_stable_prefix_without_prompt_cache_key_hashes,
  count(
    distinct transforms
      #>> '{{cache_hot_zone,stable_top_level_field_hashes,prompt_cache_key}}'
  ) as distinct_prompt_cache_key_hashes
from agent90_matched_requests;
"""
    )


def _db_rows_csv_sql(
    started_at_placeholder: str = "<proxy_started_at_utc>",
    ended_at_placeholder: str = "<proxy_ended_at_utc>",
    marker_placeholder: str = "<marker>",
    savings_profile_placeholder: str = "<expected_savings_profile>",
    grace_seconds: int = 300,
) -> str:
    return (
        _db_matched_requests_cte_sql(
            started_at_placeholder,
            ended_at_placeholder,
            marker_placeholder,
            savings_profile_placeholder,
            grace_seconds,
        )
        + """
select
  'call' as proof_row_type,
  expected_strategy_name,
  request_key,
  created_at,
  incoming_route,
  litellm_proxy_run_marker,
  litellm_proxy_project,
  litellm_proxy_client,
  correlation_source,
  strategy_name,
  strategy_version,
  execution_status,
  original_tokens,
  compressed_tokens,
  tokens_saved,
  compression_ratio,
  transforms,
  provider_call_key,
  litellm_call_id,
  provider_request_id,
  provider_response_id,
  provider,
  model,
  provider_status,
  measurement_source,
  input_tokens,
  cached_input_tokens,
  newly_processed_input_tokens,
  cache_write_tokens,
  output_tokens,
  reasoning_tokens,
  total_tokens,
  cost_total,
  currency
from agent90_matched_requests
order by created_at desc;
"""
    )


def _db_artifacts(artifact_dir: Path) -> dict[str, str]:
    proxy_dir = artifact_dir / "proxy"
    return {
        "query": str(proxy_dir / "db-proof.sql"),
        "stdout": str(proxy_dir / "db-proof.stdout.txt"),
        "stderr": str(proxy_dir / "db-proof.stderr.txt"),
        "result": str(proxy_dir / "db-proof-result.json"),
        "aggregate_query": str(proxy_dir / "db-proof-aggregate.sql"),
        "aggregate_csv": str(proxy_dir / "db-proof-aggregate.csv"),
        "aggregate_json": str(proxy_dir / "db-proof-aggregate.json"),
        "aggregate_stderr": str(proxy_dir / "db-proof-aggregate.stderr.txt"),
        "rows_query": str(proxy_dir / "db-proof-rows.sql"),
        "rows_csv": str(proxy_dir / "db-proof-rows.csv"),
        "rows_json": str(proxy_dir / "db-proof-rows.json"),
        "rows_stderr": str(proxy_dir / "db-proof-rows.stderr.txt"),
    }


def _lane_artifacts(artifact_dir: Path, lane: str) -> dict[str, str]:
    lane_dir = artifact_dir / lane
    return {
        "dir": str(lane_dir),
        "command": str(lane_dir / "command.json"),
        "environment": str(lane_dir / "environment.json"),
        "stdout": str(lane_dir / "stdout.txt"),
        "stderr": str(lane_dir / "stderr.txt"),
        "summary_lines": str(lane_dir / "summary-lines.txt"),
        "token_summary": str(lane_dir / "token-summary.json"),
        "result": str(lane_dir / "result.json"),
    }


def _preflight_artifacts(artifact_dir: Path) -> dict[str, str]:
    return {"result": str(artifact_dir / "preflight-result.json")}


def _litellm_models_url(litellm_url: str) -> str:
    base_url = litellm_url.rstrip("/")
    if base_url.endswith("/v1"):
        return f"{base_url}/models"
    return f"{base_url}/v1/models"


def _litellm_openai_base_url(litellm_url: str) -> str:
    base_url = litellm_url.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _litellm_callbacks_url(litellm_url: str) -> str:
    base_url = litellm_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}/callbacks/list"


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    workdir = Path(args.workdir).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    artifact_dir = artifact_root / args.marker
    prompt = _task_prompt(args.marker, args.task_lines)
    direct_command = _lane_command(
        args.codex_bin,
        workdir,
        prompt,
        args.model,
        args.reasoning_effort,
        args.model_verbosity,
    )
    proxy_command = _lane_command(
        str(Path(args.proxy_bin)),
        workdir,
        prompt,
        args.model,
        args.reasoning_effort,
        args.model_verbosity,
    )
    db_artifacts = _db_artifacts(artifact_dir)

    return {
        "mode": "execute" if args.execute else "dry-run",
        "marker": args.marker,
        "created_at": _utc_now(),
        "workdir": str(workdir),
        "artifact_dir": str(artifact_dir),
        "task": {
            "lines": args.task_lines,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "model_verbosity": args.model_verbosity,
            "expected_savings_profile": args.savings_profile,
            "prompt": prompt,
        },
        "preflight": {
            "enabled": not args.skip_preflight,
            "timeout_seconds": args.preflight_timeout,
            "litellm_url": args.litellm_url.rstrip("/"),
            "model_list_url": _litellm_models_url(args.litellm_url),
            "require_model_available": True,
            "model": args.model,
            "callback_list_url": _litellm_callbacks_url(args.litellm_url),
            "require_callback_loaded": True,
            "expected_callback": DEFAULT_LITELLM_CALLBACK,
            "analytics_url": args.analytics_url.rstrip("/"),
            "require_analytics_ready": bool(args.query_db),
            "artifacts": _preflight_artifacts(artifact_dir),
        },
        "lanes": {
            "direct": {
                "purpose": "host Codex without the repo LiteLLM wrapper",
                "command": direct_command,
                "artifacts": _lane_artifacts(artifact_dir, "direct"),
            },
            "proxy": {
                "purpose": "Codex through ./bin/codex-litellm and LiteLLM /v1/responses",
                "command": proxy_command,
                "environment": {
                    CODEX_LITELLM_BASE_URL_ENV: _litellm_openai_base_url(
                        args.litellm_url
                    ),
                    CODEX_LITELLM_ANALYTICS_URL_ENV: args.analytics_url.rstrip("/"),
                    PROXY_RUN_MARKER_ENV: args.marker,
                    CODEX_LITELLM_CLIENT_ENV: "codex",
                    CODEX_LITELLM_MODEL_ENV: args.model,
                    CODEX_LITELLM_REASONING_EFFORT_ENV: args.reasoning_effort,
                    CODEX_LITELLM_MODEL_VERBOSITY_ENV: args.model_verbosity,
                },
                "artifacts": _lane_artifacts(artifact_dir, "proxy"),
            },
        },
        "proxy_db": {
            "query_file": db_artifacts["query"],
            "artifacts": db_artifacts,
            "query_template": _db_query_sql(
                savings_profile_placeholder=args.savings_profile,
                grace_seconds=args.db_window_grace_seconds,
            ),
            "window_grace_seconds": args.db_window_grace_seconds,
            "manual_command": [
                args.docker_bin,
                "compose",
                "exec",
                "-T",
                "analytics-db",
                "psql",
                "-U",
                "analytics",
                "-d",
                "analytics",
                "-v",
                "ON_ERROR_STOP=1",
                "-P",
                "pager=off",
                "-f",
                "-",
            ],
            "csv_manual_command": [
                args.docker_bin,
                "compose",
                "exec",
                "-T",
                "analytics-db",
                "psql",
                "-U",
                "analytics",
                "-d",
                "analytics",
                "-v",
                "ON_ERROR_STOP=1",
                "-P",
                "pager=off",
                "--csv",
                "-f",
                "-",
            ],
            "manual_command_stdin_file": db_artifacts["query"],
            "aggregate_query_template": _db_aggregate_csv_sql(
                savings_profile_placeholder=args.savings_profile,
                grace_seconds=args.db_window_grace_seconds,
            ),
            "rows_query_template": _db_rows_csv_sql(
                savings_profile_placeholder=args.savings_profile,
                grace_seconds=args.db_window_grace_seconds,
            ),
        },
        "stop_rules": [
            "Do not print or inspect ChatGPT/Codex auth token contents.",
            "Preflight checks must pass before either provider-spending lane runs.",
            "Preflight must confirm LiteLLM advertises the configured model.",
            "Preflight must confirm LiteLLM loaded the local Headroom callback.",
            "Both lanes run with approval policy 'never' and sandbox 'read-only'.",
            "Both lanes pin the same Codex model with -m/--model.",
            "Both lanes pin the same Codex reasoning effort with -c model_reasoning_effort.",
            "Both lanes pin the same Codex model verbosity with -c model_verbosity.",
            "Proxy lane must use the generated litellm Codex profile.",
            "Proxy lane LiteLLM base URL must match the preflighted --litellm-url.",
            "Proxy lane analytics MCP URL must match the configured --analytics-url.",
            "Proxy DB rows must report the expected Headroom strategy profile.",
            "Abort instead of overwriting an existing artifact directory unless --force is set.",
            "Treat smoke/demo rows as test data; usefulness requires provider-reported direct-vs-proxy evidence.",
            "Do not claim MVP usefulness from proxy DB rows alone.",
        ],
    }


def _preflight_tcp_url(url: str, *, timeout: float) -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        return {
            "name": "litellm_tcp",
            "ok": False,
            "url": url,
            "error": "missing host",
        }
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    started_at = _utc_now()
    try:
        import socket

        with socket.create_connection((host, port), timeout=timeout):
            return {
                "name": "litellm_tcp",
                "ok": True,
                "url": url,
                "host": host,
                "port": port,
                "started_at": started_at,
                "ended_at": _utc_now(),
            }
    except OSError as exc:
        return {
            "name": "litellm_tcp",
            "ok": False,
            "url": url,
            "host": host,
            "port": port,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }


def _preflight_http_get(url: str, *, timeout: float, name: str) -> dict[str, Any]:
    started_at = _utc_now()
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            body = response.read(2048).decode("utf-8", errors="replace")
        return {
            "name": name,
            "ok": 200 <= status < 400,
            "url": url,
            "status": status,
            "body_prefix": body[:256],
            "started_at": started_at,
            "ended_at": _utc_now(),
        }
    except URLError as exc:
        return {
            "name": name,
            "ok": False,
            "url": url,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }
    except OSError as exc:
        return {
            "name": name,
            "ok": False,
            "url": url,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }


def _extract_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    model_ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str):
            model_ids.append(model_id)
    return model_ids


def _extract_callbacks(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []

    callbacks: list[str] = []
    for field in ("success", "failure", "success_and_failure"):
        values = payload.get(field)
        if not isinstance(values, list):
            continue
        callbacks.extend(value for value in values if isinstance(value, str))
    return sorted(set(callbacks))


def _litellm_auth_headers() -> tuple[dict[str, str], str]:
    api_key = os.environ.get(LITELLM_MASTER_KEY_ENV, "").strip()
    if not api_key:
        return {}, "none"
    return {"Authorization": f"Bearer {api_key}"}, LITELLM_MASTER_KEY_ENV


def _preflight_litellm_model(url: str, *, model: str, timeout: float) -> dict[str, Any]:
    started_at = _utc_now()
    auth_headers, auth_source = _litellm_auth_headers()
    auth_header_used = bool(auth_headers)
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                **auth_headers,
            },
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return {
            "name": "litellm_model_available",
            "ok": False,
            "url": url,
            "model": model,
            "status": exc.code,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": f"HTTP {exc.code} from LiteLLM model list",
        }
    except URLError as exc:
        return {
            "name": "litellm_model_available",
            "ok": False,
            "url": url,
            "model": model,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }
    except OSError as exc:
        return {
            "name": "litellm_model_available",
            "ok": False,
            "url": url,
            "model": model,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return {
            "name": "litellm_model_available",
            "ok": False,
            "url": url,
            "model": model,
            "status": status,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": f"invalid JSON from LiteLLM model list: {exc.msg}",
        }

    model_ids = _extract_model_ids(payload)
    matched_model = model if model in model_ids else None
    ok = 200 <= status < 400 and matched_model is not None
    result = {
        "name": "litellm_model_available",
        "ok": ok,
        "url": url,
        "model": model,
        "status": status,
        "model_count": len(model_ids),
        "matched_model": matched_model,
        "auth_header_used": auth_header_used,
        "auth_source": auth_source,
        "started_at": started_at,
        "ended_at": _utc_now(),
    }
    if not ok:
        result["error"] = "configured model is not advertised by LiteLLM /v1/models"
    return result


def _preflight_litellm_callback(
    url: str, *, expected_callback: str, timeout: float
) -> dict[str, Any]:
    started_at = _utc_now()
    auth_headers, auth_source = _litellm_auth_headers()
    auth_header_used = bool(auth_headers)
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                **auth_headers,
            },
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return {
            "name": "litellm_callback_loaded",
            "ok": False,
            "url": url,
            "expected_callback": expected_callback,
            "status": exc.code,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": f"HTTP {exc.code} from LiteLLM callback list",
        }
    except URLError as exc:
        return {
            "name": "litellm_callback_loaded",
            "ok": False,
            "url": url,
            "expected_callback": expected_callback,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }
    except OSError as exc:
        return {
            "name": "litellm_callback_loaded",
            "ok": False,
            "url": url,
            "expected_callback": expected_callback,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": str(exc),
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return {
            "name": "litellm_callback_loaded",
            "ok": False,
            "url": url,
            "expected_callback": expected_callback,
            "status": status,
            "auth_header_used": auth_header_used,
            "auth_source": auth_source,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "error": f"invalid JSON from LiteLLM callback list: {exc.msg}",
        }

    callbacks = _extract_callbacks(payload)
    matched_callback = expected_callback if expected_callback in callbacks else None
    ok = 200 <= status < 400 and matched_callback is not None
    result = {
        "name": "litellm_callback_loaded",
        "ok": ok,
        "url": url,
        "expected_callback": expected_callback,
        "status": status,
        "callback_count": len(callbacks),
        "matched_callback": matched_callback,
        "auth_header_used": auth_header_used,
        "auth_source": auth_source,
        "started_at": started_at,
        "ended_at": _utc_now(),
    }
    if not ok:
        result["error"] = (
            "local Headroom callback is not advertised by LiteLLM /callbacks/list"
        )
    return result


def _run_preflight(plan: dict[str, Any]) -> dict[str, Any]:
    config = plan["preflight"]
    timeout = float(config["timeout_seconds"])
    checks = [
        _preflight_tcp_url(str(config["litellm_url"]), timeout=timeout),
        _preflight_litellm_model(
            str(config["model_list_url"]),
            model=str(config["model"]),
            timeout=timeout,
        ),
        _preflight_litellm_callback(
            str(config["callback_list_url"]),
            expected_callback=str(config["expected_callback"]),
            timeout=timeout,
        ),
    ]
    if config["require_analytics_ready"]:
        analytics_url = str(config["analytics_url"]).rstrip("/")
        checks.append(
            _preflight_http_get(
                f"{analytics_url}/ready",
                timeout=timeout,
                name="analytics_ready",
            )
        )
    return {
        "enabled": bool(config["enabled"]),
        "ok": all(bool(check["ok"]) for check in checks),
        "checks": checks,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _run_lane(
    name: str, lane: dict[str, Any], cwd: Path, timeout: int
) -> dict[str, Any]:
    artifacts = {key: Path(value) for key, value in lane["artifacts"].items()}
    artifacts["dir"].mkdir(parents=True, exist_ok=True)
    _write_json(artifacts["command"], lane["command"])
    lane_environment = dict(lane.get("environment") or {})
    _write_json(artifacts["environment"], lane_environment)

    started_at = _utc_now()
    env = os.environ.copy()
    env.update(lane_environment)
    completed = subprocess.run(
        lane["command"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    ended_at = _utc_now()

    _write_text(artifacts["stdout"], completed.stdout)
    _write_text(artifacts["stderr"], completed.stderr)
    token_summary = parse_token_summary(
        {"stdout": completed.stdout, "stderr": completed.stderr}
    )
    _write_text(artifacts["summary_lines"], _token_summary_lines(token_summary))
    _write_json(artifacts["token_summary"], token_summary)
    result = {
        "lane": name,
        "started_at": started_at,
        "ended_at": ended_at,
        "returncode": completed.returncode,
        "stdout": str(artifacts["stdout"]),
        "stderr": str(artifacts["stderr"]),
        "environment": str(artifacts["environment"]),
        "summary_lines": str(artifacts["summary_lines"]),
        "token_summary_file": str(artifacts["token_summary"]),
        "token_summary": token_summary,
    }
    _write_json(artifacts["result"], result)
    return result


def _run_db_query(plan: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    artifacts = {
        key: Path(value) for key, value in plan["proxy_db"]["artifacts"].items()
    }
    query_text = artifacts["query"].read_text()
    started_at = _utc_now()
    completed = subprocess.run(
        plan["proxy_db"]["manual_command"],
        cwd=Path(plan["workdir"]),
        input=query_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    ended_at = _utc_now()

    _write_text(artifacts["stdout"], completed.stdout)
    _write_text(artifacts["stderr"], completed.stderr)
    aggregate_result = None
    rows_result = None
    if completed.returncode == 0:
        aggregate_result = _run_structured_db_query(
            plan,
            query_text=artifacts["aggregate_query"].read_text(),
            stdout_path=artifacts["aggregate_csv"],
            stderr_path=artifacts["aggregate_stderr"],
            json_path=artifacts["aggregate_json"],
            expect_single=True,
            timeout=timeout,
        )
        rows_result = _run_structured_db_query(
            plan,
            query_text=artifacts["rows_query"].read_text(),
            stdout_path=artifacts["rows_csv"],
            stderr_path=artifacts["rows_stderr"],
            json_path=artifacts["rows_json"],
            expect_single=False,
            timeout=timeout,
        )
    structured_returncodes = [
        result["returncode"]
        for result in (aggregate_result, rows_result)
        if result is not None
    ]
    returncode = completed.returncode
    if returncode == 0 and any(code != 0 for code in structured_returncodes):
        returncode = next(code for code in structured_returncodes if code != 0)
    result = {
        "started_at": started_at,
        "ended_at": ended_at,
        "returncode": returncode,
        "text_returncode": completed.returncode,
        "command": plan["proxy_db"]["manual_command"],
        "query": str(artifacts["query"]),
        "stdin": str(artifacts["query"]),
        "stdout": str(artifacts["stdout"]),
        "stderr": str(artifacts["stderr"]),
        "aggregate_result": aggregate_result,
        "rows_result": rows_result,
        "structured_artifacts": {
            "aggregate_csv": str(artifacts["aggregate_csv"]),
            "aggregate_json": str(artifacts["aggregate_json"]),
            "rows_csv": str(artifacts["rows_csv"]),
            "rows_json": str(artifacts["rows_json"]),
        },
    }
    _write_json(artifacts["result"], result)
    return result


def _csv_rows(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def _run_structured_db_query(
    plan: dict[str, Any],
    *,
    query_text: str,
    stdout_path: Path,
    stderr_path: Path,
    json_path: Path,
    expect_single: bool,
    timeout: int,
) -> dict[str, Any]:
    started_at = _utc_now()
    completed = subprocess.run(
        plan["proxy_db"]["csv_manual_command"],
        cwd=Path(plan["workdir"]),
        input=query_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    ended_at = _utc_now()

    _write_text(stdout_path, completed.stdout)
    _write_text(stderr_path, completed.stderr)
    rows = _csv_rows(completed.stdout) if completed.returncode == 0 else []
    payload: Any = rows[0] if expect_single and rows else {} if expect_single else rows
    _write_json(json_path, payload)
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "returncode": completed.returncode,
        "command": plan["proxy_db"]["csv_manual_command"],
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "json": str(json_path),
        "row_count": len(rows),
    }


def execute_plan(
    plan: dict[str, Any],
    *,
    force: bool,
    timeout: int,
    query_db: bool,
    db_timeout: int,
) -> int:
    artifact_dir = Path(plan["artifact_dir"])
    if artifact_dir.exists() and not force:
        print(
            f"agent90_usefulness=failed artifact_dir_exists path={artifact_dir}",
            file=sys.stderr,
        )
        return 1

    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifact_dir / "plan.json", plan)

    preflight_result = None
    if plan["preflight"]["enabled"]:
        preflight_result = _run_preflight(plan)
        _write_json(
            Path(plan["preflight"]["artifacts"]["result"]),
            preflight_result,
        )
        if not preflight_result["ok"]:
            _write_json(
                artifact_dir / "summary.json",
                {
                    "marker": plan["marker"],
                    "artifact_dir": str(artifact_dir),
                    "preflight_result": preflight_result,
                    "results": [],
                    "token_comparison": _compare_token_summaries([]),
                },
            )
            print(
                "agent90_usefulness=failed "
                f"preflight=failed artifact_dir={artifact_dir}",
                file=sys.stderr,
            )
            return 1

    results: list[dict[str, Any]] = []
    cwd = Path(plan["workdir"])
    for lane_name in ("direct", "proxy"):
        lane = plan["lanes"][lane_name]
        result = _run_lane(lane_name, lane, cwd, timeout)
        results.append(result)
        if result["returncode"] != 0:
            _write_json(
                artifact_dir / "summary.json",
                {
                    "marker": plan["marker"],
                    "artifact_dir": str(artifact_dir),
                    "preflight_result": preflight_result,
                    "results": results,
                    "token_comparison": _compare_token_summaries(results),
                },
            )
            print(
                "agent90_usefulness=failed "
                f"lane={lane_name} returncode={result['returncode']} "
                f"artifact_dir={artifact_dir}",
                file=sys.stderr,
            )
            return result["returncode"]

    proxy_started_at = results[-1]["started_at"]
    proxy_ended_at = results[-1]["ended_at"]
    query_path = Path(plan["proxy_db"]["query_file"])
    aggregate_query_path = Path(plan["proxy_db"]["artifacts"]["aggregate_query"])
    rows_query_path = Path(plan["proxy_db"]["artifacts"]["rows_query"])
    _write_text(
        query_path,
        _db_query_sql(
            proxy_started_at,
            proxy_ended_at,
            plan["marker"],
            str(plan["task"]["expected_savings_profile"]),
            grace_seconds=int(plan["proxy_db"]["window_grace_seconds"]),
        ),
    )
    _write_text(
        aggregate_query_path,
        _db_aggregate_csv_sql(
            proxy_started_at,
            proxy_ended_at,
            plan["marker"],
            str(plan["task"]["expected_savings_profile"]),
            grace_seconds=int(plan["proxy_db"]["window_grace_seconds"]),
        ),
    )
    _write_text(
        rows_query_path,
        _db_rows_csv_sql(
            proxy_started_at,
            proxy_ended_at,
            plan["marker"],
            str(plan["task"]["expected_savings_profile"]),
            grace_seconds=int(plan["proxy_db"]["window_grace_seconds"]),
        ),
    )
    proxy_db_result = _run_db_query(plan, timeout=db_timeout) if query_db else None
    summary = {
        "marker": plan["marker"],
        "artifact_dir": str(artifact_dir),
        "preflight_result": preflight_result,
        "results": results,
        "token_comparison": _compare_token_summaries(results),
        "proxy_db_query_file": str(query_path),
        "proxy_db_aggregate_query_file": str(aggregate_query_path),
        "proxy_db_rows_query_file": str(rows_query_path),
        "proxy_db_result": proxy_db_result,
        "proxy_db_manual_command": plan["proxy_db"]["manual_command"],
        "proxy_db_csv_manual_command": plan["proxy_db"]["csv_manual_command"],
    }
    _write_json(artifact_dir / "summary.json", summary)
    if proxy_db_result is not None and proxy_db_result["returncode"] != 0:
        print(
            "agent90_usefulness=failed "
            f"proxy_db_returncode={proxy_db_result['returncode']} "
            f"artifact_dir={artifact_dir}",
            file=sys.stderr,
        )
        return proxy_db_result["returncode"]
    mvp_usefulness = summary["token_comparison"]["mvp_usefulness"]
    if mvp_usefulness["status"] == "fail":
        print(
            "agent90_usefulness=failed "
            f"mvp_usefulness=fail reasons={','.join(mvp_usefulness['fail_reasons'])} "
            f"artifact_dir={artifact_dir}",
            file=sys.stderr,
        )
        return 2
    completion_contract = summary["token_comparison"]["completion_contract"]
    if completion_contract["status"] != "pass":
        reasons = ",".join(completion_contract["missing_reasons"])
        print(
            "agent90_usefulness=failed "
            f"completion_contract={completion_contract['status']} "
            f"reasons={reasons} artifact_dir={artifact_dir}",
            file=sys.stderr,
        )
        return 2
    print(
        "agent90_usefulness=ok "
        f"scope={completion_contract['scope']} "
        f"cost={completion_contract['cost_status']} "
        f"marker={plan['marker']} artifact_dir={artifact_dir} "
        f"proxy_db_query={query_path}"
    )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Define and optionally run a bounded direct Codex vs "
            "./bin/codex-litellm usefulness proof."
        )
    )
    parser.add_argument("--marker", type=_validate_marker, default=_default_marker())
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--workdir", default=str(REPO_ROOT))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--proxy-bin", default=str(REPO_ROOT / "bin" / "codex-litellm"))
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument(
        "--litellm-url",
        type=_validate_http_base_url,
        default=DEFAULT_LITELLM_URL,
    )
    parser.add_argument(
        "--analytics-url",
        type=_validate_http_base_url,
        default=DEFAULT_ANALYTICS_URL,
    )
    parser.add_argument(
        "--model",
        type=_validate_model,
        default=os.environ.get(CODEX_LITELLM_MODEL_ENV, DEFAULT_MODEL),
        help="Codex model to pin on both direct and proxy lanes.",
    )
    parser.add_argument(
        "--reasoning-effort",
        type=_validate_reasoning_effort,
        default=os.environ.get(
            CODEX_LITELLM_REASONING_EFFORT_ENV, DEFAULT_REASONING_EFFORT
        ),
        help="Codex model_reasoning_effort to pin on both direct and proxy lanes.",
    )
    parser.add_argument(
        "--model-verbosity",
        type=_validate_model_verbosity,
        default=os.environ.get(
            CODEX_LITELLM_MODEL_VERBOSITY_ENV, DEFAULT_MODEL_VERBOSITY
        ),
        help="Codex model_verbosity to pin on both direct and proxy lanes.",
    )
    parser.add_argument(
        "--savings-profile",
        type=_validate_savings_profile,
        default=os.environ.get("HEADROOM_SAVINGS_PROFILE", DEFAULT_SAVINGS_PROFILE),
        help="Expected Headroom strategy profile in proxy DB proof rows.",
    )
    parser.add_argument("--task-lines", type=int, default=220)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--db-timeout", type=int, default=60)
    parser.add_argument("--db-window-grace-seconds", type=int, default=300)
    parser.add_argument("--preflight-timeout", type=float, default=2.0)
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip local LiteLLM/analytics readiness checks before --execute.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run both lanes and write artifacts. Default is dry-run only.",
    )
    parser.add_argument(
        "--query-db",
        action="store_true",
        help="After --execute, run the proxy DB proof query and write artifacts.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --execute to reuse an existing artifact directory.",
    )
    args = parser.parse_args(argv)
    try:
        args.reasoning_effort = _validate_reasoning_effort(args.reasoning_effort)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    try:
        args.model_verbosity = _validate_model_verbosity(args.model_verbosity)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if args.task_lines < 1:
        parser.error("--task-lines must be positive")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.db_timeout < 1:
        parser.error("--db-timeout must be positive")
    if args.db_window_grace_seconds < 0:
        parser.error("--db-window-grace-seconds must be nonnegative")
    if args.preflight_timeout <= 0:
        parser.error("--preflight-timeout must be positive")
    if args.query_db and not args.execute:
        parser.error("--query-db requires --execute")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    plan = build_plan(args)
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    return execute_plan(
        plan,
        force=args.force,
        timeout=args.timeout,
        query_db=args.query_db,
        db_timeout=args.db_timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
