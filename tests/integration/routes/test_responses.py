from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from openai import BadRequestError, OpenAIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment
from app.integrations.openai import GeneratedDialogResponse, get_openai_provider
from app.services import responses as response_service


@pytest.fixture
def provider(application: FastAPI) -> SimpleNamespace:
    configured_provider = SimpleNamespace(model="test-model")
    application.dependency_overrides[get_openai_provider] = lambda: configured_provider
    return configured_provider


async def test_generates_all_responses_in_one_request_and_persists_them(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    provider: SimpleNamespace,
) -> None:
    await _seed_dialog(db_session)
    calls: list[tuple[object, str, list[tuple[int, str]]]] = []

    async def fake_request(
        request_provider: object,
        transcript: str,
        queries: list[tuple[int, str]],
    ) -> list[GeneratedDialogResponse]:
        calls.append((request_provider, transcript, queries))
        return [
            GeneratedDialogResponse(position=0, query="First question?", response="Generated one."),
            GeneratedDialogResponse(
                position=1,
                query="Second question?",
                response="Generated two.",
            ),
        ]

    monkeypatch.setattr(response_service, "request_dialog_responses", fake_request)

    response = await client.post("/dialogs/response-dialog/responses")

    assert response.status_code == 200
    assert response.json() == [
        {
            "query": "First question?",
            "storedResponse": "Stored one.",
            "generatedResponse": "Generated one.",
            "error": None,
        },
        {
            "query": "Second question?",
            "storedResponse": "Stored two.",
            "generatedResponse": "Generated two.",
            "error": None,
        },
    ]
    assert calls == [
        (
            provider,
            "Speaker A: First segment.\nSecond segment.",
            [(0, "First question?"), (1, "Second question?")],
        )
    ]
    persisted = await db_session.execute(
        sa.select(DialogTurn.position, DialogTurn.generated_response)
        .where(DialogTurn.dialog_id == "response-dialog")
        .order_by(DialogTurn.position)
    )
    assert list(persisted) == [(0, "Generated one."), (1, "Generated two.")]


@pytest.mark.parametrize(
    "failure",
    [OpenAIError("provider unavailable"), ValueError("invalid structured output")],
)
async def test_generation_failure_returns_item_errors_and_preserves_previous_answers(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    provider: SimpleNamespace,
    failure: Exception,
) -> None:
    await _seed_dialog(db_session, generated_response="Previous answer.")

    async def fail_request(*_: object) -> list[GeneratedDialogResponse]:
        raise failure

    monkeypatch.setattr(response_service, "request_dialog_responses", fail_request)

    response = await client.post("/dialogs/response-dialog/responses")

    assert response.status_code == 200
    assert response.json() == [
        {
            "query": query,
            "storedResponse": stored,
            "generatedResponse": None,
            "error": {"code": "provider_failed", "message": "Response provider failed"},
        }
        for query, stored in [
            ("First question?", "Stored one."),
            ("Second question?", "Stored two."),
        ]
    ]
    persisted = await db_session.scalars(
        sa.select(DialogTurn.generated_response).where(DialogTurn.dialog_id == "response-dialog")
    )
    assert list(persisted) == ["Previous answer.", "Previous answer."]


async def test_missing_provider_returns_configuration_errors(
    client: httpx.AsyncClient,
    application: FastAPI,
    db_session: AsyncSession,
) -> None:
    await _seed_dialog(db_session)
    application.dependency_overrides[get_openai_provider] = lambda: None

    response = await client.post("/dialogs/response-dialog/responses")

    assert response.status_code == 200
    assert [item["error"] for item in response.json()] == [
        {
            "code": "openai_not_configured",
            "message": "OpenAI API key is not configured",
        },
        {
            "code": "openai_not_configured",
            "message": "OpenAI API key is not configured",
        },
    ]


async def test_context_limit_returns_item_errors(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    provider: SimpleNamespace,
) -> None:
    await _seed_dialog(db_session)
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = BadRequestError(
        "context length exceeded",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "context_length_exceeded"}},
    )

    async def fail_request(*_: object) -> list[GeneratedDialogResponse]:
        raise error

    monkeypatch.setattr(response_service, "request_dialog_responses", fail_request)

    response = await client.post("/dialogs/response-dialog/responses")

    assert response.status_code == 200
    assert {item["error"]["code"] for item in response.json()} == {"context_limit_exceeded"}


async def test_unknown_dialog_returns_404_without_provider_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    provider: SimpleNamespace,
) -> None:
    request_called = False

    async def fake_request(*_: object) -> list[GeneratedDialogResponse]:
        nonlocal request_called
        request_called = True
        return []

    monkeypatch.setattr(response_service, "request_dialog_responses", fake_request)

    response = await client.post("/dialogs/unknown-dialog/responses")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dialog not found"}
    assert request_called is False


async def _seed_dialog(
    session: AsyncSession,
    *,
    generated_response: str | None = None,
) -> None:
    async with session.begin():
        session.add(Transcript(meeting_id="response-meeting"))
        await session.flush()
        session.add(Dialog(dialog_id="response-dialog", meeting_id="response-meeting"))
        await session.flush()
        session.add_all(
            [
                TranscriptSegment(
                    meeting_id="response-meeting",
                    position=1,
                    speaker=None,
                    text="Second segment.",
                ),
                TranscriptSegment(
                    meeting_id="response-meeting",
                    position=0,
                    speaker="Speaker A",
                    text="First segment.",
                ),
                DialogTurn(
                    dialog_id="response-dialog",
                    position=1,
                    query="Second question?",
                    query_metadata={},
                    response="Stored two.",
                    generated_response=generated_response,
                    attributions=[],
                    references=[],
                ),
                DialogTurn(
                    dialog_id="response-dialog",
                    position=0,
                    query="First question?",
                    query_metadata={},
                    response="Stored one.",
                    generated_response=generated_response,
                    attributions=[],
                    references=[],
                ),
            ]
        )
