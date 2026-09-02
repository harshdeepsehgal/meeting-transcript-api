from dataclasses import dataclass

from openai import AsyncOpenAI

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    """Configured client and model for future Responses API calls."""

    client: AsyncOpenAI
    model: str


def build_openai_provider(settings: Settings | None = None) -> OpenAIProvider:
    """Build an OpenAI provider without making a network request."""
    resolved = settings or get_settings()
    if resolved.openai_api_key is None or not resolved.openai_api_key.get_secret_value():
        raise RuntimeError("OPENAI_API_KEY is required before using the OpenAI integration")

    client = AsyncOpenAI(
        api_key=resolved.openai_api_key.get_secret_value(),
        timeout=resolved.openai_timeout_seconds,
        max_retries=resolved.openai_max_retries,
    )
    return OpenAIProvider(client=client, model=resolved.openai_model)
