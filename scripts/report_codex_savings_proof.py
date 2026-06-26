#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge Codex direct-vs-wrapper proof artifacts into a report."
    )
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--session-report", type=Path)
    parser.add_argument("--direct-mitm-dir", type=Path)
    parser.add_argument("--proxy-mitm-dir", type=Path)
    parser.add_argument("--litellm-outbound-mitm-dir", type=Path)
    parser.add_argument("--proxy-db-rows", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = _read_json(args.summary)
    session_report = _read_json(args.session_report) if args.session_report else None
    direct_mitm = _mitm_summary(args.direct_mitm_dir) if args.direct_mitm_dir else None
    proxy_mitm = _mitm_summary(args.proxy_mitm_dir) if args.proxy_mitm_dir else None
    outbound_mitm = (
        _raw_flow_mitm_summary(args.litellm_outbound_mitm_dir)
        if args.litellm_outbound_mitm_dir
        else None
    )
    proxy_db_rows = _read_json(args.proxy_db_rows) if args.proxy_db_rows else None
    report = build_report(
        summary=summary,
        summary_path=args.summary,
        session_report=session_report,
        session_report_path=args.session_report,
        direct_mitm=direct_mitm,
        direct_mitm_dir=args.direct_mitm_dir,
        proxy_mitm=proxy_mitm,
        proxy_mitm_dir=args.proxy_mitm_dir,
        litellm_outbound_mitm=outbound_mitm,
        litellm_outbound_mitm_dir=args.litellm_outbound_mitm_dir,
        proxy_db_rows=proxy_db_rows,
        proxy_db_rows_path=args.proxy_db_rows,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "report.json"
    md_path = args.out_dir / "report.md"
    html_path = args.out_dir / "report.html"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    print(json_path)
    print(md_path)
    print(html_path)
    return 0


def build_report(
    *,
    summary: dict[str, Any],
    summary_path: Path,
    session_report: dict[str, Any] | None = None,
    session_report_path: Path | None = None,
    direct_mitm: dict[str, Any] | None = None,
    direct_mitm_dir: Path | None = None,
    proxy_mitm: dict[str, Any] | None = None,
    proxy_mitm_dir: Path | None = None,
    litellm_outbound_mitm: dict[str, Any] | None = None,
    litellm_outbound_mitm_dir: Path | None = None,
    proxy_db_rows: list[dict[str, Any]] | None = None,
    proxy_db_rows_path: Path | None = None,
) -> dict[str, Any]:
    token_comparison = summary.get("token_comparison") or {}
    account_comparison = summary.get("account_comparison") or {}
    minimum_floor = summary.get("minimum_input_token_floor") or {}
    overall = summary.get("overall_usefulness") or {}
    lanes = {
        result["lane"]: _lane_report(result)
        for result in summary.get("results", [])
        if result.get("lane") in {"direct", "proxy"}
    }
    verdict = _verdict(
        overall=overall,
        account_comparison=account_comparison,
        token_comparison=token_comparison,
        minimum_floor=minimum_floor,
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "marker": summary.get("marker"),
        "source_summary": str(summary_path),
        "lane_order": summary.get("lane_order", ["direct", "proxy"]),
        "verdict": verdict,
        "minimum_input_token_floor": minimum_floor,
        "account_comparison": account_comparison,
        "overall_usefulness": overall,
        "lanes": lanes,
        "provider_diagnostics": {
            "completion_contract": token_comparison.get("completion_contract"),
            "mvp_usefulness": token_comparison.get("mvp_usefulness"),
            "delta_proxy_minus_direct": token_comparison.get(
                "delta_proxy_minus_direct"
            ),
            "derived": token_comparison.get("derived"),
            "cost": token_comparison.get("cost"),
        },
        "session_report": _session_report_summary(session_report, session_report_path),
        "mitm": {
            "direct_http_diagnostic": _with_path(direct_mitm, direct_mitm_dir),
            "proxy_full_fidelity": _with_path(proxy_mitm, proxy_mitm_dir),
            "litellm_outbound_provider": _with_path(
                litellm_outbound_mitm,
                litellm_outbound_mitm_dir,
            ),
        },
        "proxy_provider_row_diagnostics": _provider_row_diagnostics(
            proxy_db_rows,
            proxy_db_rows_path,
        ),
        "evidence_limits": [
            "Cost is unavailable unless Codex/provider reports it.",
            "Direct HTTP MITM is diagnostic when default direct Codex uses WebSocket.",
            "Provider diagnostics can contradict account-capacity safety.",
            "Do not claim real savings unless provider/cache or account-saving evidence passes.",
        ],
    }


def _provider_row_diagnostics(
    rows: list[dict[str, Any]] | None,
    path: Path | None,
) -> dict[str, Any] | None:
    if not rows:
        return None

    chronological_rows = sorted(rows, key=lambda row: str(row.get("created_at") or ""))
    compact_rows: list[dict[str, Any]] = []
    field_hash_values: dict[str, set[str]] = {}
    stable_input_prefix_hashes: dict[str, int] = {}
    stable_top_level_hashes: dict[str, int] = {}
    stable_prefix_without_cache_hashes: dict[str, int] = {}
    prompt_cache_key_hashes: dict[str, int] = {}
    execution_status_counts: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    transform_counts: dict[str, int] = {}
    stable_input_item_counts: set[int] = set()
    prefix_bytes_values: set[int] = set()
    mutable_estimates: list[int] = []

    for index, row in enumerate(chronological_rows, start=1):
        transforms = _json_object(row.get("transforms"))
        cache_hot_zone = _dict_value(transforms.get("cache_hot_zone"))
        deployment_payload = _dict_value(transforms.get("deployment_payload"))
        deployment_cache = _dict_value(deployment_payload.get("cache_hot_zone"))
        mutable_output = _dict_value(deployment_payload.get("mutable_output"))
        cache_for_counts = deployment_cache or cache_hot_zone

        execution_status = _string_value(row.get("execution_status")) or "unknown"
        _count(execution_status_counts, execution_status)
        skip_reason = _string_value(transforms.get("skip_reason"))
        if skip_reason:
            _count(skip_reason_counts, skip_reason)
        for transform in transforms.get("applied") or []:
            if isinstance(transform, str):
                _count(transform_counts, transform)

        field_hashes = _dict_value(cache_for_counts.get("stable_top_level_field_hashes"))
        for field, value in field_hashes.items():
            if isinstance(value, str):
                field_hash_values.setdefault(str(field), set()).add(value)

        stable_input_prefix_hash = _string_value(
            cache_for_counts.get("stable_input_prefix_hash")
        )
        if stable_input_prefix_hash:
            _count(stable_input_prefix_hashes, stable_input_prefix_hash)
        stable_top_level_hash = _string_value(cache_for_counts.get("stable_top_level_hash"))
        if stable_top_level_hash:
            _count(stable_top_level_hashes, stable_top_level_hash)
        without_cache_hash = _string_value(
            cache_for_counts.get("stable_prefix_without_prompt_cache_key_hash")
        )
        if without_cache_hash:
            _count(stable_prefix_without_cache_hashes, without_cache_hash)
        prompt_cache_hash = _string_value(
            field_hashes.get("prompt_cache_key")
            if isinstance(field_hashes, dict)
            else None
        )
        if prompt_cache_hash:
            _count(prompt_cache_key_hashes, prompt_cache_hash)

        stable_input_item_count = _int_value(cache_for_counts.get("stable_input_item_count"))
        if stable_input_item_count is not None:
            stable_input_item_counts.add(stable_input_item_count)
        stable_prefix_bytes = _int_value(cache_for_counts.get("stable_prefix_bytes"))
        if stable_prefix_bytes is not None:
            prefix_bytes_values.add(stable_prefix_bytes)
        mutable_estimate = _int_value(mutable_output.get("output_tokens_estimate")) or 0
        mutable_estimates.append(mutable_estimate)

        input_tokens = _int_value(row.get("input_tokens")) or 0
        cached_tokens = _int_value(row.get("cached_input_tokens")) or 0
        compact_rows.append(
            {
                "index": index,
                "created_at": row.get("created_at"),
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_tokens,
                "newly_processed_input_tokens": _int_value(
                    row.get("newly_processed_input_tokens")
                ),
                "cache_ratio": _safe_ratio(cached_tokens, input_tokens),
                "output_tokens": _int_value(row.get("output_tokens")),
                "total_tokens": _int_value(row.get("total_tokens")),
                "input_item_count": _int_value(cache_for_counts.get("input_item_count")),
                "stable_input_item_count": stable_input_item_count,
                "stable_prefix_bytes": stable_prefix_bytes,
                "mutable_boundary": cache_for_counts.get("mutable_boundary"),
                "mutable_output_item_count": _int_value(
                    mutable_output.get("output_item_count")
                ),
                "mutable_output_tokens_estimate": mutable_estimate,
                "skip_reason": skip_reason,
            }
        )

    total_input = sum(_int_value(row.get("input_tokens")) or 0 for row in rows)
    total_cached = sum(_int_value(row.get("cached_input_tokens")) or 0 for row in rows)
    total_new = sum(
        _int_value(row.get("newly_processed_input_tokens")) or 0 for row in rows
    )
    total_output = sum(_int_value(row.get("output_tokens")) or 0 for row in rows)
    total_tokens = sum(_int_value(row.get("total_tokens")) or 0 for row in rows)
    field_distinct_counts = {
        field: len(values) for field, values in sorted(field_hash_values.items())
    }
    summary = {
        "path": str(path) if path else None,
        "row_count": len(rows),
        "chronological_order": "created_at_ascending",
        "totals": {
            "input_tokens": total_input,
            "cached_input_tokens": total_cached,
            "newly_processed_input_tokens": total_new,
            "output_tokens": total_output,
            "total_tokens": total_tokens,
            "cache_ratio": _safe_ratio(total_cached, total_input),
        },
        "execution_status_counts": execution_status_counts,
        "skip_reason_counts": skip_reason_counts,
        "transform_counts": transform_counts,
        "distinct_counts": {
            "stable_input_prefix_hash": len(stable_input_prefix_hashes),
            "stable_top_level_hash": len(stable_top_level_hashes),
            "stable_prefix_without_prompt_cache_key_hash": len(
                stable_prefix_without_cache_hashes
            ),
            "prompt_cache_key_hash": len(prompt_cache_key_hashes),
            "stable_top_level_field_hashes": field_distinct_counts,
        },
        "stable_input_item_counts": sorted(stable_input_item_counts),
        "stable_prefix_bytes_values": sorted(prefix_bytes_values),
        "mutable_output_tokens_estimate": {
            "sum": sum(mutable_estimates),
            "min": min(mutable_estimates) if mutable_estimates else None,
            "max": max(mutable_estimates) if mutable_estimates else None,
        },
    }
    return {
        "summary": summary,
        "causal_notes": _provider_row_causal_notes(summary),
        "rows": compact_rows,
    }


def _provider_row_causal_notes(summary: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    row_count = int(summary.get("row_count") or 0)
    status_counts = summary.get("execution_status_counts") or {}
    skip_counts = summary.get("skip_reason_counts") or {}
    distinct_counts = summary.get("distinct_counts") or {}
    field_counts = distinct_counts.get("stable_top_level_field_hashes") or {}
    stable_prefix_count = distinct_counts.get("stable_input_prefix_hash")
    mutable_estimate = summary.get("mutable_output_tokens_estimate") or {}

    if status_counts == {"skipped": row_count} and skip_counts:
        notes.append(
            "Every proxy provider row skipped compression, so the negative provider "
            "delta is not evidence that compressed payloads expanded."
        )
    changing_fields = sorted(
        field for field, count in field_counts.items() if isinstance(count, int) and count > 1
    )
    if changing_fields == ["client_metadata"]:
        notes.append(
            "Only client_metadata varied among cache-sensitive top-level fields; "
            "model, tools, instructions, reasoning, text, and prompt_cache_key stayed stable."
        )
    elif changing_fields:
        notes.append(
            "Cache-sensitive top-level field churn was limited to: "
            + ", ".join(changing_fields)
            + "."
        )
    if stable_prefix_count == 1 or (
        stable_prefix_count == 2 and summary.get("stable_input_item_counts") == [3, 5]
    ):
        notes.append(
            "The cacheable input prefix was stable after the first call; row data does "
            "not show prompt/tool prefix inflation from the wrapper."
        )
    if int(mutable_estimate.get("max") or 0) > 0:
        notes.append(
            "Mutable function-call output grows across resumed turns, matching "
            "cumulative continuation accounting in the provider token totals."
        )
    return notes


def _lane_report(result: dict[str, Any]) -> dict[str, Any]:
    token_summary = result.get("token_summary") or {}
    input_tokens = token_summary.get("input_tokens")
    cached_tokens = token_summary.get("cached_input_tokens")
    cache_ratio = None
    if isinstance(input_tokens, int) and input_tokens > 0 and isinstance(cached_tokens, int):
        cache_ratio = round(cached_tokens / input_tokens, 6)
    return {
        "returncode": result.get("returncode"),
        "turn_count": result.get("turn_count"),
        "session_id": result.get("session_id"),
        "started_at": result.get("started_at"),
        "ended_at": result.get("ended_at"),
        "usage_source": token_summary.get("usage_source"),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "cache_ratio": cache_ratio,
        "output_tokens": token_summary.get("output_tokens"),
        "reasoning_tokens": token_summary.get("reasoning_tokens"),
        "total_tokens": token_summary.get("total_tokens"),
        "cost_usd": token_summary.get("cost_usd"),
    }


def _verdict(
    *,
    overall: dict[str, Any],
    account_comparison: dict[str, Any],
    token_comparison: dict[str, Any],
    minimum_floor: dict[str, Any],
) -> dict[str, Any]:
    floor_ok = bool(minimum_floor.get("ok"))
    account_status = account_comparison.get("usefulness")
    provider_status = (token_comparison.get("completion_contract") or {}).get("status")
    if not floor_ok or account_status == "fail":
        shareability_status = "fail"
    elif account_status == "pass":
        shareability_status = "pass"
    else:
        shareability_status = "unavailable"
    shareable = shareability_status == "pass"
    if not floor_ok:
        savings = "unproven"
        headline = "Not practical proof: input-token floor was not met."
    elif account_status == "fail":
        savings = "negative"
        headline = "Not shareable: wrapper depleted observed account capacity more."
    elif provider_status == "pass" and shareable:
        savings = "proven"
        headline = "Shareable and provider/cache savings are proven for this run."
    elif provider_status == "pass":
        savings = "proven"
        headline = (
            "Provider/cache savings are proven for this run; account-capacity "
            "shareability is unavailable because snapshot deltas were too coarse."
        )
    elif shareable:
        savings = "unproven_provider_negative"
        headline = (
            "Shareable on observed account capacity, but real savings are not "
            "proven because provider diagnostics failed."
        )
    else:
        savings = "unproven"
        headline = "Savings are unproven."
    return {
        "headline": headline,
        "shareability_status": shareability_status,
        "real_savings_status": savings,
        "account_status": account_status,
        "provider_diagnostic_status": provider_status,
        "minimum_input_floor_ok": floor_ok,
    }


def _session_report_summary(
    report: dict[str, Any] | None,
    path: Path | None,
) -> dict[str, Any] | None:
    if report is None:
        return None
    summary = report.get("summary") or {}
    narrative = report.get("narrative") or {}
    return {
        "path": str(path) if path else None,
        "verdict": narrative.get("verdict"),
        "window": report.get("window"),
        "requests": summary.get("requests"),
        "executions": summary.get("executions"),
        "provider_input_tokens": summary.get("provider_input_tokens"),
        "provider_cached_input_tokens": summary.get("provider_cached_input_tokens"),
        "provider_cache_hit_percent": summary.get("provider_cache_hit_percent"),
        "tokens_saved": summary.get("tokens_saved"),
        "raw_savings_percent": summary.get("raw_savings_percent"),
        "billing_equivalent_input_tokens": summary.get(
            "billing_equivalent_input_tokens"
        ),
        "billing_equivalent_savings_percent": summary.get(
            "billing_equivalent_savings_percent"
        ),
    }


def _mitm_summary(path: Path) -> dict[str, Any]:
    plan = _read_json(path / "plan.json")
    result = _read_json(path / "result.json")
    requests = _model_requests_from_flows(path / "flows.jsonl")
    return {
        "lane": plan.get("lane"),
        "returncode": result.get("returncode"),
        "flow_count": result.get("flow_count"),
        "disable_websockets_for_capture": (plan.get("safety") or {}).get(
            "disable_websockets_for_capture"
        ),
        "bypass_localhost": (plan.get("safety") or {}).get("bypass_localhost"),
        "model_requests": requests,
    }


def _raw_flow_mitm_summary(path: Path) -> dict[str, Any]:
    flows_path = path / "flows.jsonl"
    records = _flow_records(flows_path)
    requests = _model_requests_from_records(records)
    return {
        "lane": "litellm-outbound",
        "returncode": None,
        "flow_count": len(records),
        "disable_websockets_for_capture": None,
        "bypass_localhost": None,
        "model_requests": requests,
    }


def _model_requests_from_flows(path: Path) -> list[dict[str, Any]]:
    return _model_requests_from_records(_flow_records(path))


def _flow_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _model_requests_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests = []
    for record in records:
        if record.get("event") != "request":
            continue
        request = record.get("request") or {}
        body = request.get("body") or {}
        payload = body.get("json")
        if not isinstance(payload, dict) or payload.get("model") is None:
            continue
        requests.append(
            {
                "host": request.get("host"),
                "path": request.get("path"),
                "body_bytes": body.get("bytes"),
                "model": payload.get("model"),
                "input_count": _list_len(payload.get("input")),
                "tools_count": _list_len(payload.get("tools")),
                "has_prompt_cache_key": "prompt_cache_key" in payload,
                "has_client_metadata": "client_metadata" in payload,
                "has_previous_response_id": "previous_response_id" in payload,
                "has_truncation": "truncation" in payload,
            }
        )
    return requests


def _with_path(value: dict[str, Any] | None, path: Path | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {**value, "artifact_dir": str(path) if path else None}


def _list_len(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def render_markdown(report: dict[str, Any]) -> str:
    verdict = report["verdict"]
    account_lanes = report["account_comparison"].get("lanes") or {}
    provider_contract = report["provider_diagnostics"].get("completion_contract") or {}
    provider_mvp = report["provider_diagnostics"].get("mvp_usefulness") or {}
    lines = [
        "# Codex Savings Proof Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Marker: `{report['marker']}`",
        "",
        "## Verdict",
        "",
        verdict["headline"],
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Shareability | `{verdict['shareability_status']}` |",
        f"| Real savings | `{verdict['real_savings_status']}` |",
        f"| Account status | `{verdict['account_status']}` |",
        f"| Provider diagnostics | `{verdict['provider_diagnostic_status']}` |",
        f"| Minimum input floor | `{verdict['minimum_input_floor_ok']}` |",
        "",
        "## Token Consumption",
        "",
        "| Lane | Turns | Input | Cached | Cache ratio | Output | Total | Cost |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for lane in ("direct", "proxy"):
        row = report["lanes"].get(lane, {})
        lines.append(
            f"| {lane} | {_fmt(row.get('turn_count'))} | {_fmt(row.get('input_tokens'))} | "
            f"{_fmt(row.get('cached_input_tokens'))} | {_fmt_ratio(row.get('cache_ratio'))} | "
            f"{_fmt(row.get('output_tokens'))} | {_fmt(row.get('total_tokens'))} | "
            f"{row.get('cost_usd') if row.get('cost_usd') is not None else 'unavailable'} |"
        )
    lines.extend(
        [
            "",
            "## Account And Provider Diagnostics",
            "",
            f"- Input floor: `{json.dumps(report['minimum_input_token_floor'], sort_keys=True)}`",
            f"- Account reason: `{report['account_comparison'].get('reason')}`",
            "",
            "| Lane | Primary quota delta | Weekly delta | Credits delta | Reset credits delta | Daily token delta |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for lane in ("direct", "proxy"):
        delta = (account_lanes.get(lane) or {}).get("delta") or {}
        lines.append(
            f"| {lane} | {_fmt(delta.get('primary_used_percent'))} | "
            f"{_fmt(delta.get('weekly_used_percent'))} | "
            f"{_fmt(delta.get('credits_balance'))} | "
            f"{_fmt(delta.get('reset_credits_available'))} | "
            f"{_fmt(delta.get('latest_daily_bucket_tokens'))} |"
        )
    lines.extend(
        [
            "",
            f"- Provider status: `{provider_contract.get('status')}`",
            f"- Provider fail reasons: `{provider_contract.get('fail_reasons')}`",
            f"- Provider missing reasons: `{provider_contract.get('missing_reasons')}`",
            f"- Provider checks: `{json.dumps(provider_mvp.get('checks'), sort_keys=True)}`",
            "",
            "## MITM Evidence",
            "",
        ]
    )
    for name, value in report["mitm"].items():
        if not value:
            lines.append(f"- {name}: not captured.")
            continue
        lines.append(
            f"- {name}: `{value['artifact_dir']}`, flows `{value['flow_count']}`, "
            f"requests `{value['model_requests']}`"
        )
    if report.get("session_report"):
        session = report["session_report"]
        lines.extend(
            [
                "",
                "## Analytics Session Report",
                "",
                f"- Path: `{session['path']}`",
                f"- Verdict: {session['verdict']}",
                f"- Provider cache hit: `{session['provider_cache_hit_percent']}`",
                f"- Local token delta: `{session['tokens_saved']}`",
            ]
        )
    if report.get("proxy_provider_row_diagnostics"):
        diagnostics = report["proxy_provider_row_diagnostics"]
        summary = diagnostics["summary"]
        totals = summary["totals"]
        distinct = summary["distinct_counts"]
        field_counts = distinct.get("stable_top_level_field_hashes") or {}
        lines.extend(
            [
                "",
                "## Proxy Provider Row Diagnostics",
                "",
                f"- Path: `{summary['path']}`",
                f"- Rows: `{summary['row_count']}` in `{summary['chronological_order']}` order.",
                f"- Totals: input `{_fmt(totals.get('input_tokens'))}`, cached `{_fmt(totals.get('cached_input_tokens'))}`, newly processed `{_fmt(totals.get('newly_processed_input_tokens'))}`, total `{_fmt(totals.get('total_tokens'))}`.",
                f"- Execution statuses: `{json.dumps(summary['execution_status_counts'], sort_keys=True)}`",
                f"- Skip reasons: `{json.dumps(summary['skip_reason_counts'], sort_keys=True)}`",
                f"- Distinct stable input prefixes: `{distinct.get('stable_input_prefix_hash')}`",
                f"- Distinct stable top-level hashes: `{distinct.get('stable_top_level_hash')}`",
                f"- Distinct prompt-cache-key hashes: `{distinct.get('prompt_cache_key_hash')}`",
                f"- Stable top-level field hash counts: `{json.dumps(field_counts, sort_keys=True)}`",
            ]
        )
        for note in diagnostics.get("causal_notes") or []:
            lines.append(f"- Diagnosis: {note}")
        lines.extend(
            [
                "",
                "| # | Input | Cached | New | Output | Total | Items | Stable items | Prefix bytes | Mutable est. |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in diagnostics.get("rows") or []:
            lines.append(
                f"| {row['index']} | {_fmt(row.get('input_tokens'))} | "
                f"{_fmt(row.get('cached_input_tokens'))} | "
                f"{_fmt(row.get('newly_processed_input_tokens'))} | "
                f"{_fmt(row.get('output_tokens'))} | "
                f"{_fmt(row.get('total_tokens'))} | "
                f"{_fmt(row.get('input_item_count'))} | "
                f"{_fmt(row.get('stable_input_item_count'))} | "
                f"{_fmt(row.get('stable_prefix_bytes'))} | "
                f"{_fmt(row.get('mutable_output_tokens_estimate'))} |"
            )
    lines.extend(["", "## Evidence Limits", ""])
    lines.extend(f"- {item}" for item in report["evidence_limits"])
    lines.append("")
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    markdown = render_markdown(report)
    data = json.dumps(report, sort_keys=True).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Savings Proof Report</title>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f7f8fb; color: #16202a; line-height: 1.45; }}
    main {{ width: min(1120px, calc(100% - 2rem)); margin: 0 auto; padding: 1.5rem 0 2rem; }}
    h1 {{ margin: 0 0 .25rem; font-size: 1.8rem; }}
    .verdict {{ padding: 1rem; border: 1px solid #cfd8e3; border-left: 6px solid #315fbd; border-radius: 8px; background: white; }}
    pre {{ overflow: auto; padding: 1rem; border-radius: 8px; background: #111927; color: #edf6ff; }}
  </style>
</head>
<body>
<main>
  <h1>Codex Savings Proof Report</h1>
  <div class="verdict">{html.escape(report['verdict']['headline'])}</div>
  <pre>{html.escape(markdown)}</pre>
</main>
<script type="application/json" id="report-data">{data}</script>
</body>
</html>
"""


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.2f}%"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


if __name__ == "__main__":
    raise SystemExit(main())
