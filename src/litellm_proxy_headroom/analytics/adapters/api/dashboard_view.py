from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ...application.dashboard_schemas import DashboardStats
from ...application.read_models import CompressionRecordPage, StatsBreakdown
from ...application.simulation_schemas import SimulationRunPage
from .dashboard_query import DASHBOARD_PRESETS, DashboardQuery


@dataclass(frozen=True, slots=True)
class DashboardMetric:
    label: str
    value: str
    detail: str
    tone: str = "neutral"


@dataclass(frozen=True, slots=True)
class DashboardBreakdownGroup:
    key: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class DashboardFilterChip:
    label: str
    value: str


BREAKDOWN_GROUPS: tuple[DashboardBreakdownGroup, ...] = (
    DashboardBreakdownGroup("provider", "Providers", "Savings by provider"),
    DashboardBreakdownGroup("model", "Models", "Savings by model"),
    DashboardBreakdownGroup("strategy", "Strategies", "Savings by strategy"),
    DashboardBreakdownGroup("status", "Statuses", "Execution health"),
)


def build_dashboard_context(
    *,
    query: DashboardQuery,
    stats: DashboardStats,
    breakdowns: dict[str, StatsBreakdown],
    records: CompressionRecordPage,
    simulations: SimulationRunPage,
) -> dict[str, object]:
    compression_ratio = (
        stats.compressed_tokens / stats.original_tokens
        if stats.original_tokens > 0
        else None
    )
    return {
        "presets": DASHBOARD_PRESETS,
        "query": query,
        "stats": stats,
        "summary_metrics": _summary_metrics(stats, compression_ratio),
        "risk_metrics": _risk_metrics(stats),
        "activity_metrics": _activity_metrics(stats),
        "breakdown_groups": BREAKDOWN_GROUPS,
        "breakdowns": breakdowns,
        "records": records,
        "simulations": simulations,
        "filter_chips": _filter_chips(query),
        "compression_ratio": compression_ratio,
        "has_data": stats.executions > 0,
        "database_ready": True,
        "backend_status": "ready",
    }


def _summary_metrics(
    stats: DashboardStats,
    compression_ratio: float | None,
) -> list[DashboardMetric]:
    return [
        DashboardMetric(
            "Tokens saved",
            format_int(stats.tokens_saved),
            f"{format_percent(stats.savings_percent)} saved",
            tone="success" if stats.tokens_saved >= 0 else "danger",
        ),
        DashboardMetric(
            "Estimated dollars",
            format_money(stats.cost.estimated_cost_savings),
            "baseline minus measured provider cost",
            tone=_money_tone(stats.cost.estimated_cost_savings),
        ),
        DashboardMetric(
            "Compression ratio",
            format_ratio(compression_ratio),
            f"{format_int(stats.compressed_tokens)} / {format_int(stats.original_tokens)} tokens",
            tone="info",
        ),
        DashboardMetric(
            "Success rate",
            format_percent(stats.success_rate),
            f"{format_int(stats.failed_executions)} failed executions",
            tone="success" if (stats.success_rate or 0) >= 95 else "warning",
        ),
    ]


def _risk_metrics(stats: DashboardStats) -> list[DashboardMetric]:
    return [
        DashboardMetric(
            "Negative savings",
            format_int(stats.negative_savings_executions),
            "executions expanded token count",
            tone="danger" if stats.negative_savings_executions else "success",
        ),
        DashboardMetric(
            "Cost increases",
            format_int(stats.cost.cost_increase_provider_calls),
            "provider calls cost more than baseline",
            tone="danger" if stats.cost.cost_increase_provider_calls else "success",
        ),
        DashboardMetric(
            "Failures",
            format_int(stats.failed_executions),
            "failed compression executions",
            tone="danger" if stats.failed_executions else "success",
        ),
    ]


def _activity_metrics(stats: DashboardStats) -> list[DashboardMetric]:
    return [
        DashboardMetric(
            "Retrievals",
            format_int(stats.retrievals),
            f"{format_float(stats.retrievals_per_chunk)} per chunk",
            tone="info",
        ),
        DashboardMetric(
            "Cache hits",
            format_int(stats.cache.cache_hit_events),
            f"{format_int(stats.cache.cache_read_events)} reads",
            tone="info",
        ),
        DashboardMetric(
            "Provider delta",
            format_signed_int(
                stats.provider_estimate_delta.estimated_after_provider_input_delta
            ),
            "estimated-after input vs provider-reported input",
            tone="neutral",
        ),
    ]


def _filter_chips(query: DashboardQuery) -> list[DashboardFilterChip]:
    filters = query.filters
    chips = [
        DashboardFilterChip("Provider", filters.provider or ""),
        DashboardFilterChip("Model", filters.model or ""),
        DashboardFilterChip("Strategy", filters.strategy or ""),
        DashboardFilterChip("Tenant", filters.tenant_id or ""),
        DashboardFilterChip("Team", filters.team_id or ""),
        DashboardFilterChip("Status", filters.status or ""),
    ]
    if filters.negative_savings is not None:
        chips.append(
            DashboardFilterChip(
                "Savings",
                "Negative only" if filters.negative_savings else "Non-negative",
            )
        )
    return [chip for chip in chips if chip.value]


def format_int(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def format_signed_int(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+,}"


def format_float(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.1f}%"


def format_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}x"


def format_money(value: str | None) -> str:
    amount = _decimal(value)
    if amount is None:
        return "n/a"
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.4f}"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.strftime("%Y-%m-%d %H:%M")


def datetime_input_value(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M")


def status_tone(value: str | None) -> str:
    return {
        "succeeded": "success",
        "failed": "danger",
        "running": "info",
        "pending": "warning",
    }.get((value or "").lower(), "neutral")


def _money_tone(value: str | None) -> str:
    amount = _decimal(value)
    if amount is None:
        return "neutral"
    if amount < 0:
        return "danger"
    if amount == 0:
        return "warning"
    return "success"


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None
