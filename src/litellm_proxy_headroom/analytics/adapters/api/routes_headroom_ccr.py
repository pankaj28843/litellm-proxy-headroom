from __future__ import annotations

from fastapi import APIRouter, status
from starlette.responses import JSONResponse

from ...application.commands import ChunkRetrievalCommand
from ...application.services import AnalyticsIngestionService
from ..otel.telemetry import get_analytics_telemetry
from ..postgres.repositories import AnalyticsPostgresRepository
from .ccr_mapping import ccr_ingest_command
from .deps import SessionDep
from .dto import (
    CcrRetrievalRecordRequest,
    CcrRetrievalRecordResponse,
    HeadroomCcrEntryPayload,
    IngestionResponse,
)

router = APIRouter()


@router.put("/headroom/ccr/{hash_key}", response_model=IngestionResponse)
async def put_headroom_ccr_entry(
    hash_key: str,
    entry: HeadroomCcrEntryPayload,
    session: SessionDep,
) -> IngestionResponse | JSONResponse:
    if entry.hash != hash_key:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "hash path and payload mismatch"},
        )
    repository = AnalyticsPostgresRepository(session)
    result = await AnalyticsIngestionService(repository).ingest_compression_activity(
        ccr_ingest_command(entry)
    )
    return IngestionResponse(
        event_id=result.event_id,
        request_id=result.request_id,
        execution_id=result.execution_id,
        duplicate=result.duplicate,
    )


@router.get("/headroom/ccr/{hash_key}", response_model=HeadroomCcrEntryPayload)
async def get_headroom_ccr_entry(
    hash_key: str,
    session: SessionDep,
) -> HeadroomCcrEntryPayload | JSONResponse:
    repository = AnalyticsPostgresRepository(session)
    entry = await repository.retrieve_ccr_entry(hash_key)
    if entry is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "CCR entry not found", "ccr_hash": hash_key},
        )
    return HeadroomCcrEntryPayload(
        hash=entry.hash,
        original_content=entry.original_content,
        compressed_content=entry.compressed_content,
        original_tokens=entry.original_tokens,
        compressed_tokens=entry.compressed_tokens,
        original_item_count=entry.original_item_count,
        compressed_item_count=entry.compressed_item_count,
        tool_name=entry.tool_name,
        tool_call_id=entry.tool_call_id,
        query_context=entry.query_context,
        created_at=entry.created_at,
        ttl=entry.ttl,
        tool_signature_hash=entry.tool_signature_hash,
        compression_strategy=entry.compression_strategy,
        retrieval_count=entry.retrieval_count,
        search_queries=entry.search_queries,
        last_accessed=entry.last_accessed,
    )


@router.post(
    "/headroom/ccr/{hash_key}/retrievals",
    response_model=CcrRetrievalRecordResponse,
)
async def record_headroom_ccr_retrieval(
    hash_key: str,
    command: CcrRetrievalRecordRequest,
    session: SessionDep,
) -> CcrRetrievalRecordResponse:
    telemetry = get_analytics_telemetry()
    repository = AnalyticsPostgresRepository(session)
    with telemetry.start_span(
        "litellm.proxy.analytics.ccr.record_retrieval",
        {
            "litellm.proxy.analytics.operation": "record_ccr_retrieval",
            "litellm.proxy.analytics.retrieval.source": command.retrieval_source,
            "litellm.proxy.analytics.ccr_hash": hash_key,
        },
    ):
        event_id = await repository.record_chunk_retrieval(
            ChunkRetrievalCommand(
                ccr_hash=hash_key,
                retrieval_source=command.retrieval_source,
                query_hash=command.query_hash,
                result_count=command.result_count,
                latency_ms=command.latency_ms,
                success=command.success,
                error_type=command.error_type,
                error_message=command.error_message,
            )
        )
    telemetry.record_retrieval(
        source=command.retrieval_source,
        found=command.success,
        latency_ms=command.latency_ms or 0,
        operation="record_ccr_retrieval",
    )
    return CcrRetrievalRecordResponse(event_id=event_id)
