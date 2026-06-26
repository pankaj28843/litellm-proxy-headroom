from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..postgres.session import session_scope


def session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.analytics_session_factory


async def get_session(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    async with session_scope(session_factory(request)) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
