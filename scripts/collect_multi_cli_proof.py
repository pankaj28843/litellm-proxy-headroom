from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)
SUPPORT_STATUSES = {
    "supported_useful",
    "route_supported_cache_unproven",
    "route_gated",
    "isolation_only",
    "unsupported",
}
DB_CORRELATION_VALUES = {"marker", "time_window", "not_applicable"}
COST_STATUS_VALUES = {"observed", "unavailable", "not_applicable"}
COMPRESSION_MODE_VALUES = {"on", "off", "mixed", "unknown", "not_applicable"}


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _decimal_to_json(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def aggregate_provider_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    provider_rows = [
        row for row in rows if row.get("measurement_source") == "provider_reported"
    ]
    request_keys = {str(row["request_key"]) for row in rows if row.get("request_key")}
    provider_call_keys = {
        str(row["provider_call_id"])
        for row in provider_rows
        if row.get("provider_call_id")
    }
    models = sorted({str(row["model"]) for row in rows if row.get("model")})
    clients = sorted({str(row["client"]) for row in rows if row.get("client")})
    routes = sorted(
        {str(row["incoming_route"]) for row in rows if row.get("incoming_route")}
    )

    totals: dict[str, int] = {}
    present_fields: set[str] = set()
    for field in TOKEN_FIELDS:
        total = 0
        for row in provider_rows:
            value = _optional_int(row.get(field))
            if value is None:
                continue
            present_fields.add(field)
            total += value
        if field in present_fields:
            totals[field] = total

    cached_input = totals.get("cached_input_tokens")
    input_tokens = totals.get("input_tokens")
    if cached_input is None:
        cached_input_value: int | str = "absent"
        cache_ratio: str = "unavailable"
    else:
        cached_input_value = cached_input
        cache_ratio = (
            format(Decimal(cached_input) / Decimal(input_tokens), ".6f")
            if input_tokens
            else "unavailable"
        )

    cost_values = [
        parsed
        for row in provider_rows
        if (parsed := _optional_decimal(row.get("cost_total"))) is not None
    ]
    cost_total = sum(cost_values, Decimal("0")) if cost_values else None
    if cost_values:
        cost_status = "observed"
    elif provider_rows:
        cost_status = "unavailable"
    else:
        cost_status = "not_applicable"

    return {
        "request_count": len(request_keys) if request_keys else len(rows),
        "provider_reported_call_count": (
            len(provider_call_keys) if provider_call_keys else len(provider_rows)
        ),
        "models": models,
        "clients": clients,
        "incoming_routes": routes,
        "input_tokens": totals.get("input_tokens"),
        "cached_input_tokens": cached_input_value,
        "cache_ratio": cache_ratio,
        "output_tokens": totals.get("output_tokens"),
        "reasoning_tokens": totals.get("reasoning_tokens"),
        "total_tokens": totals.get("total_tokens"),
        "cost_status": cost_status,
        "cost_total": _decimal_to_json(cost_total),
    }


def build_proof_record(
    *,
    cli: str,
    wrapper: str,
    managed_home: str,
    support_status: str,
    marker: str,
    compression_mode: str,
    model_scope: list[str],
    artifact_dir: str,
    db_correlation: str,
    rows: list[dict[str, Any]],
    notes: list[str],
    cost_status_override: str | None = None,
) -> dict[str, Any]:
    if support_status not in SUPPORT_STATUSES:
        raise ValueError(f"unsupported support_status: {support_status}")
    if db_correlation not in DB_CORRELATION_VALUES:
        raise ValueError(f"unsupported db_correlation: {db_correlation}")
    if compression_mode not in COMPRESSION_MODE_VALUES:
        raise ValueError(f"unsupported compression_mode: {compression_mode}")
    if (
        cost_status_override is not None
        and cost_status_override not in COST_STATUS_VALUES
    ):
        raise ValueError(f"unsupported cost_status_override: {cost_status_override}")

    aggregate = aggregate_provider_rows(rows)
    if cost_status_override is not None:
        aggregate["cost_status"] = cost_status_override
        if cost_status_override != "observed":
            aggregate["cost_total"] = None
    return {
        "cli": cli,
        "wrapper": wrapper,
        "managed_home": managed_home,
        "support_status": support_status,
        "marker": marker,
        "compression_mode": compression_mode,
        "model_scope": model_scope,
        "artifact_dir": artifact_dir,
        "db_correlation": db_correlation,
        "client_attribution": aggregate["clients"],
        "aggregate": aggregate,
        "notes": notes,
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError("--db-rows-json must contain a JSON array of objects")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a normalized multi-CLI proof record from already captured "
            "real CLI artifacts and exported LiteLLM DB rows. This does not run "
            "agent CLIs."
        )
    )
    parser.add_argument("--cli", required=True)
    parser.add_argument("--wrapper", required=True)
    parser.add_argument("--managed-home", required=True)
    parser.add_argument(
        "--support-status", required=True, choices=sorted(SUPPORT_STATUSES)
    )
    parser.add_argument("--marker", required=True)
    parser.add_argument(
        "--compression-mode",
        default="on",
        choices=sorted(COMPRESSION_MODE_VALUES),
        help=(
            "Local proof mode for this real CLI series. Use off for explicit "
            "compression-disabled baselines."
        ),
    )
    parser.add_argument("--model-scope", action="append", default=[])
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument(
        "--db-correlation", required=True, choices=sorted(DB_CORRELATION_VALUES)
    )
    parser.add_argument("--db-rows-json", type=Path)
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--cost-status-override", choices=sorted(COST_STATUS_VALUES))
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = _load_rows(args.db_rows_json) if args.db_rows_json else []
    proof = build_proof_record(
        cli=args.cli,
        wrapper=args.wrapper,
        managed_home=args.managed_home,
        support_status=args.support_status,
        marker=args.marker,
        compression_mode=args.compression_mode,
        model_scope=args.model_scope,
        artifact_dir=args.artifact_dir,
        db_correlation=args.db_correlation,
        rows=rows,
        notes=args.note,
        cost_status_override=args.cost_status_override,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
