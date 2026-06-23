from __future__ import annotations

from fastapi import APIRouter

from . import (
    routes_chunks,
    routes_dashboard,
    routes_headroom_ccr,
    routes_health,
    routes_ingest,
    routes_records,
    routes_simulations,
    routes_stats,
)

router = APIRouter()
router.include_router(routes_health.router)
router.include_router(routes_ingest.router)
router.include_router(routes_headroom_ccr.router)
router.include_router(routes_chunks.router)
router.include_router(routes_records.router)
router.include_router(routes_simulations.router)
router.include_router(routes_stats.router)
router.include_router(routes_dashboard.router)
