from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..otel.telemetry import get_analytics_telemetry
from ..postgres.session import session_scope
from ..postgres.stats_queries import compression_stats
from .retrieval import retrieve_chunk_and_record

SessionFactoryProvider = Callable[[], async_sessionmaker[AsyncSession]]


class McpChunkRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    ccr_hash: str
    chunk_id: str | None = None
    compressed_content: str | None = None
    storage_policy: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_event_id: str
    latency_ms: int


class McpStatsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int
    executions: int
    chunks: int
    provider_calls: int
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    retrievals: int


def create_analytics_mcp_server(
    session_factory_provider: SessionFactoryProvider,
) -> FastMCP:
    mcp = FastMCP(
        "Headroom Token Compression Analytics",
        mask_error_details=True,
    )

    @mcp.tool(
        name="headroom_analytics_retrieve_chunk",
        description="Retrieve a compressed chunk by CCR hash from analytics storage.",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def retrieve_chunk(
        ccr_hash: Annotated[str, Field(min_length=1, max_length=255)],
        source: Annotated[str, Field(min_length=1, max_length=64)] = "mcp",
        query_hash: Annotated[str | None, Field(max_length=255)] = None,
    ) -> McpChunkRetrievalResult:
        telemetry = get_analytics_telemetry()
        started = time.perf_counter()
        attrs = {
            "headroom.analytics.operation": "mcp_retrieve_chunk",
            "headroom.analytics.retrieval.source": source,
            "headroom.analytics.ccr_hash": ccr_hash,
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "headroom_analytics_retrieve_chunk",
            "gen_ai.tool.type": "datastore",
        }
        with telemetry.start_span("headroom.analytics.mcp.retrieve_chunk", attrs):
            async with session_scope(session_factory_provider()) as session:
                outcome = await retrieve_chunk_and_record(
                    session,
                    ccr_hash=ccr_hash,
                    source=source,
                    query_hash=query_hash,
                )
            latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
            telemetry.record_retrieval(
                source=source,
                found=outcome.found,
                latency_ms=latency_ms,
                operation="mcp_retrieve_chunk",
            )
            if outcome.chunk is None:
                return McpChunkRetrievalResult(
                    found=False,
                    ccr_hash=ccr_hash,
                    retrieval_event_id=outcome.event_id,
                    latency_ms=latency_ms,
                )
            return McpChunkRetrievalResult(
                found=True,
                ccr_hash=ccr_hash,
                chunk_id=outcome.chunk.chunk_id,
                compressed_content=outcome.chunk.compressed_content,
                storage_policy=outcome.chunk.storage_policy,
                metadata=outcome.chunk.metadata,
                retrieval_event_id=outcome.event_id,
                latency_ms=latency_ms,
            )

    @mcp.tool(
        name="headroom_analytics_stats",
        description="Return dashboard-ready aggregate analytics counters.",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def stats() -> McpStatsResult:
        with get_analytics_telemetry().start_span(
            "headroom.analytics.mcp.stats",
            {
                "headroom.analytics.operation": "mcp_stats",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "headroom_analytics_stats",
                "gen_ai.tool.type": "datastore",
            },
        ):
            async with session_scope(session_factory_provider()) as session:
                snapshot = await compression_stats(session)
        return McpStatsResult(**snapshot.model_dump())

    return mcp
