import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment

TEST_DIALOG_IDS = (
    "api-dialog-001",
    "api-dialog-002",
    "api-dialog-003",
    "api-dialog-detail",
)
TEST_MEETING_IDS = (
    "api-meeting-001",
    "api-meeting-002",
    "api-meeting-003",
    "api-meeting-detail",
)


async def test_list_dialogs_returns_empty_page(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/dialogs")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_list_dialogs_uses_bounded_cursor_pagination(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    dialog_ids, meeting_ids = await _seed_list_rows(db_session)

    first_page = await client.get("/dialogs", params={"limit": 2})
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
    db_session: AsyncSession,
) -> None:
    await _seed_detail_row(db_session)

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


async def _seed_list_rows(
    session: AsyncSession,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    dialog_ids = TEST_DIALOG_IDS[:3]
    meeting_ids = TEST_MEETING_IDS[:3]
    async with session.begin():
        session.add_all([Transcript(meeting_id=meeting_id) for meeting_id in meeting_ids])
        await session.flush()
        session.add_all(
            [
                Dialog(dialog_id=dialog_id, meeting_id=meeting_id)
                for dialog_id, meeting_id in zip(dialog_ids, meeting_ids, strict=True)
            ]
        )
    return dialog_ids, meeting_ids


async def _seed_detail_row(
    session: AsyncSession,
) -> None:
    async with session.begin():
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
