from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def build_engine(database_url: str) -> AsyncEngine:
    """Create an async Psycopg engine without opening a database connection."""
    return create_async_engine(database_url, pool_pre_ping=True)


engine = build_engine(get_settings().database_url)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a transaction-capable session for future request dependencies."""
    async with SessionFactory() as session:
        yield session


async def dispose_engine() -> None:
    """Release pooled connections during application shutdown."""
    await engine.dispose()
