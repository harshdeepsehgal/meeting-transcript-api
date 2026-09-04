import json
import logging
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
        build_openai_provider(Settings.model_construct(openai_api_key=None))


def test_openai_provider_uses_configured_model() -> None:
    settings = Settings.model_construct(
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
        [(0, "Speaker A", "Meeting transcript."), (1, None, "Follow-up point.")],
        [(0, "First?"), (1, "Second?")],
    )

    assert generated == [
        GeneratedDialogResponse(position=0, query="First?", response="First answer."),
        GeneratedDialogResponse(position=1, query="Second?", response="Second answer."),
    ]
    call = client.responses.create.await_args
    assert call.kwargs["model"] == "test-model"
    assert call.kwargs["reasoning"] == {"effort": "medium"}
    assert call.kwargs["instructions"] == DIALOG_RESPONSES_INSTRUCTIONS
    assert json.loads(call.kwargs["input"]) == {
        "transcript": [
            {"position": 0, "speaker": "Speaker A", "text": "Meeting transcript."},
            {"position": 1, "speaker": None, "text": "Follow-up point."},
        ],
        "queries": [
            {"position": 0, "query": "First?"},
            {"position": 1, "query": "Second?"},
        ],
    }
    assert call.kwargs["text"] == {"format": DIALOG_RESPONSES_FORMAT}
    assert call.kwargs["truncation"] == "disabled"


async def test_request_dialog_responses_orders_and_allows_overlapping_attribution_ranges() -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(
        id="resp_test",
        status="completed",
        output_text=json.dumps(
            {
                "responses": [
                    {
                        "position": 0,
                        "query": "First?",
                        "response": "First answer.",
                        "attributions": {
                            "indexRanges": [
                                {"startIndex": 2, "endIndex": 2},
                                {"startIndex": 0, "endIndex": 1},
                                {"startIndex": 1, "endIndex": 2},
                            ]
                        },
                    },
                    {
                        "position": 1,
                        "query": "Second?",
                        "response": "Second answer.",
                        "attributions": {"indexRanges": [{"startIndex": 1, "endIndex": 1}]},
                    },
                ]
            }
        ),
    )
    provider = OpenAIProvider(client=client, model="test-model")

    generated = await request_dialog_responses(
        provider,
        [(0, None, "First."), (1, None, "Second."), (2, None, "Third.")],
        [(0, "First?"), (1, "Second?")],
    )

    assert generated[0].attributions == {
        "indexRanges": [
            {"startIndex": 0, "endIndex": 1},
            {"startIndex": 1, "endIndex": 2},
            {"startIndex": 2, "endIndex": 2},
        ]
    }
    assert generated[1].attributions == {"indexRanges": [{"startIndex": 1, "endIndex": 1}]}


async def test_request_dialog_responses_uses_position_as_the_canonical_mapping() -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(
            {
                "responses": [
                    {
                        "position": 0,
                        "query": "Model changed this text",
                        "response": "Answer.",
                        "attributions": {"indexRanges": [{"startIndex": 4, "endIndex": 4}]},
                        "ignored": "strict output normally prevents this",
                    }
                ],
                "ignored": True,
            }
        )
    )
    provider = OpenAIProvider(client=client, model="test-model")

    generated = await request_dialog_responses(
        provider,
        [(0, None, "Transcript.")],
        [(0, "Original question?")],
    )

    assert generated == [
        GeneratedDialogResponse(
            position=0,
            query="Original question?",
            response="Answer.",
            attributions={"indexRanges": [{"startIndex": 4, "endIndex": 4}]},
        )
    ]


async def test_request_dialog_responses_logs_safe_validation_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(
        id="resp_invalid",
        status="completed",
        output_text=json.dumps(
            {
                "responses": [
                    {
                        "position": 0,
                        "query": "Sensitive question?",
                        "response": "Sensitive answer.",
                        "attributions": {"indexRanges": [{"startIndex": 4, "endIndex": 0}]},
                    }
                ]
            }
        ),
    )
    provider = OpenAIProvider(client=client, model="test-model")

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(
            ValueError,
            match="invalid response attribution indexes",
        ),
    ):
        await request_dialog_responses(
            provider,
            [(0, None, "Sensitive transcript.")],
            [(0, "Sensitive question?")],
        )

    assert "response_id='resp_invalid'" in caplog.text
    assert "start_index=4 end_index=0" in caplog.text
    assert "output_sha256=" in caplog.text
    assert "Sensitive question" not in caplog.text
    assert "Sensitive answer" not in caplog.text


@pytest.mark.parametrize(
    "payload, queries, message",
    [
        ("not-json", [(0, "Question?")], "invalid dialog response JSON"),
        ("[]", [(0, "Question?")], "invalid dialog response object"),
        ("{}", [(0, "Question?")], "unexpected number"),
        ('{"responses": {}}', [(0, "Question?")], "unexpected number"),
        ('{"responses": []}', [(0, "Question?")], "unexpected number"),
        ('{"responses": [null]}', [(0, "Question?")], "invalid dialog response item"),
        (
            '{"responses": [{"position": 1, "query": "Question?", "response": "Answer"}]}',
            [(0, "Question?")],
            "unknown dialog position",
        ),
        (
            '{"responses": [{"position": true, "query": "Question?", "response": "Answer"}]}',
            [(0, "Question?")],
            "unknown dialog position",
        ),
        (
            '{"responses": [{"position": 0, "query": "Question?", "response": "  "}]}',
            [(0, "Question?")],
            "empty generated response",
        ),
        (
            '{"responses": [{"position": 0, "response": "Answer", "attributions": []}]}',
            [(0, "Question?")],
            "invalid response attributions",
        ),
        (
            '{"responses": [{"position": 0, "response": "Answer", '
            '"attributions": {"indexRanges": null}}]}',
            [(0, "Question?")],
            "invalid response attribution ranges",
        ),
        (
            '{"responses": [{"position": 0, "response": "Answer", '
            '"attributions": {"indexRanges": [null]}}]}',
            [(0, "Question?")],
            "invalid response attribution range",
        ),
        (
            '{"responses": [{"position": 0, "response": "Answer", '
            '"attributions": {"indexRanges": [{"startIndex": 0}]}}]}',
            [(0, "Question?")],
            "invalid response attribution indexes",
        ),
        (
            '{"responses": [{"position": 0, "response": "Answer", '
            '"attributions": {"indexRanges": [{"startIndex": -1, "endIndex": 0}]}}]}',
            [(0, "Question?")],
            "invalid response attribution indexes",
        ),
        (
            '{"responses": [{"position": 0, "response": "One"}, '
            '{"position": 0, "response": "Two"}]}',
            [(0, "First?"), (1, "Second?")],
            "duplicate dialog position",
        ),
        (
            '{"responses": [{"position": 0, "response": "One"}, '
            '{"position": 1, "response": "Two"}]}',
            [(0, "First?"), (0, "Second?")],
            "query positions must be unique",
        ),
    ],
)
async def test_request_dialog_responses_rejects_invalid_batches(
    payload: str,
    queries: list[tuple[int, str]],
    message: str,
) -> None:
    client = AsyncMock()
    client.responses.create.return_value = SimpleNamespace(output_text=payload)
    provider = OpenAIProvider(client=client, model="test-model")

    with pytest.raises(ValueError, match=message):
        await request_dialog_responses(provider, [(0, None, "Transcript")], queries)


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
