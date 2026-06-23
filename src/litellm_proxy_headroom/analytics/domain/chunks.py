from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .types import TraceContext


class ChunkStoragePolicy(StrEnum):
    HASH_ONLY = "hash_only"
    PLAINTEXT = "plaintext"
    ENCRYPTED = "encrypted"
    EXTERNAL_REF = "external_ref"


@dataclass(frozen=True, slots=True)
class CompressionChunk:
    request_key: str
    execution_attempt: int
    ordinal: int
    ccr_hash: str | None = None
    content_hash: str | None = None
    role: str | None = None
    tool_name: str | None = None
    original_tokens: int | None = None
    compressed_tokens: int | None = None
    original_bytes: int | None = None
    compressed_bytes: int | None = None
    item_count: int | None = None
    storage_policy: ChunkStoragePolicy = ChunkStoragePolicy.HASH_ONLY
    original_content: str | None = None
    compressed_content: str | None = None
    original_content_ref: str | None = None
    compressed_content_ref: str | None = None
    retention_expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChunkRetrievalEvent:
    retrieval_source: str
    ccr_hash: str
    retrieved_at: datetime
    chunk_id: str | None = None
    query_hash: str | None = None
    result_count: int | None = None
    latency_ms: int | None = None
    success: bool = True
    error_type: str | None = None
    error_message: str | None = None
    trace: TraceContext = field(default_factory=TraceContext)
