from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment
from app.db.session import SessionFactory, get_session
from app.main import create_app

TEST_DIALOG_IDS = {
    "api-dialog-001",
    "api-dialog-002",
    "api-dialog-003",
    "api-dialog-detail",
}
TEST_MEETING_IDS = {
    "api-meeting-001",
    "api-meeting-002",
    "api-meeting-003",
    "api-meeting-detail",
}


@pytest.fixture(autouse=True)
async def clean_test_rows() -> AsyncIterator[None]:
    await _delete_test_rows()
    yield
    await _delete_test_rows()


@pytest.fixture
def application() -> object:
    application = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with SessionFactory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(application: object) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as test_client:
        yield test_client


async def test_list_dialogs_returns_empty_page(client: httpx.AsyncClient) -> None:
    last_dialog_id = await _last_dialog_id()
    response = await client.get(
        "/dialogs",
        params={"cursor": last_dialog_id} if last_dialog_id is not None else None,
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_list_dialogs_uses_bounded_cursor_pagination(
    client: httpx.AsyncClient,
) -> None:
    dialog_ids, meeting_ids, cursor = await _seed_list_rows()

    first_params: dict[str, str | int] = {"limit": 2}
    if cursor is not None:
        first_params["cursor"] = cursor
    first_page = await client.get("/dialogs", params=first_params)
    second_page = await client.get(
        "/dialogs",
        params={"limit": 2, "cursor": dialog_ids[1]},
    )

    assert first_page.status_code == 200
    assert first_page.json() == {
        "items": [
            {"dialog_id": dialog_ids[0], "meeting_id": meeting_ids[0]},
            {"dialog_id": dialog_ids[1], "meeting_id": meeting_ids[1]},
        ],
        "next_cursor": dialog_ids[1],
    }
    assert second_page.status_code == 200
    assert second_page.json() == {
        "items": [{"dialog_id": dialog_ids[2], "meeting_id": meeting_ids[2]}],
        "next_cursor": None,
    }


@pytest.mark.parametrize("limit", [0, 101])
async def test_list_dialogs_rejects_limit_out_of_bounds(
    client: httpx.AsyncClient,
    limit: int,
) -> None:
    response = await client.get("/dialogs", params={"limit": limit})

    assert response.status_code == 422


async def test_list_dialogs_rejects_empty_cursor(client: httpx.AsyncClient) -> None:
    response = await client.get("/dialogs", params={"cursor": ""})

    assert response.status_code == 422


async def test_get_dialog_returns_ordered_complete_content(
    client: httpx.AsyncClient,
) -> None:
    await _seed_detail_row()

    response = await client.get("/dialogs/api-dialog-detail")

    assert response.status_code == 200
    assert response.json() == {
        "dialog_id": "api-dialog-detail",
        "meeting_id": "api-meeting-detail",
        "transcript": [
            {"position": 0, "speaker": "Speaker A", "text": "First segment."},
            {"position": 1, "speaker": None, "text": "Second segment."},
        ],
        "turns": [
            {
                "position": 0,
                "query": "First question?",
                "query_metadata": {"queryType": "specific"},
                "response": "First answer.",
                "attributions": {"indexRanges": [{"startIndex": 0, "endIndex": 0}]},
                "references": [],
            },
            {
                "position": 1,
                "query": "Second question?",
                "query_metadata": {"queryType": "broad"},
                "response": "Second answer.",
                "attributions": [],
                "references": [{"url": "https://example.test/reference"}],
            },
        ],
    }


async def test_get_dialog_returns_exact_404_for_unknown_dialog(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/dialogs/unknown-dialog")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dialog not found"}


async def _last_dialog_id() -> str | None:
    async with SessionFactory() as session:
        return await session.scalar(
            sa.select(Dialog.dialog_id).order_by(Dialog.dialog_id.desc()).limit(1)
        )


async def _seed_list_rows() -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    async with SessionFactory() as session, session.begin():
        cursor = await session.scalar(
            sa.select(Dialog.dialog_id).order_by(Dialog.dialog_id.desc()).limit(1)
        )
        namespace = f"{cursor}zz-test-{uuid4().hex}" if cursor else f"zz-test-{uuid4().hex}"
        dialog_ids = tuple(f"{namespace}-dialog-{position}" for position in range(1, 4))
        meeting_ids = tuple(f"{namespace}-meeting-{position}" for position in range(1, 4))
        TEST_DIALOG_IDS.update(dialog_ids)
        TEST_MEETING_IDS.update(meeting_ids)
        session.add_all([Transcript(meeting_id=meeting_id) for meeting_id in meeting_ids])
        await session.flush()
        session.add_all(
            [
                Dialog(dialog_id=dialog_id, meeting_id=meeting_id)
                for dialog_id, meeting_id in zip(dialog_ids, meeting_ids, strict=True)
            ]
        )
    return dialog_ids, meeting_ids, cursor


async def _seed_detail_row() -> None:
    async with SessionFactory() as session, session.begin():
        session.add(Transcript(meeting_id="api-meeting-detail"))
        await session.flush()
        session.add(Dialog(dialog_id="api-dialog-detail", meeting_id="api-meeting-detail"))
        await session.flush()
        session.add_all(
            [
                TranscriptSegment(
                    meeting_id="api-meeting-detail",
                    position=1,
                    speaker=None,
                    text="Second segment.",
                ),
                TranscriptSegment(
                    meeting_id="api-meeting-detail",
                    position=0,
                    speaker="Speaker A",
                    text="First segment.",
                ),
                DialogTurn(
                    dialog_id="api-dialog-detail",
                    position=1,
                    query="Second question?",
                    query_metadata={"queryType": "broad"},
                    response="Second answer.",
                    attributions=[],
                    references=[{"url": "https://example.test/reference"}],
                ),
                DialogTurn(
                    dialog_id="api-dialog-detail",
                    position=0,
                    query="First question?",
                    query_metadata={"queryType": "specific"},
                    response="First answer.",
                    attributions={"indexRanges": [{"startIndex": 0, "endIndex": 0}]},
                    references=[],
                ),
            ]
        )


async def _delete_test_rows() -> None:
    async with SessionFactory() as session, session.begin():
        await session.execute(
            sa.delete(DialogTurn).where(DialogTurn.dialog_id.in_(TEST_DIALOG_IDS))
        )
        await session.execute(sa.delete(Dialog).where(Dialog.dialog_id.in_(TEST_DIALOG_IDS)))
        await session.execute(
            sa.delete(TranscriptSegment).where(TranscriptSegment.meeting_id.in_(TEST_MEETING_IDS))
        )
        await session.execute(
            sa.delete(Transcript).where(Transcript.meeting_id.in_(TEST_MEETING_IDS))
        )
