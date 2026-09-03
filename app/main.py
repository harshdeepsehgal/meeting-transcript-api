import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TypedDict

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.integrations.openai import OpenAIProvider, build_openai_provider
from app.routes.dialogs import router as dialogs_router

logger = logging.getLogger(__name__)


class LifespanState(TypedDict):
    """Shared resources copied into each request state."""

    db_session_factory: async_sessionmaker[AsyncSession]
    openai_provider: OpenAIProvider | None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[LifespanState]:
    """Own application resources without connecting to dependencies at import time."""
    logger.info("Application startup")
    try:
        async with AsyncExitStack() as stack:
            settings = get_settings()
            database_engine = build_engine(settings.database_url)
            stack.push_async_callback(database_engine.dispose)
            db_session_factory = build_session_factory(database_engine)

            provider = None
            if settings.openai_api_key is not None and settings.openai_api_key.get_secret_value():
                provider = build_openai_provider(settings)
                stack.push_async_callback(provider.close)

            yield {
                "db_session_factory": db_session_factory,
                "openai_provider": provider,
            }
    finally:
        logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Build the API application."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(levelname)s:     %(asctime)s - %(name)s - %(message)s",
    )
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="MISeD meeting transcript service.",
        lifespan=lifespan,
    )
    application.include_router(dialogs_router)
    logger.info("Application configured: environment=%s", settings.app_env)
    return application


app = create_app()
