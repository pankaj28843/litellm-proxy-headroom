from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

type DataScope = Literal["real", "test", "all"]


@dataclass(frozen=True, slots=True)
class AnalyticsFilters:
    started_from: datetime | None = None
    started_to: datetime | None = None
    provider: str | None = None
    model: str | None = None
    strategy: str | None = None
    tenant_id: str | None = None
    team_id: str | None = None
    status: str | None = None
    negative_savings: bool | None = None
    data_scope: DataScope = "real"
