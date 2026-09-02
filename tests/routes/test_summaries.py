from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from openai import BadRequestError, OpenAIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment, TranscriptSummary
from app.db.session import SessionFactory, get_session
from app.main import create_app
from app.services import summarization

SUMMARY_DIALOG_IDS = ("summary-dialog-one", "summary-dialog-two")
SUMMARY_MEETING_IDS = ("summary-meeting-shared",)


@pytest.fixture(autouse=True)
async def clean_summary_rows() -> AsyncIterator[None]:
    await _delete_summary_rows()
    yield
    await _delete_summary_rows()


@pytest.fixture
def application() -> FastAPI:
    application = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with SessionFactory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as test_client:
        yield test_client


async def test_summary_cache_hit_does_not_construct_provider(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(summary="Cached meeting summary.")

    def fail_provider() -> object:
        raise AssertionError("provider must not be constructed for a cache hit")

    monkeypatch.setattr(summarization, "build_openai_provider", fail_provider)

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "Cached meeting summary."}


async def test_summary_cache_miss_sends_full_rendered_transcript_and_caches_result(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog()
    calls: list[tuple[object, str]] = []

    monkeypatch.setattr(
        summarization,
        "build_openai_provider",
        lambda: SimpleNamespace(model="test-model"),
    )

    async def fake_request(provider: object, transcript: str) -> str:
        calls.append((provider, transcript))
        return "Generated meeting summary."

    monkeypatch.setattr(summarization, "request_transcript_summary", fake_request)

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "Generated meeting summary."}
    assert len(calls) == 1
    assert calls[0][1] == "Speaker A: First meeting point.\nSecond meeting point."
    async with SessionFactory() as session:
        assert (
            await session.scalar(
                sa.select(TranscriptSummary.summary).where(
                    TranscriptSummary.meeting_id == "summary-meeting-shared"
                )
            )
            == "Generated meeting summary."
        )


async def test_dialogs_sharing_a_meeting_share_one_summary_cache(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog()
    request_count = 0

    monkeypatch.setattr(
        summarization,
        "build_openai_provider",
        lambda: SimpleNamespace(model="test-model"),
    )

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(summary="Original summary.")
    responses = iter(("Refreshed summary.", ValueError("empty summary output")))

    monkeypatch.setattr(
        summarization,
        "build_openai_provider",
        lambda: SimpleNamespace(model="test-model"),
    )

    async def fake_request(_: object, __: str) -> str:
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
    async with SessionFactory() as session:
        assert (
            await session.scalar(
                sa.select(TranscriptSummary.summary).where(
                    TranscriptSummary.meeting_id == "summary-meeting-shared"
                )
            )
            == "Refreshed summary."
        )


async def test_model_configuration_does_not_change_cache_identity(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog(summary="Model-independent summary.")

    def fail_provider() -> object:
        raise AssertionError("cached summary should be returned")

    monkeypatch.setattr(summarization, "build_openai_provider", fail_provider)

    response = await client.post("/dialogs/summary-dialog-two/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "Model-independent summary."}


async def test_missing_key_returns_503(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog()
    monkeypatch.setattr(
        summarization,
        "build_openai_provider",
        lambda: (_ for _ in ()).throw(RuntimeError("OPENAI_API_KEY is required")),
    )

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 503
    assert response.json() == {"detail": "OpenAI API key is not configured"}


async def test_context_limit_failure_returns_422(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_summary_dialog()
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = BadRequestError(
        "context length exceeded",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "context_length_exceeded"}},
    )
    monkeypatch.setattr(
        summarization,
        "build_openai_provider",
        lambda: SimpleNamespace(model="test-model"),
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
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    await _seed_summary_dialog()
    monkeypatch.setattr(
        summarization,
        "build_openai_provider",
        lambda: SimpleNamespace(model="test-model"),
    )

    async def fail_request(_: object, __: str) -> str:
        raise failure

    monkeypatch.setattr(summarization, "request_transcript_summary", fail_request)

    response = await client.post("/dialogs/summary-dialog-one/summary")

    assert response.status_code == 502
    assert response.json() == {"detail": "Summary provider failed"}


async def test_unknown_dialog_returns_404_without_provider_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_provider() -> object:
        raise AssertionError("provider must not be constructed for an unknown dialog")

    monkeypatch.setattr(summarization, "build_openai_provider", fail_provider)

    response = await client.post("/dialogs/unknown-dialog/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dialog not found"}


async def test_invalid_refresh_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/dialogs/summary-dialog-one/summary",
        params={"refresh": "not-a-boolean"},
    )

    assert response.status_code == 422


async def _seed_summary_dialog(*, summary: str | None = None) -> None:
    async with SessionFactory() as session, session.begin():
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


async def _delete_summary_rows() -> None:
    async with SessionFactory() as session, session.begin():
        await session.execute(
            sa.delete(TranscriptSummary).where(
                TranscriptSummary.meeting_id.in_(SUMMARY_MEETING_IDS)
            )
        )
        await session.execute(
            sa.delete(DialogTurn).where(DialogTurn.dialog_id.in_(SUMMARY_DIALOG_IDS))
        )
        await session.execute(sa.delete(Dialog).where(Dialog.dialog_id.in_(SUMMARY_DIALOG_IDS)))
        await session.execute(
            sa.delete(TranscriptSegment).where(
                TranscriptSegment.meeting_id.in_(SUMMARY_MEETING_IDS)
            )
        )
        await session.execute(
            sa.delete(Transcript).where(Transcript.meeting_id.in_(SUMMARY_MEETING_IDS))
        )
