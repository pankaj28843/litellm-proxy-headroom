from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from ...application.read_models import CompressionRecordPage, CompressionRequestDetail
from ..postgres.read_queries import (
    get_compression_record_detail,
    list_compression_records,
)
from .deps import SessionDep
from .query_params import AnalyticsFiltersDep

router = APIRouter(prefix="/records", tags=["records"])


@router.get("/compression", response_model=CompressionRecordPage)
async def compression_records(
    session: SessionDep,
    filters: AnalyticsFiltersDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompressionRecordPage:
    return await list_compression_records(
        session,
        filters,
        limit=limit,
        offset=offset,
    )


@router.get("/compression/{request_key}", response_model=CompressionRequestDetail)
async def compression_record_detail(
    request_key: str,
    session: SessionDep,
) -> CompressionRequestDetail:
    detail = await get_compression_record_detail(session, request_key)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="compression request not found",
        )
    return detail
