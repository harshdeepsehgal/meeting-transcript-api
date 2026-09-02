from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import BadRequestError
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.openai import (
    SUMMARY_INSTRUCTIONS,
    OpenAIProvider,
    build_openai_provider,
    is_context_limit_error,
    request_transcript_summary,
)


def test_openai_provider_requires_an_api_key() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_openai_provider(Settings(_env_file=None, openai_api_key=None))


def test_openai_provider_uses_configured_model() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr("test-key"),
        openai_model="test-model",
    )

    provider = build_openai_provider(settings)

    assert provider.model == "test-model"


async def test_request_transcript_summary_uses_complete_plain_text_input() -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(output_text="  Meeting summary.  ")
    provider = OpenAIProvider(client=client, model="test-model")

    summary = await request_transcript_summary(provider, "Speaker A: Meeting transcript.")

    assert summary == "Meeting summary."
    client.responses.create.assert_awaited_once_with(
        model="test-model",
        instructions=SUMMARY_INSTRUCTIONS,
        input="Speaker A: Meeting transcript.",
        truncation="disabled",
    )


@pytest.mark.parametrize("output_text", ["  ", None])
async def test_request_transcript_summary_rejects_empty_output(output_text: object) -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(output_text=output_text)
    provider = OpenAIProvider(client=client, model="test-model")

    with pytest.raises(ValueError, match="empty summary output"):
        await request_transcript_summary(provider, "Transcript")


def test_context_limit_error_classifier_only_matches_context_failures() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    context_error = BadRequestError(
        "context length exceeded",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "context_length_exceeded"}},
    )
    generic_error = BadRequestError(
        "invalid request",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "invalid_request_error"}},
    )

    assert is_context_limit_error(context_error)
    assert not is_context_limit_error(generic_error)
