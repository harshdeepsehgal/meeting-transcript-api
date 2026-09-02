"""Dialog read endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, TranscriptSegment
from app.db.session import get_session
from app.schemas.dialogs import (
    DialogDetailResponse,
    DialogListItem,
    DialogListResponse,
    DialogTurnResponse,
    TranscriptSegmentResponse,
)
from app.services.dialogs import get_dialog_detail, list_dialogs

router = APIRouter(prefix="/dialogs", tags=["dialogs"])


@router.get(
    "",
    response_model=DialogListResponse,
    responses={422: {"description": "Request validation error"}},
)
async def read_dialogs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Maximum number of dialogs to return.",
        ),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            description="Last returned dialog ID from the previous page.",
        ),
    ] = None,
) -> DialogListResponse:
    """List dialogs in ascending ID order with bounded cursor pagination."""
    dialogs, next_cursor = await list_dialogs(session, limit=limit, cursor=cursor)
    return DialogListResponse(
        items=[
            DialogListItem(dialog_id=dialog.dialog_id, meeting_id=dialog.meeting_id)
            for dialog in dialogs
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/{dialog_id}",
    response_model=DialogDetailResponse,
    responses={
        404: {
            "description": "Dialog not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Dialog not found"},
                }
            },
        },
        422: {"description": "Request validation error"},
    },
)
async def read_dialog(
    dialog_id: Annotated[str, Path(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DialogDetailResponse:
    """Return a complete dialog and its ordered meeting transcript."""
    result = await get_dialog_detail(session, dialog_id=dialog_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Dialog not found")

    dialog, segments, turns = result
    return _detail_response(dialog, segments, turns)


def _detail_response(
    dialog: Dialog,
    segments: list[TranscriptSegment],
    turns: list[DialogTurn],
) -> DialogDetailResponse:
    return DialogDetailResponse(
        dialog_id=dialog.dialog_id,
        meeting_id=dialog.meeting_id,
        transcript=[
            TranscriptSegmentResponse(
                position=segment.position,
                speaker=segment.speaker,
                text=segment.text,
            )
            for segment in segments
        ],
        turns=[
            DialogTurnResponse(
                position=turn.position,
                query=turn.query,
                query_metadata=turn.query_metadata,
                response=turn.response,
                attributions=turn.attributions,
                references=turn.references,
            )
            for turn in turns
        ],
    )


__all__ = ["router"]
