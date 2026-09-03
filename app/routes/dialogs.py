"""Dialog read endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from openai import APIStatusError, OpenAIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, TranscriptSegment
from app.db.session import get_session
from app.integrations.openai import is_context_limit_error
from app.schemas.dialogs import (
    DialogDetailResponse,
    DialogListItem,
    DialogListResponse,
    DialogTurnResponse,
    SummaryResponse,
    TranscriptSegmentResponse,
)
from app.services.dialogs import get_dialog_detail, list_dialogs
from app.services.summarization import summarize_dialog

router = APIRouter(prefix="/dialogs", tags=["dialogs"])
logger = logging.getLogger(__name__)


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
    logger.info("Listing dialogs: limit=%d cursor_present=%s", limit, cursor is not None)
    dialogs, next_cursor = await list_dialogs(session, limit=limit, cursor=cursor)
    logger.info(
        "Listed dialogs: returned=%d has_next_cursor=%s",
        len(dialogs),
        next_cursor is not None,
    )
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
    logger.info("Reading dialog: dialog_id=%r", dialog_id)
    result = await get_dialog_detail(session, dialog_id=dialog_id)
    if result is None:
        logger.warning("Dialog not found: dialog_id=%r", dialog_id)
        raise HTTPException(status_code=404, detail="Dialog not found")

    dialog, segments, turns = result
    logger.info(
        "Read dialog: dialog_id=%r meeting_id=%r transcript_segments=%d turns=%d",
        dialog.dialog_id,
        dialog.meeting_id,
        len(segments),
        len(turns),
    )
    return _detail_response(dialog, segments, turns)


@router.post(
    "/{dialog_id}/summary",
    response_model=SummaryResponse,
    responses={
        404: {
            "description": "Dialog not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Dialog not found"},
                }
            },
        },
        422: {"description": "Validation error or transcript exceeds model context limit"},
        502: {"description": "Summary provider failed"},
        503: {"description": "OpenAI API key is not configured"},
    },
)
async def create_dialog_summary(
    dialog_id: Annotated[str, Path(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    refresh: Annotated[
        bool,
        Query(description="Regenerate the summary instead of returning the cached value."),
    ] = False,
) -> SummaryResponse:
    """Return a cached or newly generated meeting transcript summary."""
    logger.info("Summary requested: dialog_id=%r refresh=%s", dialog_id, refresh)
    try:
        summary = await summarize_dialog(session, dialog_id=dialog_id, refresh=refresh)
    except APIStatusError as exc:
        if is_context_limit_error(exc):
            logger.warning(
                "Summary rejected by provider context limit: dialog_id=%r",
                dialog_id,
            )
            raise HTTPException(
                status_code=422,
                detail="Transcript exceeds model context limit",
            ) from exc
        logger.error(
            "Summary provider status failure: dialog_id=%r status_code=%s",
            dialog_id,
            exc.status_code,
        )
        raise HTTPException(status_code=502, detail="Summary provider failed") from exc
    except OpenAIError as exc:
        logger.error(
            "Summary provider failure: dialog_id=%r error_type=%s",
            dialog_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Summary provider failed") from exc
    except RuntimeError as exc:
        if "OPENAI_API_KEY" in str(exc):
            logger.warning("Summary unavailable without OpenAI API key: dialog_id=%r", dialog_id)
            raise HTTPException(
                status_code=503,
                detail="OpenAI API key is not configured",
            ) from exc
        logger.error(
            "Summary runtime failure: dialog_id=%r error_type=%s",
            dialog_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Summary provider failed") from exc
    except ValueError as exc:
        logger.error(
            "Summary output failure: dialog_id=%r error_type=%s",
            dialog_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Summary provider failed") from exc

    if summary is None:
        logger.warning("Summary requested for unknown dialog: dialog_id=%r", dialog_id)
        raise HTTPException(status_code=404, detail="Dialog not found")
    logger.info("Summary returned: dialog_id=%r refresh=%s", dialog_id, refresh)
    return SummaryResponse(summary=summary)


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
