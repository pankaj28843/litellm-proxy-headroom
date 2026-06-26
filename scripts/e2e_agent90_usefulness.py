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
ACCOUNT_SNAPSHOT_SCRIPT = REPO_ROOT / "scripts" / "codex_account_snapshot.py"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_DIRECT_MODEL_PROVIDER = "openai"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_MODEL_VERBOSITY = "medium"
DEFAULT_SAVINGS_PROFILE = "agent-90"
DEFAULT_LITELLM_CALLBACK = "HeadroomCallback"
DEFAULT_LITELLM_URL = "http://127.0.0.1:4000"
DEFAULT_ANALYTICS_URL = "http://127.0.0.1:8010"
DEFAULT_ACCOUNT_SNAPSHOT_TIMEOUT = 20.0
DEFAULT_ACCOUNT_SNAPSHOT_ATTEMPTS = 2
DEFAULT_ACCOUNT_SNAPSHOT_RETRY_DELAY_SECONDS = 1.0
DEFAULT_CACHED_INPUT_COST_MULTIPLIER = 0.10
DEFAULT_MAX_CACHE_RATIO_DROP = 0.05
DEFAULT_SESSION_TURNS = 1
DEFAULT_MIN_COMBINED_INPUT_TOKENS = 0
DEFAULT_LANE_ORDER = "direct,proxy"
SESSION_ID_PLACEHOLDER = "<session-id-from-turn-1>"
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
CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH_ENV = (
    "CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH"
)
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
CCR_REF_RE = re.compile(
    r"<<ccr:[^,>]+,[^,>]+,(?P<size>\d+(?:\.\d+)?)(?P<unit>B|KB|MB)>>"
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


def aggregate_turn_token_summaries(
    turn_summaries: list[dict[str, Any]],
    *,
    cumulative: bool = False,
) -> dict[str, Any]:
    if len(turn_summaries) == 1:
        return {**turn_summaries[0], "turn_count": 1}

    if cumulative:
        latest = dict(turn_summaries[-1])
        latest["usage_source"] = (
            "codex_json_turn_completed_cumulative_latest_across_resumed_session"
        )
        latest["turn_count"] = len(turn_summaries)
        latest["turn_summaries"] = turn_summaries
        latest["aggregation_note"] = (
            "Codex exec resume reports cumulative session usage; the lane "
            "summary uses the latest turn.completed usage event instead of "
            "summing resumed turns."
        )
        return latest

    values: dict[str, int | None] = {}
    missing_fields: list[str] = []
    for field in TOKEN_FIELDS:
        field_values = [summary.get(field) for summary in turn_summaries]
        if all(isinstance(value, int) for value in field_values):
            values[field] = sum(int(value) for value in field_values)
        else:
            values[field] = None
            missing_fields.append(field)

    cost_values = [summary.get("cost_usd") for summary in turn_summaries]
    cost_usd = None
    if all(value is not None for value in cost_values):
        cost_usd = format(
            sum(Decimal(str(value)) for value in cost_values),
            "f",
        )

    source_lines: list[dict[str, Any]] = []
    for turn_index, summary in enumerate(turn_summaries, start=1):
        for source in summary.get("source_lines", []):
            source_lines.append({**source, "turn_index": turn_index})

    field_sources = {
        field: {
            "source": "aggregate_codex_exec_turns",
            "turn_count": len(turn_summaries),
            "turns_with_field": [
                index
                for index, summary in enumerate(turn_summaries, start=1)
                if summary.get(field) is not None
            ],
        }
        for field in TOKEN_FIELDS
    }
    return {
        **values,
        "usage_source": "aggregate_codex_exec_turns",
        "json_turn_completed_count": sum(
            int(summary.get("json_turn_completed_count") or 0)
            for summary in turn_summaries
        ),
        "cost_usd": cost_usd,
        "cost_complete": cost_usd is not None,
        "cost_source": {
            "source": "aggregate_codex_exec_turns",
            "turn_count": len(turn_summaries),
        }
        if cost_usd is not None
        else None,
        "complete": not missing_fields,
        "missing_fields": missing_fields,
        "field_sources": field_sources,
        "source_lines": source_lines,
        "turn_count": len(turn_summaries),
        "turn_summaries": turn_summaries,
    }


def _increment_counter(counter: dict[str, int], key: Any) -> None:
    normalized = str(key) if key not in (None, "") else "<missing>"
    counter[normalized] = counter.get(normalized, 0) + 1


def _parse_size_bytes(value: str, unit: str) -> int:
    multiplier = {"B": 1, "KB": 1024, "MB": 1024 * 1024}[unit]
    return int(float(value) * multiplier)


def _ccr_referenced_bytes(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    total = 0
    for match in CCR_REF_RE.finditer(value):
        total += _parse_size_bytes(match.group("size"), match.group("unit"))
    return total


def parse_codex_trajectory(streams: dict[str, str]) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    item_started_counts: dict[str, int] = {}
    item_completed_counts: dict[str, int] = {}
    command_exit_code_counts: dict[str, int] = {}
    json_line_count = 0
    invalid_json_line_count = 0
    command_started = 0
    command_completed = 0
    command_succeeded = 0
    command_failed = 0
    command_incomplete = 0
    command_output_ccr_bytes = 0
    command_output_inline_chars = 0
    command_output_size_estimate = 0
    agent_message_count = 0
    agent_message_chars = 0
    error_item_count = 0
    turn_completed_count = 0
    latest_turn_usage: dict[str, int] = {}
    thread_ids: list[str] = []
    commands: list[dict[str, Any]] = []
    agent_messages: list[dict[str, Any]] = []
    error_items: list[dict[str, Any]] = []

    for stream_name, text in streams.items():
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_json_line_count += 1
                continue
            if not isinstance(payload, dict):
                invalid_json_line_count += 1
                continue
            json_line_count += 1
            event_type = payload.get("type")
            _increment_counter(event_counts, event_type)
            if event_type == "thread.started" and isinstance(
                payload.get("thread_id"), str
            ):
                thread_ids.append(payload["thread_id"])
            if event_type == "turn.completed":
                turn_completed_count += 1
                usage = _parse_codex_json_usage(line)
                if usage:
                    latest_turn_usage = usage

            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if event_type == "item.started":
                _increment_counter(item_started_counts, item_type)
            elif event_type == "item.completed":
                _increment_counter(item_completed_counts, item_type)

            if item_type == "command_execution":
                if event_type == "item.started":
                    command_started += 1
                elif event_type == "item.completed":
                    command_completed += 1
                    exit_code = item.get("exit_code")
                    _increment_counter(command_exit_code_counts, exit_code)
                    if exit_code == 0:
                        command_succeeded += 1
                    elif exit_code is None:
                        command_incomplete += 1
                    else:
                        command_failed += 1
                    aggregated_output = item.get("aggregated_output")
                    ccr_bytes = _ccr_referenced_bytes(aggregated_output)
                    command_output_ccr_bytes += ccr_bytes
                    if isinstance(aggregated_output, str) and ccr_bytes == 0:
                        command_output_inline_chars += len(aggregated_output)
                        output_size_estimate = len(aggregated_output)
                    else:
                        output_size_estimate = ccr_bytes
                    command_output_size_estimate += output_size_estimate
                    commands.append(
                        {
                            "stream": stream_name,
                            "line_number": line_number,
                            "id": item.get("id"),
                            "command": item.get("command"),
                            "status": item.get("status"),
                            "exit_code": exit_code,
                            "aggregated_output": aggregated_output,
                            "aggregated_output_ccr_bytes": ccr_bytes,
                            "aggregated_output_size_estimate": output_size_estimate,
                        }
                    )
            elif item_type == "agent_message" and event_type == "item.completed":
                text_value = item.get("text")
                text_chars = len(text_value) if isinstance(text_value, str) else 0
                agent_message_count += 1
                agent_message_chars += text_chars
                agent_messages.append(
                    {
                        "stream": stream_name,
                        "line_number": line_number,
                        "id": item.get("id"),
                        "text": text_value,
                        "chars": text_chars,
                    }
                )
            elif item_type == "error" and event_type == "item.completed":
                error_item_count += 1
                error_items.append(
                    {
                        "stream": stream_name,
                        "line_number": line_number,
                        "id": item.get("id"),
                        "message": item.get("message"),
                    }
                )

    return {
        "json_line_count": json_line_count,
        "invalid_json_line_count": invalid_json_line_count,
        "event_counts": event_counts,
        "item_started_counts": item_started_counts,
        "item_completed_counts": item_completed_counts,
        "thread_ids": thread_ids,
        "turn_completed_count": turn_completed_count,
        "latest_turn_usage": latest_turn_usage,
        "command_execution": {
            "started": command_started,
            "completed": command_completed,
            "succeeded": command_succeeded,
            "failed": command_failed,
            "incomplete": command_incomplete,
            "exit_code_counts": command_exit_code_counts,
            "aggregated_output_ccr_bytes": command_output_ccr_bytes,
            "aggregated_output_inline_chars": command_output_inline_chars,
            "aggregated_output_size_estimate": command_output_size_estimate,
            "commands": commands,
        },
        "agent_message": {
            "count": agent_message_count,
            "chars": agent_message_chars,
            "messages": agent_messages,
        },
        "error_item": {
            "count": error_item_count,
            "items": error_items,
        },
    }


def _trajectory_metric(summary: dict[str, Any] | None, path: tuple[str, ...]) -> Any:
    current: Any = summary
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _compare_trajectories(results: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = {
        result["lane"]: result.get("trajectory_summary")
        for result in results
        if result.get("lane") in {"direct", "proxy"}
    }
    direct = summaries.get("direct")
    proxy = summaries.get("proxy")
    metric_paths = {
        "json_line_count": ("json_line_count",),
        "turn_completed_count": ("turn_completed_count",),
        "command_completed": ("command_execution", "completed"),
        "command_succeeded": ("command_execution", "succeeded"),
        "command_failed": ("command_execution", "failed"),
        "command_output_ccr_bytes": (
            "command_execution",
            "aggregated_output_ccr_bytes",
        ),
        "command_output_inline_chars": (
            "command_execution",
            "aggregated_output_inline_chars",
        ),
        "command_output_size_estimate": (
            "command_execution",
            "aggregated_output_size_estimate",
        ),
        "agent_message_count": ("agent_message", "count"),
        "agent_message_chars": ("agent_message", "chars"),
        "error_item_count": ("error_item", "count"),
    }
    comparison: dict[str, Any] = {
        "status": "complete" if direct is not None and proxy is not None else "incomplete",
        "direct": {},
        "proxy": {},
        "delta_proxy_minus_direct": {},
    }
    for name, path in metric_paths.items():
        direct_value = _trajectory_metric(direct, path)
        proxy_value = _trajectory_metric(proxy, path)
        comparison["direct"][name] = direct_value
        comparison["proxy"][name] = proxy_value
        comparison["delta_proxy_minus_direct"][name] = (
            proxy_value - direct_value
            if isinstance(direct_value, int | float)
            and isinstance(proxy_value, int | float)
            else None
        )
    command_delta = comparison["delta_proxy_minus_direct"]["command_completed"]
    output_delta = comparison["delta_proxy_minus_direct"][
        "command_output_size_estimate"
    ]
    has_codex_json_events = bool(
        comparison["direct"]["json_line_count"]
        and comparison["proxy"]["json_line_count"]
    )
    comparison["interpretation"] = {
        "has_codex_json_events": has_codex_json_events,
        "same_completed_command_count": command_delta == 0,
        "same_tool_output_size_estimate": output_delta == 0,
        "provider_usage_is_trajectory_normalized": has_codex_json_events
        and command_delta == 0
        and output_delta == 0,
    }
    return comparison


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
    warning_reasons: list[str] = []
    checks: dict[str, Any] = {}

    if comparison["status"] != "complete":
        missing_reasons.append("token_summary_incomplete")
    else:
        total_delta = comparison["delta_proxy_minus_direct"]["total_tokens"]
        checks["total_tokens_not_worse"] = {
            "ok": total_delta is not None and total_delta <= 0,
            "delta_proxy_minus_direct": total_delta,
        }
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

        if not checks["total_tokens_not_worse"]["ok"]:
            total_check = checks["total_tokens_not_worse"]
            if checks["billing_equivalent_input_not_worse"]["ok"]:
                total_check["diagnostic_only"] = True
                total_check["ignored_because"] = "billing_equivalent_input_not_worse"
                warning_reasons.append("proxy_total_tokens_worse")
            else:
                fail_reasons.append("proxy_total_tokens_worse")

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
        "warning_reasons": warning_reasons,
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


def _minimum_input_token_floor(
    token_comparison: dict[str, Any],
    *,
    minimum_combined_input_tokens: int,
) -> dict[str, Any]:
    direct_input = token_comparison.get("direct", {}).get("input_tokens")
    proxy_input = token_comparison.get("proxy", {}).get("input_tokens")
    if minimum_combined_input_tokens <= 0:
        return {
            "enabled": False,
            "minimum_combined_input_tokens": minimum_combined_input_tokens,
            "combined_input_tokens": None,
            "ok": None,
        }
    combined = (
        int(direct_input) + int(proxy_input)
        if direct_input is not None and proxy_input is not None
        else None
    )
    return {
        "enabled": True,
        "minimum_combined_input_tokens": minimum_combined_input_tokens,
        "combined_input_tokens": combined,
        "direct_input_tokens": direct_input,
        "proxy_input_tokens": proxy_input,
        "ok": combined is not None and combined >= minimum_combined_input_tokens,
        "reason": None
        if combined is not None and combined >= minimum_combined_input_tokens
        else "combined_input_tokens_below_floor",
    }


def _overall_usefulness(
    *,
    account_comparison: dict[str, Any],
    token_comparison: dict[str, Any],
    minimum_input_token_floor: dict[str, Any],
) -> dict[str, Any]:
    if minimum_input_token_floor["enabled"] and not minimum_input_token_floor["ok"]:
        return {
            "status": "fail",
            "scope": "minimum_input_token_floor",
            "reason": minimum_input_token_floor["reason"],
            "fail_reasons": [str(minimum_input_token_floor["reason"])],
            "missing_reasons": [],
            "account_usefulness": account_comparison.get("usefulness"),
            "provider_diagnostic_status": token_comparison["mvp_usefulness"][
                "status"
            ],
            "cost_status": token_comparison["cost"]["status"],
        }

    if account_comparison.get("status") == "observed":
        account_usefulness = account_comparison.get("usefulness")
        provider_contract = token_comparison["completion_contract"]
        if account_usefulness == "pass":
            return {
                "status": "pass",
                "scope": "account_capacity",
                "reason": account_comparison.get("reason"),
                "fail_reasons": [],
                "missing_reasons": [],
                "account_usefulness": account_usefulness,
                "provider_diagnostic_status": provider_contract["status"],
                "provider_diagnostic_fail_reasons": provider_contract["fail_reasons"],
                "provider_diagnostic_missing_reasons": provider_contract[
                    "missing_reasons"
                ],
                "provider_diagnostic_warning_reasons": token_comparison[
                    "mvp_usefulness"
                ].get("warning_reasons", []),
                "cost_status": token_comparison["cost"]["status"],
            }
        if account_usefulness == "fail":
            return {
                "status": "fail",
                "scope": "account_capacity",
                "reason": account_comparison.get("reason"),
                "fail_reasons": account_comparison.get("fail_reasons", []),
                "missing_reasons": [],
                "account_usefulness": account_usefulness,
                "provider_diagnostic_status": provider_contract["status"],
                "cost_status": token_comparison["cost"]["status"],
            }

    completion_contract = token_comparison["completion_contract"]
    return {
        "status": completion_contract["status"],
        "scope": completion_contract["scope"],
        "reason": (
            "account_snapshot_unavailable_fell_back_to_provider_diagnostics"
        ),
        "fail_reasons": completion_contract["fail_reasons"],
        "missing_reasons": completion_contract["missing_reasons"],
        "account_usefulness": account_comparison.get("usefulness"),
        "provider_diagnostic_status": token_comparison["mvp_usefulness"]["status"],
        "cost_status": completion_contract["cost_status"],
    }


def _nested_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _metric_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return None
    return None


def _account_snapshot_metrics(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {"account_snapshot_status": "unavailable"}
    return {
        "account_snapshot_status": snapshot.get("account_snapshot_status"),
        "captured_at": snapshot.get("captured_at"),
        "codex_version": snapshot.get("codex_version"),
        "primary_used_percent": _nested_get(
            snapshot, ("rate_limits", "rateLimits", "primary", "usedPercent")
        ),
        "primary_resets_at": _nested_get(
            snapshot, ("rate_limits", "rateLimits", "primary", "resetsAt")
        ),
        "weekly_used_percent": _nested_get(
            snapshot, ("rate_limits", "rateLimits", "secondary", "usedPercent")
        ),
        "weekly_resets_at": _nested_get(
            snapshot, ("rate_limits", "rateLimits", "secondary", "resetsAt")
        ),
        "credits_balance": _nested_get(
            snapshot, ("rate_limits", "rateLimits", "credits", "balance")
        ),
        "reset_credits_available": _nested_get(
            snapshot, ("rate_limits", "rateLimitResetCredits", "availableCount")
        ),
        "latest_daily_bucket_start": _nested_get(
            snapshot, ("usage", "latest_daily_bucket", "startDate")
        ),
        "latest_daily_bucket_tokens": _nested_get(
            snapshot, ("usage", "latest_daily_bucket", "tokens")
        ),
    }


def _account_metric_delta(
    before: dict[str, Any], after: dict[str, Any], key: str
) -> int | float | None:
    before_value = _metric_number(before.get(key))
    after_value = _metric_number(after.get(key))
    if before_value is None or after_value is None:
        return None
    return after_value - before_value


def _account_lane_summary(result: dict[str, Any]) -> dict[str, Any]:
    snapshots = result.get("account_snapshots")
    if not isinstance(snapshots, dict):
        return {"status": "unavailable"}
    if snapshots.get("enabled") is False:
        return {"status": "skipped"}

    before = _account_snapshot_metrics(snapshots.get("before", {}).get("snapshot"))
    after = _account_snapshot_metrics(snapshots.get("after", {}).get("snapshot"))
    observed = (
        before.get("account_snapshot_status") == "observed"
        and after.get("account_snapshot_status") == "observed"
    )
    delta_keys = (
        "primary_used_percent",
        "weekly_used_percent",
        "credits_balance",
        "reset_credits_available",
        "latest_daily_bucket_tokens",
    )
    return {
        "status": "observed" if observed else "unavailable",
        "before": before,
        "after": after,
        "delta": {
            key: _account_metric_delta(before, after, key) for key in delta_keys
        },
    }


def _compare_account_snapshots(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_lane = {result["lane"]: _account_lane_summary(result) for result in results}
    direct = by_lane.get("direct")
    proxy = by_lane.get("proxy")
    if direct is None or proxy is None:
        return {
            "status": "unavailable",
            "usefulness": "unavailable",
            "reason": "missing_lane",
            "lanes": by_lane,
        }
    if direct["status"] != "observed" or proxy["status"] != "observed":
        return {
            "status": "unavailable",
            "usefulness": "unavailable",
            "reason": "snapshot_unavailable",
            "lanes": by_lane,
        }

    worse_reasons: list[str] = []
    compared = False
    for key in (
        "primary_used_percent",
        "weekly_used_percent",
        "latest_daily_bucket_tokens",
    ):
        direct_delta = direct["delta"].get(key)
        proxy_delta = proxy["delta"].get(key)
        if direct_delta is None or proxy_delta is None:
            continue
        compared = True
        if proxy_delta > direct_delta:
            worse_reasons.append(f"proxy_{key}_depleted_more")

    for key in ("credits_balance", "reset_credits_available"):
        direct_delta = direct["delta"].get(key)
        proxy_delta = proxy["delta"].get(key)
        if direct_delta is None or proxy_delta is None:
            continue
        compared = True
        if proxy_delta < direct_delta:
            worse_reasons.append(f"proxy_{key}_depleted_more")

    nonzero = any(
        value not in {None, 0}
        for lane in (direct, proxy)
        for value in lane["delta"].values()
    )
    if not compared or not nonzero:
        return {
            "status": "observed",
            "usefulness": "unavailable",
            "reason": "snapshot_deltas_too_coarse",
            "lanes": by_lane,
        }
    return {
        "status": "observed",
        "usefulness": "fail" if worse_reasons else "pass",
        "reason": "proxy_depleted_more" if worse_reasons else "proxy_not_worse",
        "fail_reasons": worse_reasons,
        "lanes": by_lane,
    }


def _task_prompt(
    marker: str,
    task_lines: int,
    *,
    turn_index: int = 1,
    session_turns: int = 1,
) -> str:
    if session_turns == 1:
        line_marker = marker
        user_message_context = ""
        reply_marker = marker
    else:
        line_marker = f"{marker}-turn-{turn_index:02d}"
        topics = (
            "current repo state and uncommitted changes",
            "focused dashboard/report equivalence for the local analytics window",
            "Codex CLI JSON/account surfaces and command shape",
            "MITM request traceability for direct versus wrapper behavior",
            "LiteLLM provider rows and cache diagnostics",
            "callback cache_hot_zone continuation fields",
            "account snapshot quota, credits, and usage interpretation",
            "docs consistency around usefulness claims",
            "focused tests and validation commands",
            "root-cause notes for session/cache parity",
            "operator-facing wrapper contract checks",
            "final usefulness classification and remaining gaps",
        )
        topic = topics[(turn_index - 1) % len(topics)]
        user_message_context = (
            f"This is user message {turn_index} of {session_turns} in a resumed "
            f"Codex proof session. Focus on {topic}. "
        )
        reply_marker = line_marker
    line_template = (
        f"{line_marker} 2026-06-23T21:"
        + "{i%60:02d}"
        + ":00 ERROR component=litellm route=/v1/responses "
        + f"turn={turn_index:02d} "
        + "request="
        + "{i:03d}"
        + " payload payload payload payload"
    )
    shell_command = (
        f"python3 -c 'for i in range({task_lines}): " + f'print(f"{line_template}")\''
    )
    return (
        f"Do not edit files. {user_message_context}"
        "For runtime evidence, first use the shell tool to "
        f"run exactly this read-only command: {shell_command}. After the "
        "command finishes, reply with exactly this marker and nothing else: "
        f"{reply_marker}"
    )


def _prompts_from_args(args: argparse.Namespace) -> tuple[list[str], dict[str, Any]]:
    prompt_file = getattr(args, "prompt_file", None)
    if prompt_file:
        prompt = getattr(args, "prompt_text", "")
        delimiter = "\n---TURN---\n"
        if args.session_turns > 1 and delimiter in prompt:
            prompts = [part.strip() for part in prompt.split(delimiter)]
            prompts = [part + "\n" for part in prompts if part]
            if len(prompts) != args.session_turns:
                raise ValueError(
                    "--prompt-file turn delimiter count must match --session-turns"
                )
        else:
            prompts = [prompt for _ in range(args.session_turns)]
        prompt_source = {
            "type": "file",
            "path": str(prompt_file),
            "bytes": len(prompt.encode("utf-8")),
        }
        if args.session_turns > 1:
            prompt_source["turns"] = args.session_turns
            prompt_source["delimiter"] = delimiter if delimiter in prompt else None
        return prompts, prompt_source
    prompts = [
        _task_prompt(
            args.marker,
            args.task_lines,
            turn_index=turn_index,
            session_turns=args.session_turns,
        )
        for turn_index in range(1, args.session_turns + 1)
    ]
    prompt_source = {
        "type": "generated_shell_output_task",
        "lines": args.task_lines,
    }
    if args.session_turns > 1:
        prompt_source["turns"] = args.session_turns
        prompt_source["mode"] = "resumed_codex_exec_session"
    return prompts, prompt_source


def _prompt_from_args(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    prompts, prompt_source = _prompts_from_args(args)
    return prompts[0], prompt_source


def _lane_command(
    executable: str,
    workdir: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    model_verbosity: str,
    model_provider: str | None = None,
    resume_session_id: str | None = None,
    yolo: bool = False,
) -> list[str]:
    command = [
        executable,
        "-m",
        model,
    ]
    if model_provider:
        command.extend(["-c", f'model_provider="{model_provider}"'])
    command.extend(
        [
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            f'model_verbosity="{model_verbosity}"',
        ]
    )
    if yolo:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(["-a", "never", "-s", "read-only"])
    command.extend(["-C", str(workdir), "exec"])
    if resume_session_id is not None:
        command.extend(["resume", "--json", resume_session_id, prompt])
    else:
        command.extend(["--json", prompt])
    return command


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
  cr.request_metadata->>'litellm_proxy_responses_provider_passthrough'
    as litellm_proxy_responses_provider_passthrough,
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
  string_agg(
    distinct litellm_proxy_responses_provider_passthrough,
    ', ' order by litellm_proxy_responses_provider_passthrough
  ) filter (
    where litellm_proxy_responses_provider_passthrough is not null
  ) as responses_provider_passthrough_modes,
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
  cr.request_metadata->>'litellm_proxy_responses_provider_passthrough'
    as litellm_proxy_responses_provider_passthrough,
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
  string_agg(
    distinct litellm_proxy_responses_provider_passthrough,
    ', ' order by litellm_proxy_responses_provider_passthrough
  ) filter (
    where litellm_proxy_responses_provider_passthrough is not null
  ) as responses_provider_passthrough_modes,
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
        "turns_dir": str(lane_dir / "turns"),
        "command": str(lane_dir / "command.json"),
        "environment": str(lane_dir / "environment.json"),
        "account_before": str(lane_dir / "account-before.json"),
        "account_before_stderr": str(lane_dir / "account-before.stderr.txt"),
        "account_before_result": str(lane_dir / "account-before-result.json"),
        "account_after": str(lane_dir / "account-after.json"),
        "account_after_stderr": str(lane_dir / "account-after.stderr.txt"),
        "account_after_result": str(lane_dir / "account-after-result.json"),
        "stdout": str(lane_dir / "stdout.txt"),
        "stderr": str(lane_dir / "stderr.txt"),
        "summary_lines": str(lane_dir / "summary-lines.txt"),
        "token_summary": str(lane_dir / "token-summary.json"),
        "trajectory_summary": str(lane_dir / "trajectory-summary.json"),
        "result": str(lane_dir / "result.json"),
    }


def _turn_artifacts(lane_dir: Path, turn_index: int) -> dict[str, Path]:
    turn_dir = lane_dir / "turns" / f"{turn_index:02d}"
    return {
        "dir": turn_dir,
        "command": turn_dir / "command.json",
        "stdout": turn_dir / "stdout.txt",
        "stderr": turn_dir / "stderr.txt",
        "summary_lines": turn_dir / "summary-lines.txt",
        "token_summary": turn_dir / "token-summary.json",
        "trajectory_summary": turn_dir / "trajectory-summary.json",
        "result": turn_dir / "result.json",
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
    prompts, prompt_source = _prompts_from_args(args)
    direct_commands = [
        _lane_command(
            args.codex_bin,
            workdir,
            prompt,
            args.model,
            args.reasoning_effort,
            args.model_verbosity,
            model_provider=args.direct_model_provider,
            resume_session_id=SESSION_ID_PLACEHOLDER if index > 1 else None,
            yolo=bool(args.yolo),
        )
        for index, prompt in enumerate(prompts, start=1)
    ]
    proxy_commands = [
        _lane_command(
            str(Path(args.proxy_bin)),
            workdir,
            prompt,
            args.model,
            args.reasoning_effort,
            args.model_verbosity,
            resume_session_id=SESSION_ID_PLACEHOLDER if index > 1 else None,
            yolo=bool(args.yolo),
        )
        for index, prompt in enumerate(prompts, start=1)
    ]
    db_artifacts = _db_artifacts(artifact_dir)

    return {
        "mode": "execute" if args.execute else "dry-run",
        "marker": args.marker,
        "created_at": _utc_now(),
        "workdir": str(workdir),
        "artifact_dir": str(artifact_dir),
        "lane_order": args.lane_order,
        "task": {
            "lines": args.task_lines,
            "session_turns": args.session_turns,
            "min_combined_input_tokens": args.min_combined_input_tokens,
            "yolo": bool(args.yolo),
            "model": args.model,
            "direct_model_provider": args.direct_model_provider,
            "reasoning_effort": args.reasoning_effort,
            "model_verbosity": args.model_verbosity,
            "expected_savings_profile": args.savings_profile,
            "proxy_responses_provider_passthrough": (
                args.proxy_responses_provider_passthrough
            ),
            "prompt_source": prompt_source,
            "prompt": prompts[0],
            "prompts": prompts,
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
        "account_snapshots": {
            "enabled": not args.skip_account_snapshots,
            "codex_bin": args.account_snapshot_codex_bin,
            "script": str(ACCOUNT_SNAPSHOT_SCRIPT),
            "timeout_seconds": args.account_snapshot_timeout,
            "settle_seconds": args.account_snapshot_settle_seconds,
            "attempts": args.account_snapshot_attempts,
            "retry_delay_seconds": args.account_snapshot_retry_delay_seconds,
        },
        "lanes": {
            "direct": {
                "purpose": "host Codex without the repo LiteLLM wrapper",
                "command": direct_commands[0],
                "commands": direct_commands,
                "session": {
                    "turns": len(direct_commands),
                    "resume_session_id_source": "thread.started",
                    "resume_session_id_placeholder": SESSION_ID_PLACEHOLDER,
                },
                "artifacts": _lane_artifacts(artifact_dir, "direct"),
            },
            "proxy": {
                "purpose": "Codex through ./bin/codex-litellm and LiteLLM /v1/responses",
                "command": proxy_commands[0],
                "commands": proxy_commands,
                "session": {
                    "turns": len(proxy_commands),
                    "resume_session_id_source": "thread.started",
                    "resume_session_id_placeholder": SESSION_ID_PLACEHOLDER,
                },
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
                    **(
                        {
                            CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH_ENV: (
                                args.proxy_responses_provider_passthrough
                            )
                        }
                        if args.proxy_responses_provider_passthrough
                        else {}
                    ),
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
            "Account snapshots must use codex app-server without reading auth token files unless explicitly skipped.",
            "Both lanes use the same execution mode: read-only/approval-never by default, or dangerous yolo-equivalent bypass only when --yolo is set.",
            "Both lanes pin the same Codex model with -m/--model.",
            "Direct lane pins first-party Codex model_provider to avoid native config contamination.",
            "Both lanes pin the same Codex reasoning effort with -c model_reasoning_effort.",
            "Both lanes pin the same Codex model verbosity with -c model_verbosity.",
            "Proxy lane must use the generated litellm Codex profile.",
            "Proxy lane LiteLLM base URL must match the preflighted --litellm-url.",
            "Proxy lane analytics MCP URL must match the configured --analytics-url.",
            "Proxy DB rows must report the expected Headroom strategy profile.",
            "Abort instead of overwriting an existing artifact directory unless --force is set.",
            "Treat smoke/demo rows as test data; usefulness requires provider-reported direct-vs-proxy evidence.",
            "Do not claim MVP usefulness from proxy DB rows alone.",
            "When --min-combined-input-tokens is set, the direct plus proxy input-token aggregate must meet that floor before the run is a practical proof.",
            "Use MITM artifacts for observed request-shape parity; do not use MITM alone as quota proof.",
        ],
        "mitm_trace": {
            "purpose": (
                "Trace observed transport, headers, bodies, model/reasoning fields, "
                "cache keys, continuation fields, and wrapper routing that could "
                "penalize quota burn."
            ),
            "primary_quota_metric": "codex_account_snapshots",
            "trace_is_not_quota_proof": True,
            "direct_default_gap": (
                "Default direct Codex may use Responses WebSocket; plain HTTPS_PROXY "
                "MITM may not fully observe that path."
            ),
            "commands": {
                "direct_http_diagnostic": [
                    sys.executable,
                    "scripts/mitm_codex_capture.py",
                    "--marker",
                    f"{args.marker}-mitm-direct-http",
                    "--lane",
                    "direct",
                    "--model",
                    args.model,
                    "--reasoning-effort",
                    args.reasoning_effort,
                    "--model-verbosity",
                    args.model_verbosity,
                    "--disable-websockets-for-capture",
                    "--execute",
                ],
                "proxy_full_fidelity": [
                    sys.executable,
                    "scripts/mitm_codex_capture.py",
                    "--marker",
                    f"{args.marker}-mitm-proxy",
                    "--lane",
                    "proxy",
                    "--model",
                    args.model,
                    "--reasoning-effort",
                    args.reasoning_effort,
                    "--model-verbosity",
                    args.model_verbosity,
                    "--no-bypass-localhost",
                    "--execute",
                ],
            },
        },
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


def _run_account_snapshot(
    *,
    config: dict[str, Any],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    if not config["enabled"]:
        result = {"enabled": False, "status": "skipped"}
        _write_json(result_path, result)
        return result

    command = [
        sys.executable,
        str(config["script"]),
        "--codex-bin",
        str(config["codex_bin"]),
        "--timeout-seconds",
        str(config["timeout_seconds"]),
    ]
    attempts: list[dict[str, Any]] = []
    attempt_limit = max(int(config["attempts"]), 1)
    retry_delay_seconds = max(float(config["retry_delay_seconds"]), 0.0)
    started_at = _utc_now()
    ended_at = started_at
    returncode = 1
    stdout = ""
    stderr = ""
    snapshot: dict[str, Any] | None = None

    for attempt_number in range(1, attempt_limit + 1):
        attempt_started_at = _utc_now()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=float(config["timeout_seconds"]) + 5,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            returncode = 1
            stdout = ""
            stderr = str(exc)
        attempt_ended_at = _utc_now()
        ended_at = attempt_ended_at

        try:
            parsed = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            parsed = None
        snapshot = parsed if isinstance(parsed, dict) else None
        snapshot_status = (
            snapshot.get("account_snapshot_status")
            if isinstance(snapshot, dict)
            else "unavailable"
        )
        attempts.append(
            {
                "attempt": attempt_number,
                "started_at": attempt_started_at,
                "ended_at": attempt_ended_at,
                "returncode": returncode,
                "account_snapshot_status": snapshot_status,
                "missing_response_ids": snapshot.get("missing_response_ids")
                if isinstance(snapshot, dict)
                else None,
            }
        )
        if snapshot_status == "observed":
            break
        if attempt_number < attempt_limit and retry_delay_seconds > 0:
            time.sleep(retry_delay_seconds)

    _write_text(stdout_path, stdout)
    _write_text(stderr_path, stderr)
    result = {
        "enabled": True,
        "started_at": started_at,
        "ended_at": ended_at,
        "returncode": returncode,
        "command": command,
        "attempts": attempts,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "snapshot": snapshot,
    }
    _write_json(result_path, result)
    return result


def _command_with_session_id(command: list[str], session_id: str | None) -> list[str]:
    if SESSION_ID_PLACEHOLDER not in command:
        return command
    if not session_id:
        raise RuntimeError("cannot resume Codex turn before thread.started is observed")
    return [session_id if arg == SESSION_ID_PLACEHOLDER else arg for arg in command]


def _latest_thread_id(trajectory_summary: dict[str, Any]) -> str | None:
    thread_ids = trajectory_summary.get("thread_ids")
    if not isinstance(thread_ids, list) or not thread_ids:
        return None
    latest = thread_ids[-1]
    return latest if isinstance(latest, str) and latest else None


def _run_lane(
    name: str,
    lane: dict[str, Any],
    cwd: Path,
    timeout: int,
    account_snapshot_config: dict[str, Any],
) -> dict[str, Any]:
    artifacts = {key: Path(value) for key, value in lane["artifacts"].items()}
    artifacts["dir"].mkdir(parents=True, exist_ok=True)
    _write_json(artifacts["command"], lane["command"])
    _write_json(artifacts["dir"] / "commands.json", lane.get("commands") or [])
    lane_environment = dict(lane.get("environment") or {})
    _write_json(artifacts["environment"], lane_environment)

    env = os.environ.copy()
    env.update(lane_environment)
    account_before = _run_account_snapshot(
        config=account_snapshot_config,
        cwd=cwd,
        env=env,
        stdout_path=artifacts["account_before"],
        stderr_path=artifacts["account_before_stderr"],
        result_path=artifacts["account_before_result"],
    )
    started_at = _utc_now()
    turn_results: list[dict[str, Any]] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    turn_token_summaries: list[dict[str, Any]] = []
    session_id: str | None = None
    returncode = 0
    commands = lane.get("commands") or [lane["command"]]

    for turn_index, command_template in enumerate(commands, start=1):
        turn_artifacts = _turn_artifacts(artifacts["dir"], turn_index)
        turn_artifacts["dir"].mkdir(parents=True, exist_ok=True)
        command = _command_with_session_id(list(command_template), session_id)
        _write_json(turn_artifacts["command"], command)
        turn_started_at = _utc_now()
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        turn_ended_at = _utc_now()
        _write_text(turn_artifacts["stdout"], completed.stdout)
        _write_text(turn_artifacts["stderr"], completed.stderr)
        stdout_parts.append(completed.stdout)
        stderr_parts.append(completed.stderr)
        turn_token_summary = parse_token_summary(
            {"stdout": completed.stdout, "stderr": completed.stderr}
        )
        turn_trajectory_summary = parse_codex_trajectory(
            {"stdout": completed.stdout, "stderr": completed.stderr}
        )
        _write_text(
            turn_artifacts["summary_lines"],
            _token_summary_lines(turn_token_summary),
        )
        _write_json(turn_artifacts["token_summary"], turn_token_summary)
        _write_json(turn_artifacts["trajectory_summary"], turn_trajectory_summary)
        if session_id is None:
            session_id = _latest_thread_id(turn_trajectory_summary)
        turn_result = {
            "turn_index": turn_index,
            "started_at": turn_started_at,
            "ended_at": turn_ended_at,
            "returncode": completed.returncode,
            "command": str(turn_artifacts["command"]),
            "stdout": str(turn_artifacts["stdout"]),
            "stderr": str(turn_artifacts["stderr"]),
            "summary_lines": str(turn_artifacts["summary_lines"]),
            "token_summary_file": str(turn_artifacts["token_summary"]),
            "token_summary": turn_token_summary,
            "trajectory_summary_file": str(turn_artifacts["trajectory_summary"]),
            "trajectory_summary": turn_trajectory_summary,
            "session_id_after_turn": session_id,
        }
        _write_json(turn_artifacts["result"], turn_result)
        turn_results.append(turn_result)
        turn_token_summaries.append(turn_token_summary)
        returncode = completed.returncode
        if completed.returncode != 0:
            break

    ended_at = _utc_now()
    settle_seconds = float(account_snapshot_config["settle_seconds"])
    if account_snapshot_config["enabled"] and settle_seconds > 0:
        time.sleep(settle_seconds)
    account_after = _run_account_snapshot(
        config=account_snapshot_config,
        cwd=cwd,
        env=env,
        stdout_path=artifacts["account_after"],
        stderr_path=artifacts["account_after_stderr"],
        result_path=artifacts["account_after_result"],
    )

    combined_stdout = "".join(stdout_parts)
    combined_stderr = "".join(stderr_parts)
    _write_text(artifacts["stdout"], combined_stdout)
    _write_text(artifacts["stderr"], combined_stderr)
    token_summary = aggregate_turn_token_summaries(
        turn_token_summaries,
        cumulative=len(commands) > 1,
    )
    trajectory_summary = parse_codex_trajectory(
        {"stdout": combined_stdout, "stderr": combined_stderr}
    )
    _write_text(artifacts["summary_lines"], _token_summary_lines(token_summary))
    _write_json(artifacts["token_summary"], token_summary)
    _write_json(artifacts["trajectory_summary"], trajectory_summary)
    result = {
        "lane": name,
        "started_at": started_at,
        "ended_at": ended_at,
        "returncode": returncode,
        "session_id": session_id,
        "turn_count": len(turn_results),
        "turn_results": turn_results,
        "stdout": str(artifacts["stdout"]),
        "stderr": str(artifacts["stderr"]),
        "environment": str(artifacts["environment"]),
        "account_snapshots": {
            "enabled": bool(account_snapshot_config["enabled"]),
            "settle_seconds": settle_seconds,
            "before": account_before,
            "after": account_after,
        },
        "summary_lines": str(artifacts["summary_lines"]),
        "token_summary_file": str(artifacts["token_summary"]),
        "token_summary": token_summary,
        "trajectory_summary_file": str(artifacts["trajectory_summary"]),
        "trajectory_summary": trajectory_summary,
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
                    "lane_order": plan.get("lane_order", ["direct", "proxy"]),
                    "results": [],
                    "account_comparison": _compare_account_snapshots([]),
                    "token_comparison": _compare_token_summaries([]),
                    "trajectory_comparison": _compare_trajectories([]),
                    "minimum_input_token_floor": _minimum_input_token_floor(
                        _compare_token_summaries([]),
                        minimum_combined_input_tokens=int(
                            plan["task"]["min_combined_input_tokens"]
                        ),
                    ),
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
    for lane_name in plan.get("lane_order", ["direct", "proxy"]):
        lane = plan["lanes"][lane_name]
        result = _run_lane(
            lane_name,
            lane,
            cwd,
            timeout,
            plan["account_snapshots"],
        )
        results.append(result)
        if result["returncode"] != 0:
            _write_json(
                artifact_dir / "summary.json",
                {
                    "marker": plan["marker"],
                    "artifact_dir": str(artifact_dir),
                    "preflight_result": preflight_result,
                    "lane_order": plan.get("lane_order", ["direct", "proxy"]),
                    "results": results,
                    "account_comparison": _compare_account_snapshots(results),
                    "token_comparison": _compare_token_summaries(results),
                    "trajectory_comparison": _compare_trajectories(results),
                    "minimum_input_token_floor": _minimum_input_token_floor(
                        _compare_token_summaries(results),
                        minimum_combined_input_tokens=int(
                            plan["task"]["min_combined_input_tokens"]
                        ),
                    ),
                },
            )
            print(
                "agent90_usefulness=failed "
                f"lane={lane_name} returncode={result['returncode']} "
                f"artifact_dir={artifact_dir}",
                file=sys.stderr,
            )
            return result["returncode"]

    proxy_result = next(
        (result for result in results if result.get("lane") == "proxy"),
        None,
    )
    if proxy_result is None:
        raise RuntimeError("proxy lane result missing; cannot build proxy DB query")
    proxy_started_at = proxy_result["started_at"]
    proxy_ended_at = proxy_result["ended_at"]
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
    account_comparison = _compare_account_snapshots(results)
    token_comparison = _compare_token_summaries(results)
    minimum_input_token_floor = _minimum_input_token_floor(
        token_comparison,
        minimum_combined_input_tokens=int(plan["task"]["min_combined_input_tokens"]),
    )
    overall_usefulness = _overall_usefulness(
        account_comparison=account_comparison,
        token_comparison=token_comparison,
        minimum_input_token_floor=minimum_input_token_floor,
    )
    summary = {
        "marker": plan["marker"],
        "artifact_dir": str(artifact_dir),
        "preflight_result": preflight_result,
        "lane_order": plan.get("lane_order", ["direct", "proxy"]),
        "results": results,
        "account_comparison": account_comparison,
        "token_comparison": token_comparison,
        "minimum_input_token_floor": minimum_input_token_floor,
        "overall_usefulness": overall_usefulness,
        "trajectory_comparison": _compare_trajectories(results),
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
    if overall_usefulness["status"] == "fail":
        print(
            "agent90_usefulness=failed "
            f"scope={overall_usefulness['scope']} "
            f"reason={overall_usefulness['reason']} "
            f"reasons={','.join(overall_usefulness['fail_reasons'])} "
            f"artifact_dir={artifact_dir}",
            file=sys.stderr,
        )
        return 2
    if overall_usefulness["status"] != "pass":
        reasons = ",".join(overall_usefulness["missing_reasons"])
        print(
            "agent90_usefulness=failed "
            f"overall_usefulness={overall_usefulness['status']} "
            f"reasons={reasons} artifact_dir={artifact_dir}",
            file=sys.stderr,
        )
        return 2
    print(
        "agent90_usefulness=ok "
        f"scope={overall_usefulness['scope']} "
        f"account={overall_usefulness['account_usefulness']} "
        f"provider_diagnostic={overall_usefulness['provider_diagnostic_status']} "
        f"cost={overall_usefulness['cost_status']} "
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
        "--direct-model-provider",
        type=_validate_model,
        default=DEFAULT_DIRECT_MODEL_PROVIDER,
        help=(
            "Codex model_provider override for the direct lane. Defaults to "
            "openai so native config providers cannot contaminate the baseline."
        ),
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
    parser.add_argument(
        "--proxy-responses-provider-passthrough",
        choices=("on", "off"),
        help=(
            "Proxy-lane request-scoped experiment switch for preserving "
            "Responses provider passthrough fields through LiteLLM."
        ),
    )
    parser.add_argument("--task-lines", type=int, default=220)
    parser.add_argument(
        "--session-turns",
        type=int,
        default=DEFAULT_SESSION_TURNS,
        help=(
            "Number of user-message turns per lane. Turn 1 uses codex exec; "
            "later turns use codex exec resume against the exact thread id."
        ),
    )
    parser.add_argument(
        "--min-combined-input-tokens",
        type=int,
        default=DEFAULT_MIN_COMBINED_INPUT_TOKENS,
        help=(
            "Require direct plus proxy aggregate input tokens to reach this "
            "floor before treating the run as a practical proof."
        ),
    )
    parser.add_argument(
        "--lane-order",
        default=DEFAULT_LANE_ORDER,
        help=(
            "Comma-separated lane execution order. Use direct,proxy by default "
            "or proxy,direct to audit coarse account-quota threshold bias."
        ),
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help=(
            "Read the exact Codex task prompt from a UTF-8 file instead of "
            "using the generated shell-output task."
        ),
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--db-timeout", type=int, default=60)
    parser.add_argument("--db-window-grace-seconds", type=int, default=300)
    parser.add_argument("--preflight-timeout", type=float, default=2.0)
    parser.add_argument(
        "--account-snapshot-codex-bin",
        default="codex",
        help="Codex binary used for account quota snapshots.",
    )
    parser.add_argument(
        "--account-snapshot-timeout",
        type=float,
        default=DEFAULT_ACCOUNT_SNAPSHOT_TIMEOUT,
    )
    parser.add_argument(
        "--account-snapshot-settle-seconds",
        type=float,
        default=0.0,
        help=(
            "Seconds to wait after each lane before the after-account snapshot. "
            "Use a positive value for quota surfaces that update with delay."
        ),
    )
    parser.add_argument(
        "--account-snapshot-attempts",
        type=int,
        default=DEFAULT_ACCOUNT_SNAPSHOT_ATTEMPTS,
        help="Number of attempts for each account snapshot when the probe is unavailable.",
    )
    parser.add_argument(
        "--account-snapshot-retry-delay-seconds",
        type=float,
        default=DEFAULT_ACCOUNT_SNAPSHOT_RETRY_DELAY_SECONDS,
        help="Seconds to wait between account snapshot attempts.",
    )
    parser.add_argument(
        "--skip-account-snapshots",
        action="store_true",
        help="Do not bracket lanes with codex app-server account snapshots.",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help=(
            "Use Codex's dangerous bypass mode for both lanes instead of "
            "approval-never/read-only mode, matching yolo-style validation."
        ),
    )
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
    if args.session_turns < 1:
        parser.error("--session-turns must be positive")
    if args.min_combined_input_tokens < 0:
        parser.error("--min-combined-input-tokens must be nonnegative")
    lane_order = [value.strip() for value in args.lane_order.split(",") if value.strip()]
    if sorted(lane_order) != ["direct", "proxy"]:
        parser.error("--lane-order must contain direct and proxy exactly once")
    args.lane_order = lane_order
    if args.prompt_file:
        prompt_path = Path(args.prompt_file).expanduser()
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"--prompt-file cannot be read: {exc}")
        if not prompt_text.strip():
            parser.error("--prompt-file must contain nonblank text")
        args.prompt_file = str(prompt_path.resolve(strict=False))
        args.prompt_text = prompt_text
    else:
        args.prompt_text = None
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.db_timeout < 1:
        parser.error("--db-timeout must be positive")
    if args.db_window_grace_seconds < 0:
        parser.error("--db-window-grace-seconds must be nonnegative")
    if args.preflight_timeout <= 0:
        parser.error("--preflight-timeout must be positive")
    if args.account_snapshot_timeout <= 0:
        parser.error("--account-snapshot-timeout must be positive")
    if args.account_snapshot_settle_seconds < 0:
        parser.error("--account-snapshot-settle-seconds must be nonnegative")
    if args.account_snapshot_attempts < 1:
        parser.error("--account-snapshot-attempts must be positive")
    if args.account_snapshot_retry_delay_seconds < 0:
        parser.error("--account-snapshot-retry-delay-seconds must be nonnegative")
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
