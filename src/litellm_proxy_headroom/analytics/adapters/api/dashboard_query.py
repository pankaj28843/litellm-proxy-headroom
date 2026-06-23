from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Query

from ...application.query_filters import AnalyticsFilters

type DashboardRangePreset = Literal["15m", "1h", "24h", "7d", "30d", "all", "custom"]


@dataclass(frozen=True, slots=True)
class DashboardPresetOption:
    value: DashboardRangePreset
    label: str


DASHBOARD_PRESETS: tuple[DashboardPresetOption, ...] = (
    DashboardPresetOption("15m", "Last 15 minutes"),
    DashboardPresetOption("1h", "Last hour"),
    DashboardPresetOption("24h", "Last 24 hours"),
    DashboardPresetOption("7d", "Last 7 days"),
    DashboardPresetOption("30d", "Last 30 days"),
    DashboardPresetOption("all", "All time"),
    DashboardPresetOption("custom", "Custom"),
)

_PRESET_LABELS = {option.value: option.label for option in DASHBOARD_PRESETS}


@dataclass(frozen=True, slots=True)
class DashboardQuery:
    filters: AnalyticsFilters
    preset: DashboardRangePreset
    explicit_from: datetime | None
    explicit_to: datetime | None
    live: bool
    paused: bool
    generated_at: datetime

    @property
    def auto_refresh(self) -> bool:
        return self.live and not self.paused

    @property
    def window_label(self) -> str:
        if self.preset == "custom":
            if self.explicit_from and self.explicit_to:
                return f"{_format_dt(self.explicit_from)} to {_format_dt(self.explicit_to)}"
            if self.explicit_from:
                return f"From {_format_dt(self.explicit_from)}"
            if self.explicit_to:
                return f"Until {_format_dt(self.explicit_to)}"
        return _PRESET_LABELS[self.preset]

    def query_items(
        self,
        *,
        overrides: Mapping[str, str | bool | None] | None = None,
    ) -> tuple[tuple[str, str], ...]:
        values: dict[str, str | bool | None] = {
            "preset": self.preset,
            "from": self.explicit_from.isoformat() if self.explicit_from else None,
            "to": self.explicit_to.isoformat() if self.explicit_to else None,
            "provider": self.filters.provider,
            "model": self.filters.model,
            "strategy": self.filters.strategy,
            "tenant_id": self.filters.tenant_id,
            "team_id": self.filters.team_id,
            "status": self.filters.status,
            "negative_savings": self.filters.negative_savings,
            "live": self.live,
            "paused": self.paused,
        }
        if overrides:
            values.update(overrides)
        return tuple(
            (key, _query_value(value))
            for key, value in values.items()
            if value is not None and _query_value(value) != ""
        )

    def query_string(
        self,
        *,
        overrides: Mapping[str, str | bool | None] | None = None,
    ) -> str:
        return urlencode(self.query_items(overrides=overrides))


async def get_dashboard_query(
    preset: Annotated[DashboardRangePreset, Query()] = "24h",
    started_from: Annotated[str | None, Query(alias="from", max_length=64)] = None,
    started_to: Annotated[str | None, Query(alias="to", max_length=64)] = None,
    provider: Annotated[str | None, Query(max_length=128)] = None,
    model: Annotated[str | None, Query(max_length=255)] = None,
    strategy: Annotated[str | None, Query(max_length=128)] = None,
    tenant_id: Annotated[str | None, Query(max_length=128)] = None,
    team_id: Annotated[str | None, Query(max_length=128)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    negative_savings: Annotated[str | None, Query(max_length=8)] = None,
    live: bool = True,
    paused: bool = False,
) -> DashboardQuery:
    generated_at = datetime.now(UTC)
    explicit_from = _parse_optional_datetime(started_from, field_name="from")
    explicit_to = _parse_optional_datetime(started_to, field_name="to")
    selected_preset: DashboardRangePreset = (
        "custom" if explicit_from is not None or explicit_to is not None else preset
    )
    effective_from, effective_to = _date_window(
        selected_preset,
        generated_at=generated_at,
        started_from=explicit_from,
        started_to=explicit_to,
    )
    return DashboardQuery(
        filters=AnalyticsFilters(
            started_from=effective_from,
            started_to=effective_to,
            provider=_blank_to_none(provider),
            model=_blank_to_none(model),
            strategy=_blank_to_none(strategy),
            tenant_id=_blank_to_none(tenant_id),
            team_id=_blank_to_none(team_id),
            status=_blank_to_none(status),
            negative_savings=_parse_optional_bool(
                negative_savings,
                field_name="negative_savings",
            ),
        ),
        preset=selected_preset,
        explicit_from=explicit_from,
        explicit_to=explicit_to,
        live=live,
        paused=paused,
        generated_at=generated_at,
    )


def _date_window(
    preset: DashboardRangePreset,
    *,
    generated_at: datetime,
    started_from: datetime | None,
    started_to: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    if preset == "custom":
        return started_from, started_to
    if preset == "all":
        return None, None
    duration = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }[preset]
    return generated_at - duration, generated_at


def _format_dt(value: datetime) -> str:
    return value.isoformat(timespec="minutes")


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_optional_datetime(
    value: str | None,
    *,
    field_name: str,
) -> datetime | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Dashboard query field '{field_name}' must be an ISO datetime.",
        ) from exc


def _parse_optional_bool(
    value: str | None,
    *,
    field_name: str,
) -> bool | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered in {"1", "true", "on", "yes"}:
        return True
    if lowered in {"0", "false", "off", "no"}:
        return False
    raise HTTPException(
        status_code=422,
        detail=f"Dashboard query field '{field_name}' must be a boolean.",
    )


def _query_value(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


DashboardQueryDep = Annotated[DashboardQuery, Depends(get_dashboard_query)]
