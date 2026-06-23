from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IngestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    request_id: str | None = None
    execution_id: str | None = None
    duplicate: bool


class RetrievedChunkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    ccr_hash: str
    compressed_content: str | None
    storage_policy: str
    metadata: dict[str, object]


class HeadroomCcrEntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hash: str = Field(min_length=1, max_length=255)
    original_content: str
    compressed_content: str
    original_tokens: int = Field(ge=0)
    compressed_tokens: int = Field(ge=0)
    original_item_count: int = Field(ge=0)
    compressed_item_count: int = Field(ge=0)
    tool_name: str | None = Field(default=None, max_length=255)
    tool_call_id: str | None = Field(default=None, max_length=255)
    query_context: str | None = None
    created_at: float
    ttl: int = Field(ge=0)
    tool_signature_hash: str | None = Field(default=None, max_length=255)
    compression_strategy: str | None = Field(default=None, max_length=255)
    retrieval_count: int = Field(default=0, ge=0)
    search_queries: list[str] = Field(default_factory=list)
    last_accessed: float | None = None


class CcrRetrievalRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieval_source: str = Field(default="headroom_ccr_backend", max_length=64)
    query_hash: str | None = Field(default=None, max_length=255)
    result_count: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    success: bool = True
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = None


class CcrRetrievalRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
