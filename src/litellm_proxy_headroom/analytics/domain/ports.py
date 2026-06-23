from __future__ import annotations

from typing import Protocol

from .chunks import ChunkRetrievalEvent, CompressionChunk
from .compression import CompressionExecution, CompressionRequest
from .provider_usage import ProviderCall


class CompressionHistoryRepository(Protocol):
    async def add_request(self, request: CompressionRequest) -> str: ...

    async def add_execution(self, execution: CompressionExecution) -> str: ...


class ProviderUsageRepository(Protocol):
    async def add_provider_call(self, call: ProviderCall) -> str: ...


class CompressedChunkRepository(Protocol):
    async def add_chunk(self, chunk: CompressionChunk) -> str: ...

    async def get_by_ccr_hash(self, ccr_hash: str) -> CompressionChunk | None: ...

    async def record_retrieval(self, event: ChunkRetrievalEvent) -> str: ...


class AnalyticsStatisticsReader(Protocol):
    async def compression_summary(self) -> dict[str, object]: ...
