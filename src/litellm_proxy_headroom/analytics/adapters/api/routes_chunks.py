from __future__ import annotations

from fastapi import APIRouter, status
from starlette.responses import JSONResponse

from ..otel.telemetry import get_analytics_telemetry
from .deps import SessionDep
from .dto import RetrievedChunkResponse
from .retrieval import retrieve_chunk_and_record

router = APIRouter()


@router.get("/chunks/{ccr_hash}", response_model=RetrievedChunkResponse)
async def get_chunk(
    ccr_hash: str,
    session: SessionDep,
    source: str = "api",
) -> RetrievedChunkResponse | JSONResponse:
    telemetry = get_analytics_telemetry()
    with telemetry.start_span(
        "litellm.proxy.analytics.retrieve_chunk",
        {
            "litellm.proxy.analytics.operation": "retrieve_chunk",
            "litellm.proxy.analytics.retrieval.source": source,
            "litellm.proxy.analytics.ccr_hash": ccr_hash,
        },
    ):
        outcome = await retrieve_chunk_and_record(
            session,
            ccr_hash=ccr_hash,
            source=source,
        )
    telemetry.record_retrieval(
        source=source,
        found=outcome.found,
        latency_ms=outcome.latency_ms,
    )
    if outcome.chunk is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "chunk not found", "ccr_hash": ccr_hash},
        )
    return RetrievedChunkResponse(
        chunk_id=outcome.chunk.chunk_id,
        ccr_hash=outcome.chunk.ccr_hash,
        compressed_content=outcome.chunk.compressed_content,
        storage_policy=outcome.chunk.storage_policy,
        metadata=outcome.chunk.metadata,
    )
