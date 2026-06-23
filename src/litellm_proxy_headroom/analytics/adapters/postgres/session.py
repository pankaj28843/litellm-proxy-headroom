from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_ANALYTICS_DATABASE_URL = (
    "postgresql+asyncpg://analytics:analytics@127.0.0.1:55432/analytics"
)


def analytics_database_url() -> str:
    return os.getenv("ANALYTICS_DATABASE_URL", DEFAULT_ANALYTICS_DATABASE_URL)


def create_analytics_engine(
    database_url: str | None = None,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: float = 5.0,
) -> AsyncEngine:
    return create_async_engine(
        database_url or analytics_database_url(),
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session, session.begin():
        yield session
