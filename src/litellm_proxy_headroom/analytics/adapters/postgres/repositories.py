from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.commands import (
    CacheActivityCommand,
    ChunkRetrievalCommand,
    CompressionActivityIngestCommand,
    CompressionChunkCommand,
    CostCalculationCommand,
    ProviderCallCommand,
    TokenUsageBreakdownCommand,
    TraceContextCommand,
)
from ...application.services import IngestionResult, RetrievedChunk, StoredCcrEntry
from .models import (
    CacheActivityModel,
    ChunkRetrievalEventModel,
    CompressionChunkModel,
    CompressionConfigSnapshotModel,
    CompressionExecutionModel,
    CompressionRequestModel,
    CostCalculationModel,
    IngestionEventModel,
    ProviderCallModel,
    TokenUsageBreakdownModel,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _trace_columns(trace: TraceContextCommand) -> dict[str, str | None]:
    return {"trace_id": trace.trace_id, "span_id": trace.span_id}


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    return uuid.UUID(value)


class AnalyticsPostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ingest_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> IngestionResult:
        event_id, duplicate = await self._insert_ingestion_event(command)
        if duplicate:
            return IngestionResult(event_id=str(event_id), duplicate=True)

        try:
            async with self._session.begin_nested():
                request_id = await self._ensure_request(command)
                config_id = await self._ensure_config(command)
                execution_id = await self._ensure_execution(
                    command, request_id, config_id
                )
                chunk_ids = await self._insert_chunks(command.chunks, execution_id)
                provider_call_ids = await self._insert_provider_calls(
                    command.provider_calls, request_id, execution_id
                )
                await self._insert_cache_activities(
                    command.cache_activities,
                    request_id=request_id,
                    execution_id=execution_id,
                    provider_call_ids=provider_call_ids,
                    chunk_ids=chunk_ids,
                )
            await self._mark_event_processed(event_id)
            return IngestionResult(
                event_id=str(event_id),
                request_id=str(request_id),
                execution_id=str(execution_id),
            )
        except Exception as exc:
            await self._mark_event_failed(event_id, exc)
            raise

    async def retrieve_chunk(self, ccr_hash: str) -> RetrievedChunk | None:
        result = await self._session.execute(
            select(CompressionChunkModel)
            .where(CompressionChunkModel.ccr_hash == ccr_hash)
            .order_by(CompressionChunkModel.created_at.desc())
            .limit(1)
        )
        chunk = result.scalar_one_or_none()
        if chunk is None:
            return None
        return RetrievedChunk(
            chunk_id=str(chunk.id),
            ccr_hash=ccr_hash,
            compressed_content=chunk.compressed_content,
            storage_policy=chunk.storage_policy,
            metadata=dict(chunk.storage_metadata),
        )

    async def retrieve_ccr_entry(self, ccr_hash: str) -> StoredCcrEntry | None:
        result = await self._session.execute(
            select(CompressionChunkModel)
            .where(CompressionChunkModel.ccr_hash == ccr_hash)
            .order_by(CompressionChunkModel.created_at.desc())
            .limit(1)
        )
        chunk = result.scalar_one_or_none()
        if (
            chunk is None
            or chunk.original_content is None
            or chunk.compressed_content is None
        ):
            return None

        metadata = dict(chunk.storage_metadata)
        retrieval_count = await self._session.scalar(
            select(func.count(ChunkRetrievalEventModel.id)).where(
                ChunkRetrievalEventModel.ccr_hash == ccr_hash,
                ChunkRetrievalEventModel.success.is_(True),
            )
        )
        search_queries = metadata.get("search_queries")
        if not isinstance(search_queries, list):
            search_queries = []
        raw_last_accessed = metadata.get("last_accessed")

        return StoredCcrEntry(
            hash=ccr_hash,
            original_content=chunk.original_content,
            compressed_content=chunk.compressed_content,
            original_tokens=chunk.original_tokens or 0,
            compressed_tokens=chunk.compressed_tokens or 0,
            original_item_count=int(
                metadata.get("original_item_count") or chunk.item_count or 0
            ),
            compressed_item_count=int(metadata.get("compressed_item_count") or 0),
            tool_name=chunk.tool_name,
            tool_call_id=metadata.get("tool_call_id"),
            query_context=metadata.get("query_context"),
            created_at=float(
                metadata.get("headroom_created_at") or chunk.created_at.timestamp()
            ),
            ttl=int(metadata.get("headroom_ttl") or 1800),
            tool_signature_hash=metadata.get("tool_signature_hash"),
            compression_strategy=metadata.get("compression_strategy"),
            retrieval_count=int(retrieval_count or 0),
            search_queries=[str(query) for query in search_queries],
            last_accessed=float(raw_last_accessed)
            if raw_last_accessed is not None
            else None,
        )

    async def record_chunk_retrieval(self, command: ChunkRetrievalCommand) -> str:
        chunk_id = await self._chunk_id_for_hash(command.ccr_hash)
        event = ChunkRetrievalEventModel(
            chunk_id=chunk_id,
            ccr_hash=command.ccr_hash,
            retrieval_source=command.retrieval_source,
            query_hash=command.query_hash,
            result_count=command.result_count,
            latency_ms=command.latency_ms,
            success=command.success,
            error_type=command.error_type,
            error_message=command.error_message,
            retrieved_at=command.retrieved_at or _utcnow(),
            **_trace_columns(command.trace),
        )
        self._session.add(event)
        await self._session.flush()
        return str(event.id)

    async def _insert_ingestion_event(
        self, command: CompressionActivityIngestCommand
    ) -> tuple[uuid.UUID, bool]:
        payload = command.event.raw_payload or command.model_dump(mode="json")
        payload_hash = command.event.payload_hash or _payload_hash(payload)
        stmt = (
            insert(IngestionEventModel)
            .values(
                source=command.event.source,
                event_type=command.event.event_type,
                event_key=command.event.event_key,
                payload_hash=payload_hash,
                raw_payload=payload,
                status="received",
                **_trace_columns(command.event.trace),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IngestionEventModel.source,
                    IngestionEventModel.event_type,
                    IngestionEventModel.event_key,
                ]
            )
            .returning(IngestionEventModel.id)
        )
        event_id = await self._session.scalar(stmt)
        if event_id is not None:
            return event_id, False

        existing_id = await self._session.scalar(
            select(IngestionEventModel.id).where(
                IngestionEventModel.source == command.event.source,
                IngestionEventModel.event_type == command.event.event_type,
                IngestionEventModel.event_key == command.event.event_key,
            )
        )
        if existing_id is None:
            raise RuntimeError("ingestion event conflict did not return existing row")
        return existing_id, True

    async def _ensure_request(
        self, command: CompressionActivityIngestCommand
    ) -> uuid.UUID:
        request = command.request
        stmt = (
            insert(CompressionRequestModel)
            .values(
                request_key=request.request_key,
                source_system=request.source_system,
                tenant_id=request.tenant_id,
                team_id=request.team_id,
                user_id=request.user_id,
                incoming_route=request.incoming_route,
                provider_hint=request.provider_hint,
                model_hint=request.model_hint,
                external_request_id=request.external_request_id,
                started_at=request.started_at,
                ended_at=request.ended_at,
                request_metadata=request.metadata,
                **_trace_columns(request.trace),
            )
            .on_conflict_do_nothing(
                index_elements=[CompressionRequestModel.request_key]
            )
            .returning(CompressionRequestModel.id)
        )
        request_id = await self._session.scalar(stmt)
        if request_id is not None:
            return request_id
        existing_id = await self._session.scalar(
            select(CompressionRequestModel.id).where(
                CompressionRequestModel.request_key == request.request_key
            )
        )
        if existing_id is None:
            raise RuntimeError("request conflict did not return existing row")
        return existing_id

    async def _ensure_config(
        self, command: CompressionActivityIngestCommand
    ) -> uuid.UUID:
        config = command.config
        strategy_version = config.strategy_version or ""
        stmt = (
            insert(CompressionConfigSnapshotModel)
            .values(
                config_hash=config.config_hash,
                strategy_name=config.strategy_name,
                strategy_version=strategy_version,
                algorithm=config.algorithm,
                target_model=config.target_model,
                token_budget=config.token_budget,
                trigger_reason=config.trigger_reason,
                raw_config=config.raw_config,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    CompressionConfigSnapshotModel.config_hash,
                    CompressionConfigSnapshotModel.strategy_name,
                    CompressionConfigSnapshotModel.strategy_version,
                ]
            )
            .returning(CompressionConfigSnapshotModel.id)
        )
        config_id = await self._session.scalar(stmt)
        if config_id is not None:
            return config_id
        existing_id = await self._session.scalar(
            select(CompressionConfigSnapshotModel.id).where(
                CompressionConfigSnapshotModel.config_hash == config.config_hash,
                CompressionConfigSnapshotModel.strategy_name == config.strategy_name,
                CompressionConfigSnapshotModel.strategy_version == strategy_version,
            )
        )
        if existing_id is None:
            raise RuntimeError("config conflict did not return existing row")
        return existing_id

    async def _ensure_execution(
        self,
        command: CompressionActivityIngestCommand,
        request_id: uuid.UUID,
        config_id: uuid.UUID,
    ) -> uuid.UUID:
        execution = command.execution
        values = {
            "request_id": request_id,
            "config_snapshot_id": config_id,
            "attempt_number": execution.attempt_number,
            "is_simulated": execution.is_simulated,
            "status": execution.status,
            "started_at": execution.started_at,
            "ended_at": execution.ended_at,
            "duration_ms": execution.duration_ms,
            "original_tokens": execution.original_tokens,
            "compressed_tokens": execution.compressed_tokens,
            "tokens_saved": execution.tokens_saved,
            "compression_ratio": execution.compression_ratio,
            "transforms": execution.transforms,
            "error_type": execution.error_type,
            "error_message": execution.error_message,
            **_trace_columns(execution.trace),
        }
        stmt = insert(CompressionExecutionModel).values(**values)
        if not execution.is_simulated:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[
                    CompressionExecutionModel.request_id,
                    CompressionExecutionModel.attempt_number,
                ],
                index_where=text("is_simulated = false"),
            )
        execution_id = await self._session.scalar(
            stmt.returning(CompressionExecutionModel.id)
        )
        if execution_id is not None:
            return execution_id

        existing_id = await self._session.scalar(
            select(CompressionExecutionModel.id).where(
                CompressionExecutionModel.request_id == request_id,
                CompressionExecutionModel.attempt_number == execution.attempt_number,
                CompressionExecutionModel.is_simulated.is_(execution.is_simulated),
            )
        )
        if existing_id is None:
            raise RuntimeError("execution conflict did not return existing row")
        return existing_id

    async def _insert_chunks(
        self, chunks: list[CompressionChunkCommand], execution_id: uuid.UUID
    ) -> dict[str, uuid.UUID]:
        chunk_ids: dict[str, uuid.UUID] = {}
        for chunk in chunks:
            stmt = (
                insert(CompressionChunkModel)
                .values(
                    execution_id=execution_id,
                    ordinal=chunk.ordinal,
                    role=chunk.role,
                    tool_name=chunk.tool_name,
                    ccr_hash=chunk.ccr_hash,
                    content_hash=chunk.content_hash,
                    original_tokens=chunk.original_tokens,
                    compressed_tokens=chunk.compressed_tokens,
                    original_bytes=chunk.original_bytes,
                    compressed_bytes=chunk.compressed_bytes,
                    item_count=chunk.item_count,
                    storage_policy=chunk.storage_policy,
                    original_content=chunk.original_content,
                    compressed_content=chunk.compressed_content,
                    original_content_ref=chunk.original_content_ref,
                    compressed_content_ref=chunk.compressed_content_ref,
                    retention_expires_at=chunk.retention_expires_at,
                    storage_metadata=chunk.metadata,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        CompressionChunkModel.execution_id,
                        CompressionChunkModel.ordinal,
                    ]
                )
                .returning(CompressionChunkModel.id)
            )
            chunk_id = await self._session.scalar(stmt)
            if chunk_id is None:
                chunk_id = await self._session.scalar(
                    select(CompressionChunkModel.id).where(
                        CompressionChunkModel.execution_id == execution_id,
                        CompressionChunkModel.ordinal == chunk.ordinal,
                    )
                )
            if chunk_id is not None and chunk.ccr_hash:
                chunk_ids[chunk.ccr_hash] = chunk_id
        return chunk_ids

    async def _insert_provider_calls(
        self,
        calls: list[ProviderCallCommand],
        request_id: uuid.UUID,
        execution_id: uuid.UUID,
    ) -> dict[str, uuid.UUID]:
        provider_call_ids: dict[str, uuid.UUID] = {}
        for call in calls:
            call_execution_id = (
                execution_id if call.execution_attempt is not None else None
            )
            stmt = (
                insert(ProviderCallModel)
                .values(
                    request_id=request_id,
                    execution_id=call_execution_id,
                    provider_call_key=call.provider_call_key,
                    provider=call.provider,
                    model=call.model,
                    litellm_call_id=call.litellm_call_id,
                    provider_request_id=call.provider_request_id,
                    provider_response_id=call.provider_response_id,
                    status=call.status,
                    started_at=call.started_at,
                    ended_at=call.ended_at,
                    duration_ms=call.duration_ms,
                    cost_total=call.cost_total,
                    currency=call.currency,
                    error_type=call.error_type,
                    error_message=call.error_message,
                    raw_response_metadata=call.raw_response_metadata,
                    **_trace_columns(call.trace),
                )
                .on_conflict_do_nothing(
                    index_elements=[ProviderCallModel.provider_call_key]
                )
                .returning(ProviderCallModel.id)
            )
            provider_call_id = await self._session.scalar(stmt)
            if provider_call_id is None:
                provider_call_id = await self._session.scalar(
                    select(ProviderCallModel.id).where(
                        ProviderCallModel.provider_call_key == call.provider_call_key
                    )
                )
            if provider_call_id is None:
                raise RuntimeError("provider call conflict did not return row")
            provider_call_ids[call.provider_call_key] = provider_call_id
            await self._insert_token_usage(call.token_usage, provider_call_id, None)
            await self._insert_costs(call.cost_calculations, provider_call_id, None)
        return provider_call_ids

    async def _insert_token_usage(
        self,
        usages: list[TokenUsageBreakdownCommand],
        provider_call_id: uuid.UUID | None,
        execution_id: uuid.UUID | None,
    ) -> None:
        for usage in usages:
            self._session.add(
                TokenUsageBreakdownModel(
                    provider_call_id=provider_call_id,
                    execution_id=execution_id,
                    measurement_source=usage.measurement_source,
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    newly_processed_input_tokens=usage.newly_processed_input_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=usage.reasoning_tokens,
                    total_tokens=usage.total_tokens,
                    raw_usage=usage.raw_usage,
                )
            )

    async def _insert_costs(
        self,
        costs: list[CostCalculationCommand],
        provider_call_id: uuid.UUID | None,
        execution_id: uuid.UUID | None,
    ) -> None:
        for cost in costs:
            self._session.add(
                CostCalculationModel(
                    provider_call_id=provider_call_id,
                    execution_id=execution_id,
                    pricing_snapshot_id=_uuid_or_none(cost.pricing_snapshot_id),
                    calculation_kind=cost.calculation_kind,
                    input_cost=cost.input_cost,
                    cached_input_cost=cost.cached_input_cost,
                    cache_write_cost=cost.cache_write_cost,
                    output_cost=cost.output_cost,
                    reasoning_cost=cost.reasoning_cost,
                    total_cost=cost.total_cost,
                    currency=cost.currency,
                    assumptions=cost.assumptions,
                )
            )

    async def _insert_cache_activities(
        self,
        activities: list[CacheActivityCommand],
        *,
        request_id: uuid.UUID,
        execution_id: uuid.UUID,
        provider_call_ids: dict[str, uuid.UUID],
        chunk_ids: dict[str, uuid.UUID],
    ) -> None:
        for activity in activities:
            self._session.add(
                CacheActivityModel(
                    request_id=request_id,
                    execution_id=execution_id
                    if activity.execution_attempt is not None
                    else None,
                    provider_call_id=provider_call_ids.get(activity.provider_call_key)
                    if activity.provider_call_key
                    else None,
                    chunk_id=chunk_ids.get(activity.ccr_hash)
                    if activity.ccr_hash
                    else None,
                    cache_system=activity.cache_system,
                    operation=activity.operation,
                    hit=activity.hit,
                    tokens_read=activity.tokens_read,
                    tokens_written=activity.tokens_written,
                    key_hash=activity.key_hash,
                    ttl_seconds=activity.ttl_seconds,
                    occurred_at=activity.occurred_at or _utcnow(),
                    activity_metadata=activity.metadata,
                )
            )

    async def _chunk_id_for_hash(self, ccr_hash: str) -> uuid.UUID | None:
        return await self._session.scalar(
            select(CompressionChunkModel.id)
            .where(CompressionChunkModel.ccr_hash == ccr_hash)
            .order_by(CompressionChunkModel.created_at.desc())
            .limit(1)
        )

    async def _mark_event_processed(self, event_id: uuid.UUID) -> None:
        await self._session.execute(
            update(IngestionEventModel)
            .where(IngestionEventModel.id == event_id)
            .values(status="processed", processed_at=_utcnow())
        )

    async def _mark_event_failed(self, event_id: uuid.UUID, exc: Exception) -> None:
        await self._session.execute(
            update(IngestionEventModel)
            .where(IngestionEventModel.id == event_id)
            .values(
                status="failed",
                processed_at=_utcnow(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        )
