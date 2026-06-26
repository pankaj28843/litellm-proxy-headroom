from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .commands import (
    ChunkRetrievalCommand,
    CompressionActivityIngestCommand,
    SimulationRunCommand,
)
from .simulation_schemas import SimulationRunDetail


@dataclass(frozen=True, slots=True)
class IngestionResult:
    event_id: str
    request_id: str | None = None
    execution_id: str | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    ccr_hash: str
    compressed_content: str | None
    storage_policy: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class StoredCcrEntry:
    hash: str
    original_content: str
    compressed_content: str
    original_tokens: int
    compressed_tokens: int
    original_item_count: int
    compressed_item_count: int
    tool_name: str | None
    tool_call_id: str | None
    query_context: str | None
    created_at: float
    ttl: int
    tool_signature_hash: str | None
    compression_strategy: str | None
    retrieval_count: int
    search_queries: list[str]
    last_accessed: float | None


class AnalyticsCommandStore(Protocol):
    async def ingest_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> IngestionResult: ...

    async def retrieve_chunk(self, ccr_hash: str) -> RetrievedChunk | None: ...

    async def record_chunk_retrieval(self, command: ChunkRetrievalCommand) -> str: ...


class SimulationStore(Protocol):
    async def run_simulation(
        self, command: SimulationRunCommand
    ) -> SimulationRunDetail: ...


class AnalyticsIngestionService:
    def __init__(self, store: AnalyticsCommandStore) -> None:
        self._store = store

    async def ingest_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> IngestionResult:
        return await self._store.ingest_compression_activity(command)


class ChunkRetrievalService:
    def __init__(self, store: AnalyticsCommandStore) -> None:
        self._store = store

    async def retrieve_by_ccr_hash(
        self, command: ChunkRetrievalCommand
    ) -> RetrievedChunk | None:
        chunk = await self._store.retrieve_chunk(command.ccr_hash)
        await self._store.record_chunk_retrieval(command)
        return chunk


class SimulationService:
    def __init__(self, store: SimulationStore) -> None:
        self._store = store

    async def run_simulation(
        self, command: SimulationRunCommand
    ) -> SimulationRunDetail:
        return await self._store.run_simulation(command)
