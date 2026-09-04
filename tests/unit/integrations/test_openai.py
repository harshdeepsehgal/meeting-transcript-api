import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request
from openai import BadRequestError
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.openai import (
    DIALOG_RESPONSES_FORMAT,
    DIALOG_RESPONSES_INSTRUCTIONS,
    SUMMARY_INSTRUCTIONS,
    GeneratedDialogResponse,
    OpenAIProvider,
    build_openai_provider,
    get_openai_provider,
    is_context_limit_error,
    request_dialog_responses,
    request_transcript_summary,
)


def test_get_openai_provider_reads_lifespan_state() -> None:
    provider = SimpleNamespace(model="test-model")
    request = Request(
        {
            "type": "http",
            "state": {"openai_provider": provider},
        }
    )

    assert get_openai_provider(request) is provider


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


async def test_openai_provider_closes_client() -> None:
    client = AsyncMock()
    provider = OpenAIProvider(client=client, model="test-model")

    await provider.close()

    client.close.assert_awaited_once_with()


async def test_request_transcript_summary_uses_complete_plain_text_input() -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(output_text="  Meeting summary.  ")
    provider = OpenAIProvider(client=client, model="test-model")

    summary = await request_transcript_summary(provider, "Speaker A: Meeting transcript.")

    assert summary == "Meeting summary."
    client.responses.create.assert_awaited_once_with(
        model="test-model",
        reasoning={"effort": "none"},
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


async def test_request_dialog_responses_batches_queries_as_strict_json() -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(
            {
                "responses": [
                    {"position": 1, "query": "Second?", "response": " Second answer. "},
                    {"position": 0, "query": "First?", "response": "First answer."},
                ]
            }
        )
    )
    provider = OpenAIProvider(client=client, model="test-model")

    generated = await request_dialog_responses(
        provider,
        "Speaker A: Meeting transcript.",
        [(0, "First?"), (1, "Second?")],
    )

    assert generated == [
        GeneratedDialogResponse(position=0, query="First?", response="First answer."),
        GeneratedDialogResponse(position=1, query="Second?", response="Second answer."),
    ]
    call = client.responses.create.await_args
    assert call.kwargs["model"] == "test-model"
    assert call.kwargs["reasoning"] == {"effort": "none"}
    assert call.kwargs["instructions"] == DIALOG_RESPONSES_INSTRUCTIONS
    assert json.loads(call.kwargs["input"]) == {
        "transcript": "Speaker A: Meeting transcript.",
        "queries": [
            {"position": 0, "query": "First?"},
            {"position": 1, "query": "Second?"},
        ],
    }
    assert call.kwargs["text"] == {"format": DIALOG_RESPONSES_FORMAT}
    assert call.kwargs["truncation"] == "disabled"


@pytest.mark.parametrize(
    "payload, message",
    [
        ("not-json", "invalid dialog response JSON"),
        ('{"responses": []}', "unexpected number"),
        (
            '{"responses": [{"position": 1, "query": "Question?", "response": "Answer"}]}',
            "unknown dialog position",
        ),
        (
            '{"responses": [{"position": 0, "query": "Changed?", "response": "Answer"}]}',
            "mismatched dialog query",
        ),
        (
            '{"responses": [{"position": 0, "query": "Question?", "response": "  "}]}',
            "empty generated response",
        ),
    ],
)
async def test_request_dialog_responses_rejects_invalid_batches(
    payload: str,
    message: str,
) -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(output_text=payload)
    provider = OpenAIProvider(client=client, model="test-model")

    with pytest.raises(ValueError, match=message):
        await request_dialog_responses(provider, "Transcript", [(0, "Question?")])


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
