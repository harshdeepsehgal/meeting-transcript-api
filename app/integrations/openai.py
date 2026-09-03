import logging
from dataclasses import dataclass

from fastapi import Request
from openai import APIStatusError, AsyncOpenAI

from app.core.config import Settings

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    """Configured client and model for Responses API calls."""

    client: AsyncOpenAI
    model: str

    async def close(self) -> None:
        """Close the underlying asynchronous HTTP client."""
        await self.client.close()


def build_openai_provider(settings: Settings) -> OpenAIProvider:
    """Build an OpenAI provider without making a network request."""
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        logger.warning("OpenAI provider requested without a configured API key")
        raise RuntimeError("OPENAI_API_KEY is required before using the OpenAI integration")

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    logger.info(
        "OpenAI provider configured: model=%s timeout_seconds=%s max_retries=%s",
        settings.openai_model,
        settings.openai_timeout_seconds,
        settings.openai_max_retries,
    )
    return OpenAIProvider(client=client, model=settings.openai_model)


def get_openai_provider(request: Request) -> OpenAIProvider | None:
    """Return the lifespan-scoped provider, when OpenAI is configured."""
    return request.state.openai_provider


SUMMARY_INSTRUCTIONS = (
    """
    Summarize this meeting. Return only a concise plain-text summary.
    It should contain the following:
    1. Executive summary
    2. Key discussion points
    3. Decisions made
    4. Action items — owner, task, deadline
    5. Open questions
    6. Risks/blockers
    7. Important dates and numbers

    Do not invent owners, deadlines, or decisions that were not explicitly stated.
    """
)


async def request_transcript_summary(
    provider: OpenAIProvider,
    transcript: str,
) -> str:
    """Generate a plain-text summary from the complete rendered transcript."""
    logger.info(
        "Requesting OpenAI transcript summary: model=%s transcript_chars=%d",
        provider.model,
        len(transcript),
    )
    response = await provider.client.responses.create(
        model=provider.model,
        reasoning={"effort": "none"},
        instructions=SUMMARY_INSTRUCTIONS,
        input=transcript,
        truncation="disabled",
    )
    output_text = getattr(response, "output_text", "")
    logger.info(
        "Received OpenAI transcript summary: model=%s output_chars=%d",
        provider.model,
        len(output_text) if isinstance(output_text, str) else 0,
    )
    summary = output_text.strip() if isinstance(output_text, str) else ""
    if not summary:
        raise ValueError("OpenAI returned empty summary output")
    return summary


def is_context_limit_error(error: APIStatusError) -> bool:
    """Identify provider errors caused by an input that exceeds model context."""
    if error.status_code != 400:
        return False

    details = [str(error), str(error.code or "")]
    body = error.body
    if isinstance(body, dict):
        error_body = body.get("error")
        if isinstance(error_body, dict):
            details.extend(str(error_body.get(key, "")) for key in ("code", "type", "message"))

    normalized = " ".join(details).lower()
    return any(
        marker in normalized
        for marker in (
            "context_length",
            "context length",
            "maximum context",
            "token limit",
            "too many tokens",
        )
    )


__all__ = [
    "OpenAIProvider",
    "SUMMARY_INSTRUCTIONS",
    "build_openai_provider",
    "get_openai_provider",
    "is_context_limit_error",
    "request_transcript_summary",
]
