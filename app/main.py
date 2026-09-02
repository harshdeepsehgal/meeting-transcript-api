from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own application resources without connecting to dependencies at import time."""
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    """Build the API application.

    Business routes are intentionally deferred until the domain contract is implemented.
    """
    settings = get_settings()
    return FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Infrastructure scaffold for the MISeD meeting transcript service.",
        lifespan=lifespan,
    )


app = create_app()
