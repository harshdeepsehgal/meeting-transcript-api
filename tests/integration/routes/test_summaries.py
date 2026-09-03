from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from openai import BadRequestError, OpenAIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, Transcript, TranscriptSegment, TranscriptSummary
from app.integrations.openai import get_openai_provider
from app.services import summarization


@pytest.fixture(autouse=True)
def provider(application: FastAPI) -> SimpleNamespace:
    provider = SimpleNamespace(model="test-model")
    application.dependency_overrides[get_openai_provider] = lambda: provider
    return provider


async def test_summary_cache_hit_does_not_construct_provider(
    client: httpx.AsyncClient,
    application: FastAPI,
    db_session: AsyncSession,
) -> None:
    await _seed_summary_dialog(db_session, summary="Cached meeting summary.")
    application.dependency_overrides[get_openai_provider] = lambda: None

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "Cached meeting summary."}


async def test_summary_cache_miss_sends_full_rendered_transcript_and_caches_result(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(db_session)
    calls: list[tuple[object, str]] = []

    async def fake_request(provider: object, transcript: str) -> str:
        calls.append((provider, transcript))
        return "Generated meeting summary."

    monkeypatch.setattr(summarization, "request_transcript_summary", fake_request)

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "Generated meeting summary."}
    assert len(calls) == 1
    assert calls[0][1] == "Speaker A: First meeting point.\nSecond meeting point."
    assert (
        await db_session.scalar(
            sa.select(TranscriptSummary.summary).where(
                TranscriptSummary.meeting_id == "summary-meeting-shared"
            )
        )
        == "Generated meeting summary."
    )


async def test_dialogs_sharing_a_meeting_share_one_summary_cache(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(db_session)
    request_count = 0

    async def fake_request(_: object, __: str) -> str:
        nonlocal request_count
        request_count += 1
        return "Shared meeting summary."

    monkeypatch.setattr(summarization, "request_transcript_summary", fake_request)

    first = await client.post("/dialogs/summary-dialog-one/summary")
    second = await client.post("/dialogs/summary-dialog-two/summary")

    assert first.json() == {"summary": "Shared meeting summary."}
    assert second.json() == {"summary": "Shared meeting summary."}
    assert request_count == 1


async def test_refresh_replaces_cache_and_failed_refresh_preserves_previous_value(
    client: httpx.AsyncClient,
    provider: SimpleNamespace,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(db_session, summary="Original summary.")
    responses = iter(("Refreshed summary.", ValueError("empty summary output")))
    providers: list[object] = []

    async def fake_request(provider: object, __: str) -> str:
        providers.append(provider)
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(summarization, "request_transcript_summary", fake_request)

    refreshed = await client.post(
        "/dialogs/summary-dialog-one/summary",
        params={"refresh": "true"},
    )
    failed = await client.post(
        "/dialogs/summary-dialog-one/summary",
        params={"refresh": "true"},
    )

    assert refreshed.status_code == 200
    assert refreshed.json() == {"summary": "Refreshed summary."}
    assert failed.status_code == 502
    assert providers == [provider, provider]
    assert (
        await db_session.scalar(
            sa.select(TranscriptSummary.summary).where(
                TranscriptSummary.meeting_id == "summary-meeting-shared"
            )
        )
        == "Refreshed summary."
    )


async def test_model_configuration_does_not_change_cache_identity(
    client: httpx.AsyncClient,
    application: FastAPI,
    db_session: AsyncSession,
) -> None:
    await _seed_summary_dialog(db_session, summary="Model-independent summary.")
    application.dependency_overrides[get_openai_provider] = lambda: None

    response = await client.post("/dialogs/summary-dialog-two/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "Model-independent summary."}


async def test_missing_key_returns_503(
    client: httpx.AsyncClient,
    application: FastAPI,
    db_session: AsyncSession,
) -> None:
    await _seed_summary_dialog(db_session)
    application.dependency_overrides[get_openai_provider] = lambda: None

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 503
    assert response.json() == {"detail": "OpenAI API key is not configured"}


async def test_context_limit_failure_returns_422(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(db_session)
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = BadRequestError(
        "context length exceeded",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "context_length_exceeded"}},
    )

    async def fail_request(_: object, __: str) -> str:
        raise error

    monkeypatch.setattr(summarization, "request_transcript_summary", fail_request)

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 422
    assert response.json() == {"detail": "Transcript exceeds model context limit"}


@pytest.mark.parametrize("failure", [OpenAIError("provider unavailable"), ValueError("empty")])
async def test_other_provider_failures_return_502(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    await _seed_summary_dialog(db_session)

    async def fail_request(_: object, __: str) -> str:
        raise failure

    monkeypatch.setattr(summarization, "request_transcript_summary", fail_request)

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 502
    assert response.json() == {"detail": "Summary provider failed"}


async def test_unknown_dialog_returns_404_without_provider_call(
    client: httpx.AsyncClient,
    application: FastAPI,
) -> None:
    application.dependency_overrides[get_openai_provider] = lambda: None

    response = await client.post("/dialogs/unknown-dialog/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dialog not found"}


async def test_invalid_refresh_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/dialogs/summary-dialog-one/summary",
        params={"refresh": "not-a-boolean"},
    )

    assert response.status_code == 422


async def _seed_summary_dialog(
    session: AsyncSession,
    *,
    summary: str | None = None,
) -> None:
    async with session.begin():
        session.add(Transcript(meeting_id="summary-meeting-shared"))
        await session.flush()
        session.add_all(
            [
                Dialog(
                    dialog_id="summary-dialog-one",
                    meeting_id="summary-meeting-shared",
                ),
                Dialog(
                    dialog_id="summary-dialog-two",
                    meeting_id="summary-meeting-shared",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                TranscriptSegment(
                    meeting_id="summary-meeting-shared",
                    position=0,
                    speaker="Speaker A",
                    text="First meeting point.",
                ),
                TranscriptSegment(
                    meeting_id="summary-meeting-shared",
                    position=1,
                    speaker=None,
                    text="Second meeting point.",
                ),
            ]
        )
        if summary is not None:
            session.add(
                TranscriptSummary(
                    meeting_id="summary-meeting-shared",
                    summary=summary,
                )
            )
