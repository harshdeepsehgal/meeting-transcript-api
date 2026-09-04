import json
import logging
from collections.abc import Sequence
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


SUMMARY_INSTRUCTIONS = """
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

DIALOG_RESPONSES_INSTRUCTIONS = (
    "Answer every supplied query using only the meeting transcript. Preserve each supplied "
    "position and query exactly. If the transcript does not contain an answer, say that the "
    "information is not available in the transcript."
)

DIALOG_RESPONSES_FORMAT = {
    "type": "json_schema",
    "name": "dialog_responses",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "responses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "query": {"type": "string"},
                        "response": {"type": "string"},
                    },
                    "required": ["position", "query", "response"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["responses"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True, slots=True)
class GeneratedDialogResponse:
    """One validated answer returned by the OpenAI Responses API."""

    position: int
    query: str
    response: str


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


async def request_dialog_responses(
    provider: OpenAIProvider,
    transcript: str,
    queries: Sequence[tuple[int, str]],
) -> list[GeneratedDialogResponse]:
    """Answer all dialog queries in one schema-constrained Responses API call."""
    request_input = json.dumps(
        {
            "transcript": transcript,
            "queries": [{"position": position, "query": query} for position, query in queries],
        },
        ensure_ascii=False,
    )
    logger.info(
        "Requesting OpenAI dialog responses: model=%s transcript_chars=%d queries=%d",
        provider.model,
        len(transcript),
        len(queries),
    )
    response = await provider.client.responses.create(
        model=provider.model,
        reasoning={"effort": "none"},
        instructions=DIALOG_RESPONSES_INSTRUCTIONS,
        input=request_input,
        text={"format": DIALOG_RESPONSES_FORMAT},
        truncation="disabled",
    )
    output_text = getattr(response, "output_text", "")
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("OpenAI returned empty dialog response output")

    generated = _parse_dialog_responses(output_text, queries)
    logger.info(
        "Received OpenAI dialog responses: model=%s responses=%d",
        provider.model,
        len(generated),
    )
    return generated


def _parse_dialog_responses(
    output_text: str,
    queries: Sequence[tuple[int, str]],
) -> list[GeneratedDialogResponse]:
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI returned invalid dialog response JSON") from exc

    if not isinstance(payload, dict) or set(payload) != {"responses"}:
        raise ValueError("OpenAI returned an invalid dialog response object")
    response_items = payload["responses"]
    if not isinstance(response_items, list) or len(response_items) != len(queries):
        raise ValueError("OpenAI returned an unexpected number of dialog responses")

    expected_queries = dict(queries)
    if len(expected_queries) != len(queries):
        raise ValueError("Dialog query positions must be unique")

    generated_by_position: dict[int, GeneratedDialogResponse] = {}
    for item in response_items:
        if not isinstance(item, dict) or set(item) != {"position", "query", "response"}:
            raise ValueError("OpenAI returned an invalid dialog response item")

        position = item["position"]
        query = item["query"]
        generated_response = item["response"]
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or position not in expected_queries
        ):
            raise ValueError("OpenAI returned an unknown dialog position")
        if position in generated_by_position:
            raise ValueError("OpenAI returned a duplicate dialog position")
        if not isinstance(query, str) or query != expected_queries[position]:
            raise ValueError("OpenAI returned a mismatched dialog query")
        if not isinstance(generated_response, str) or not generated_response.strip():
            raise ValueError("OpenAI returned an empty generated response")

        generated_by_position[position] = GeneratedDialogResponse(
            position=position,
            query=query,
            response=generated_response.strip(),
        )

    return [generated_by_position[position] for position, _ in queries]


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
    "DIALOG_RESPONSES_FORMAT",
    "DIALOG_RESPONSES_INSTRUCTIONS",
    "GeneratedDialogResponse",
    "OpenAIProvider",
    "SUMMARY_INSTRUCTIONS",
    "build_openai_provider",
    "get_openai_provider",
    "is_context_limit_error",
    "request_dialog_responses",
    "request_transcript_summary",
]
