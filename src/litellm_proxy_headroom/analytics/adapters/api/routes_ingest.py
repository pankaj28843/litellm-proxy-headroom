from __future__ import annotations

import time

from fastapi import APIRouter

from ...application.commands import CompressionActivityIngestCommand
from ...application.services import AnalyticsIngestionService
from ..otel.telemetry import get_analytics_telemetry
from ..postgres.repositories import AnalyticsPostgresRepository
from .deps import SessionDep
from .dto import IngestionResponse

router = APIRouter()


@router.post("/ingest/compression", response_model=IngestionResponse)
async def ingest_compression(
    command: CompressionActivityIngestCommand,
    session: SessionDep,
) -> IngestionResponse:
    telemetry = get_analytics_telemetry()
    started = time.perf_counter()
    attrs = {
        "litellm.proxy.analytics.operation": "ingest",
        "litellm.proxy.analytics.event.source": command.event.source,
        "litellm.proxy.analytics.event.type": command.event.event_type,
    }
    with telemetry.start_span("litellm.proxy.analytics.ingest", attrs):
        repository = AnalyticsPostgresRepository(session)
        try:
            result = await AnalyticsIngestionService(
                repository
            ).ingest_compression_activity(command)
        except Exception as exc:
            latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
            telemetry.mark_span_error(exc)
            telemetry.record_ingest(command, latency_ms=latency_ms, success=False)
            raise
        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        telemetry.record_ingest(command, latency_ms=latency_ms, success=True)
    return IngestionResponse(
        event_id=result.event_id,
        request_id=result.request_id,
        execution_id=result.execution_id,
        duplicate=result.duplicate,
    )
