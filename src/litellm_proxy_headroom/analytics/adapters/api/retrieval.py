from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ...application.commands import ChunkRetrievalCommand
from ...application.services import RetrievedChunk
from ..postgres.repositories import AnalyticsPostgresRepository


@dataclass(frozen=True, slots=True)
class ChunkRetrievalOutcome:
    chunk: RetrievedChunk | None
    event_id: str
    latency_ms: int

    @property
    def found(self) -> bool:
        return self.chunk is not None


async def retrieve_chunk_and_record(
    session: AsyncSession,
    *,
    ccr_hash: str,
    source: str,
    query_hash: str | None = None,
) -> ChunkRetrievalOutcome:
    repository = AnalyticsPostgresRepository(session)
    started = time.perf_counter()
    chunk = await repository.retrieve_chunk(ccr_hash)
    latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
    event_id = await repository.record_chunk_retrieval(
        ChunkRetrievalCommand(
            ccr_hash=ccr_hash,
            retrieval_source=source,
            query_hash=query_hash,
            result_count=1 if chunk is not None else 0,
            latency_ms=latency_ms,
            success=chunk is not None,
            error_type=None if chunk is not None else "not_found",
        )
    )
    return ChunkRetrievalOutcome(
        chunk=chunk,
        event_id=event_id,
        latency_ms=latency_ms,
    )
