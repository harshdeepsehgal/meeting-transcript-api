from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(database_url: str) -> AsyncEngine:
    """Create an async Psycopg engine without opening a database connection."""
    return create_async_engine(database_url, pool_pre_ping=True)


def build_session_factory(
    database_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create a session factory for an application-owned engine."""
    return async_sessionmaker(database_engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a transaction-capable session from lifespan state."""
    db_session_factory: async_sessionmaker[AsyncSession] = request.state.db_session_factory
    async with db_session_factory() as session:
        yield session
