from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import build_engine, get_session


async def test_build_engine_does_not_require_a_live_database() -> None:
    engine = build_engine("postgresql+psycopg://postgres:postgres@localhost/test")

    assert isinstance(engine, AsyncEngine)
    assert engine.dialect.driver == "psycopg"
    await engine.dispose()


async def test_session_dependency_yields_an_async_session() -> None:
    dependency = get_session()
    session = await anext(dependency)

    assert isinstance(session, AsyncSession)

    await dependency.aclose()
