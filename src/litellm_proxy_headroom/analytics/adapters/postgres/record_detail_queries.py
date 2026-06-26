from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.read_models import (
    CompressionChunkSummary,
    CompressionExecutionDetail,
    CompressionRequestDetail,
    ProviderCallSummary,
    TokenUsageSummary,
)
from .cache_activity_read import cache_activities_for_request
from .models import (
    ChunkRetrievalEventModel,
    CompressionChunkModel,
    CompressionConfigSnapshotModel,
    CompressionExecutionModel,
    CompressionRequestModel,
    ProviderCallModel,
    TokenUsageBreakdownModel,
)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


async def get_compression_record_detail(
    session: AsyncSession,
    request_key: str,
) -> CompressionRequestDetail | None:
    request = await session.scalar(
        select(CompressionRequestModel).where(
            CompressionRequestModel.request_key == request_key
        )
    )
    if request is None:
        return None

    execution_rows = await _execution_rows(session, request.id)
    execution_ids = [execution.id for execution, _ in execution_rows]
    chunks_by_execution = await _chunks_by_execution(session, execution_ids)
    return CompressionRequestDetail(
        request_id=str(request.id),
        request_key=request.request_key,
        source_system=request.source_system,
        tenant_id=request.tenant_id,
        team_id=request.team_id,
        user_id=request.user_id,
        incoming_route=request.incoming_route,
        provider_hint=request.provider_hint,
        model_hint=request.model_hint,
        external_request_id=request.external_request_id,
        trace_id=request.trace_id,
        started_at=request.started_at,
        ended_at=request.ended_at,
        created_at=request.created_at,
        executions=[
            _execution_detail(execution, config, chunks_by_execution)
            for execution, config in execution_rows
        ],
        provider_calls=await _provider_calls(session, request.id),
        cache_activities=await cache_activities_for_request(session, request.id),
    )


async def _execution_rows(session: AsyncSession, request_id: object):
    return (
        await session.execute(
            select(CompressionExecutionModel, CompressionConfigSnapshotModel)
            .join(
                CompressionConfigSnapshotModel,
                CompressionExecutionModel.config_snapshot_id
                == CompressionConfigSnapshotModel.id,
            )
            .where(CompressionExecutionModel.request_id == request_id)
            .order_by(CompressionExecutionModel.attempt_number)
        )
    ).all()


def _execution_detail(
    execution: CompressionExecutionModel,
    config: CompressionConfigSnapshotModel,
    chunks_by_execution: dict[str, list[CompressionChunkSummary]],
) -> CompressionExecutionDetail:
    return CompressionExecutionDetail(
        execution_id=str(execution.id),
        config_snapshot_id=str(config.id),
        attempt_number=execution.attempt_number,
        is_simulated=execution.is_simulated,
        status=execution.status,
        strategy_name=config.strategy_name,
        strategy_version=config.strategy_version,
        algorithm=config.algorithm,
        target_model=config.target_model,
        started_at=execution.started_at,
        ended_at=execution.ended_at,
        duration_ms=execution.duration_ms,
        original_tokens=execution.original_tokens,
        compressed_tokens=execution.compressed_tokens,
        tokens_saved=execution.tokens_saved,
        compression_ratio=_float(execution.compression_ratio),
        error_type=execution.error_type,
        chunks=chunks_by_execution.get(str(execution.id), []),
    )


async def _chunks_by_execution(
    session: AsyncSession,
    execution_ids: Sequence[object],
) -> dict[str, list[CompressionChunkSummary]]:
    if not execution_ids:
        return {}
    chunks = (
        await session.scalars(
            select(CompressionChunkModel)
            .where(CompressionChunkModel.execution_id.in_(execution_ids))
            .order_by(CompressionChunkModel.execution_id, CompressionChunkModel.ordinal)
        )
    ).all()
    retrieval_counts = await _chunk_retrieval_counts(
        session, [chunk.id for chunk in chunks]
    )
    grouped: dict[str, list[CompressionChunkSummary]] = {}
    for chunk in chunks:
        grouped.setdefault(str(chunk.execution_id), []).append(
            CompressionChunkSummary(
                chunk_id=str(chunk.id),
                execution_id=str(chunk.execution_id),
                ordinal=chunk.ordinal,
                ccr_hash=chunk.ccr_hash,
                content_hash=chunk.content_hash,
                role=chunk.role,
                tool_name=chunk.tool_name,
                original_tokens=chunk.original_tokens,
                compressed_tokens=chunk.compressed_tokens,
                storage_policy=chunk.storage_policy,
                has_original_content=chunk.original_content is not None,
                has_compressed_content=chunk.compressed_content is not None,
                retrievals=retrieval_counts.get(str(chunk.id), 0),
                created_at=chunk.created_at,
            )
        )
    return grouped


async def _chunk_retrieval_counts(
    session: AsyncSession,
    chunk_ids: Sequence[object],
) -> dict[str, int]:
    if not chunk_ids:
        return {}
    rows = (
        await session.execute(
            select(
                ChunkRetrievalEventModel.chunk_id,
                func.count(ChunkRetrievalEventModel.id),
            )
            .where(ChunkRetrievalEventModel.chunk_id.in_(chunk_ids))
            .group_by(ChunkRetrievalEventModel.chunk_id)
        )
    ).all()
    return {str(chunk_id): int(count or 0) for chunk_id, count in rows}


async def _provider_calls(
    session: AsyncSession,
    request_id: object,
) -> list[ProviderCallSummary]:
    calls = (
        await session.scalars(
            select(ProviderCallModel)
            .where(ProviderCallModel.request_id == request_id)
            .order_by(ProviderCallModel.created_at)
        )
    ).all()
    usage_by_call = await _token_usage_by_call(session, [call.id for call in calls])
    return [
        ProviderCallSummary(
            provider_call_id=str(call.id),
            execution_id=str(call.execution_id) if call.execution_id else None,
            provider_call_key=call.provider_call_key,
            provider=call.provider,
            model=call.model,
            status=call.status,
            litellm_call_id=call.litellm_call_id,
            provider_request_id=call.provider_request_id,
            provider_response_id=call.provider_response_id,
            started_at=call.started_at,
            ended_at=call.ended_at,
            duration_ms=call.duration_ms,
            cost_total=str(call.cost_total) if call.cost_total is not None else None,
            currency=call.currency,
            error_type=call.error_type,
            has_raw_response_metadata=bool(call.raw_response_metadata),
            token_usage=usage_by_call.get(str(call.id), []),
        )
        for call in calls
    ]


async def _token_usage_by_call(
    session: AsyncSession,
    call_ids: Sequence[object],
) -> dict[str, list[TokenUsageSummary]]:
    usage_by_call = {str(call_id): [] for call_id in call_ids}
    if not call_ids:
        return usage_by_call
    usage_rows = (
        await session.scalars(
            select(TokenUsageBreakdownModel)
            .where(TokenUsageBreakdownModel.provider_call_id.in_(call_ids))
            .order_by(TokenUsageBreakdownModel.created_at)
        )
    ).all()
    for usage in usage_rows:
        usage_by_call[str(usage.provider_call_id)].append(
            TokenUsageSummary(
                measurement_source=usage.measurement_source,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                newly_processed_input_tokens=usage.newly_processed_input_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                total_tokens=usage.total_tokens,
                has_raw_usage=bool(usage.raw_usage),
            )
        )
    return usage_by_call
