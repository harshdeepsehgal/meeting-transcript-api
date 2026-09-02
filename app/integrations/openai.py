from dataclasses import dataclass

from openai import APIStatusError, AsyncOpenAI

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    """Configured client and model for Responses API calls."""

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


SUMMARY_INSTRUCTIONS = (
    "Summarize the meeting described by the transcript. Focus on the meeting itself, "
    "not the dataset's questions or answers. Return only a concise plain-text summary."
)


async def request_transcript_summary(
    provider: OpenAIProvider,
    transcript: str,
) -> str:
    """Generate a plain-text summary from the complete rendered transcript."""
    response = await provider.client.responses.create(
        model=provider.model,
        instructions=SUMMARY_INSTRUCTIONS,
        input=transcript,
        truncation="disabled",
    )
    output_text = getattr(response, "output_text", "")
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
    "is_context_limit_error",
    "request_transcript_summary",
]
