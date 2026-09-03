import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory, get_session
from app.main import create_app

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:55432/meeting_transcripts_test"
)
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session", autouse=True)
def integration_database() -> Iterator[None]:
    try:
        subprocess.run(["make", "integration-db-up"], cwd=PROJECT_ROOT, check=True)
        yield
    finally:
        subprocess.run(["make", "integration-db-down"], cwd=PROJECT_ROOT, check=True)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def clean_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    await _truncate_database(session_factory)
    yield
    await _truncate_database(session_factory)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide a session factory connected only to the integration-test database."""
    database_engine = build_engine(DEFAULT_TEST_DATABASE_URL)
    try:
        yield build_session_factory(database_engine)
    finally:
        await database_engine.dispose()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def application(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    application = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as test_client:
        yield test_client


async def _truncate_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            sa.text(
                "TRUNCATE TABLE "
                "meeting_transcript.dialog_turns, "
                "meeting_transcript.transcript_summaries, "
                "meeting_transcript.dialogs, "
                "meeting_transcript.transcript_segments, "
                "meeting_transcript.transcripts"
            )
        )
