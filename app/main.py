import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.db.session import dispose_engine
from app.routes.dialogs import router as dialogs_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own application resources without connecting to dependencies at import time."""
    logger.info("Application startup")
    yield
    await dispose_engine()
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
