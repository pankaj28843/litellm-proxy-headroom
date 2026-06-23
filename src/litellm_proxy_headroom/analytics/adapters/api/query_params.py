from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query

from ...application.query_filters import AnalyticsFilters


async def get_analytics_filters(
    started_from: Annotated[datetime | None, Query(alias="from")] = None,
    started_to: Annotated[datetime | None, Query(alias="to")] = None,
    provider: Annotated[str | None, Query(max_length=128)] = None,
    model: Annotated[str | None, Query(max_length=255)] = None,
    strategy: Annotated[str | None, Query(max_length=128)] = None,
    tenant_id: Annotated[str | None, Query(max_length=128)] = None,
    team_id: Annotated[str | None, Query(max_length=128)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    negative_savings: bool | None = None,
) -> AnalyticsFilters:
    return AnalyticsFilters(
        started_from=started_from,
        started_to=started_to,
        provider=provider,
        model=model,
        strategy=strategy,
        tenant_id=tenant_id,
        team_id=team_id,
        status=status,
        negative_savings=negative_savings,
    )


AnalyticsFiltersDep = Annotated[AnalyticsFilters, Depends(get_analytics_filters)]
