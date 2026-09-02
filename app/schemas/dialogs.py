"""Response models for dialog read APIs."""

from typing import Any

from pydantic import BaseModel


class DialogListItem(BaseModel):
    """Compact representation of one dialog."""

    dialog_id: str
    meeting_id: str


class DialogListResponse(BaseModel):
    """Bounded dialog list response with an optional continuation cursor."""

    items: list[DialogListItem]
    next_cursor: str | None = None


class TranscriptSegmentResponse(BaseModel):
    """One ordered transcript segment."""

    position: int
    speaker: str | None
    text: str


class DialogTurnResponse(BaseModel):
    """One ordered query/response turn."""

    position: int
    query: str
    query_metadata: dict[str, Any]
    response: str
    attributions: Any
    references: Any


class DialogDetailResponse(BaseModel):
    """Complete dialog content and its associated meeting transcript."""

    dialog_id: str
    meeting_id: str
    transcript: list[TranscriptSegmentResponse]
    turns: list[DialogTurnResponse]


__all__ = [
    "DialogDetailResponse",
    "DialogListItem",
    "DialogListResponse",
    "DialogTurnResponse",
    "TranscriptSegmentResponse",
]
