from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ...application.commands import (
    CacheActivityCommand,
    CompressionActivityIngestCommand,
    CompressionChunkCommand,
    CompressionConfigCommand,
    CompressionExecutionCommand,
    CompressionRequestCommand,
    IngestionEventCommand,
)
from .dto import HeadroomCcrEntryPayload


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _retention_expires_at(created_at: float, ttl: int) -> datetime | None:
    if ttl <= 0:
        return None
    return datetime.fromtimestamp(created_at + ttl, UTC)


def ccr_ingest_command(
    entry: HeadroomCcrEntryPayload,
) -> CompressionActivityIngestCommand:
    content_hash = _content_hash(entry.original_content)
    config_hash = _stable_hash(
        {
            "compression_strategy": entry.compression_strategy,
            "tool_name": entry.tool_name,
        }
    )
    metadata = {
        "source": "headroom_ccr_backend",
        "headroom_created_at": entry.created_at,
        "headroom_ttl": entry.ttl,
        "tool_call_id": entry.tool_call_id,
        "query_context": entry.query_context,
        "original_item_count": entry.original_item_count,
        "compressed_item_count": entry.compressed_item_count,
        "tool_signature_hash": entry.tool_signature_hash,
        "compression_strategy": entry.compression_strategy,
        "search_queries": entry.search_queries,
        "last_accessed": entry.last_accessed,
    }
    return CompressionActivityIngestCommand(
        event=IngestionEventCommand(
            source="headroom-ccr-backend",
            event_type="ccr_entry_stored",
            event_key=f"{entry.hash}:{content_hash}",
            raw_payload={
                "hash": entry.hash,
                "content_hash": content_hash,
                "original_tokens": entry.original_tokens,
                "compressed_tokens": entry.compressed_tokens,
                "original_bytes": len(entry.original_content.encode()),
                "compressed_bytes": len(entry.compressed_content.encode()),
                "tool_name": entry.tool_name,
                "compression_strategy": entry.compression_strategy,
            },
        ),
        request=CompressionRequestCommand(
            request_key=f"headroom-ccr:{entry.hash}:{content_hash[:16]}",
            source_system="headroom_ccr",
            incoming_route="/headroom/ccr",
            metadata={"ccr_hash": entry.hash, "content_hash": content_hash},
        ),
        config=CompressionConfigCommand(
            config_hash=config_hash,
            strategy_name=entry.compression_strategy or "headroom_ccr",
            strategy_version="1",
            raw_config={
                "tool_name": entry.tool_name,
                "tool_signature_hash": entry.tool_signature_hash,
            },
        ),
        execution=CompressionExecutionCommand(
            attempt_number=1,
            status="succeeded",
            original_tokens=entry.original_tokens,
            compressed_tokens=entry.compressed_tokens,
            tokens_saved=entry.original_tokens - entry.compressed_tokens,
            compression_ratio=(
                (entry.original_tokens - entry.compressed_tokens)
                / entry.original_tokens
                if entry.original_tokens
                else None
            ),
            transforms={"source": "headroom_ccr_backend"},
        ),
        chunks=[
            CompressionChunkCommand(
                ordinal=0,
                ccr_hash=entry.hash,
                content_hash=content_hash,
                tool_name=entry.tool_name,
                original_tokens=entry.original_tokens,
                compressed_tokens=entry.compressed_tokens,
                original_bytes=len(entry.original_content.encode()),
                compressed_bytes=len(entry.compressed_content.encode()),
                item_count=entry.original_item_count,
                storage_policy="plaintext",
                original_content=entry.original_content,
                compressed_content=entry.compressed_content,
                retention_expires_at=_retention_expires_at(entry.created_at, entry.ttl),
                metadata=metadata,
            )
        ],
        cache_activities=[
            CacheActivityCommand(
                cache_system="headroom_ccr",
                operation="write",
                execution_attempt=1,
                ccr_hash=entry.hash,
                tokens_written=entry.original_tokens,
                key_hash=entry.hash,
                ttl_seconds=entry.ttl,
                metadata={
                    "tool_name": entry.tool_name,
                    "compression_strategy": entry.compression_strategy,
                },
            )
        ],
    )
