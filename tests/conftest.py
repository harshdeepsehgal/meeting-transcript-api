from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    """Keep environment-dependent settings isolated between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide a test-owned session factory and dispose its engine afterward."""
    database_engine = build_engine(get_settings().database_url)
    try:
        yield build_session_factory(database_engine)
    finally:
        await database_engine.dispose()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Provide one caller-owned database session."""
    async with session_factory() as session:
        yield session
