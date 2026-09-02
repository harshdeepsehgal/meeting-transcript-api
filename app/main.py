from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.db.session import dispose_engine
from app.routes.dialogs import router as dialogs_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own application resources without connecting to dependencies at import time."""
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    """Build the API application."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="MISeD meeting transcript service.",
        lifespan=lifespan,
    )
    application.include_router(dialogs_router)
    return application


app = create_app()
