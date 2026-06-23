from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from ..postgres.dashboard_stats_queries import dashboard_stats
from ..postgres.read_queries import list_compression_records
from ..postgres.simulation_read import list_simulation_runs
from ..postgres.stats_queries import compression_stats_breakdown
from .dashboard_query import DashboardQueryDep
from .dashboard_view import (
    BREAKDOWN_GROUPS,
    build_dashboard_context,
    datetime_input_value,
    format_datetime,
    format_float,
    format_int,
    format_money,
    format_percent,
    format_ratio,
    format_signed_int,
    status_tone,
)
from .deps import SessionDep

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
templates.env.filters.update(
    {
        "datetime_input": datetime_input_value,
        "datetime_short": format_datetime,
        "float": format_float,
        "integer": format_int,
        "money": format_money,
        "percent": format_percent,
        "ratio": format_ratio,
        "signed_integer": format_signed_int,
        "status_tone": status_tone,
    }
)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    context = await _dashboard_context(session, query)
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        context,
    )


@router.get("/dashboard/partials/live", response_class=HTMLResponse)
async def dashboard_live_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "live_region")


@router.get("/dashboard/partials/controls", response_class=HTMLResponse)
async def dashboard_controls_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "controls")


@router.get("/dashboard/partials/summary", response_class=HTMLResponse)
async def dashboard_summary_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "summary")


@router.get("/dashboard/partials/activity", response_class=HTMLResponse)
async def dashboard_activity_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "activity")


@router.get("/dashboard/partials/breakdowns", response_class=HTMLResponse)
async def dashboard_breakdowns_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "breakdowns")


@router.get("/dashboard/partials/records", response_class=HTMLResponse)
async def dashboard_records_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "records")


@router.get("/dashboard/partials/simulations", response_class=HTMLResponse)
async def dashboard_simulations_partial(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
) -> HTMLResponse:
    return await _partial_response(request, session, query, "simulations")


async def _partial_response(
    request: Request,
    session: SessionDep,
    query: DashboardQueryDep,
    partial_name: Literal[
        "activity",
        "breakdowns",
        "controls",
        "live_region",
        "records",
        "simulations",
        "summary",
    ],
) -> HTMLResponse:
    context = await _dashboard_context(session, query)
    return templates.TemplateResponse(
        request,
        f"dashboard/partials/{partial_name}.html",
        context,
    )


async def _dashboard_context(session: SessionDep, query: DashboardQueryDep):
    stats = await dashboard_stats(session, query.filters)
    breakdowns = {
        group.key: await compression_stats_breakdown(
            session,
            query.filters,
            group_by=group.key,
            limit=8,
        )
        for group in BREAKDOWN_GROUPS
    }
    records = await list_compression_records(
        session,
        query.filters,
        limit=8,
        offset=0,
    )
    simulations = await list_simulation_runs(session, limit=6, offset=0)
    return build_dashboard_context(
        query=query,
        stats=stats,
        breakdowns=breakdowns,
        records=records,
        simulations=simulations,
    )
