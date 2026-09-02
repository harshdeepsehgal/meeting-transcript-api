"""Database queries for dialog read APIs."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, TranscriptSegment


async def list_dialogs(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[Dialog], str | None]:
    """Return one bounded, lexicographically ordered dialog page."""
    statement = sa.select(Dialog).order_by(Dialog.dialog_id.asc()).limit(limit + 1)
    if cursor is not None:
        statement = statement.where(Dialog.dialog_id > cursor)

    rows = list(await session.scalars(statement))
    has_more = len(rows) > limit
    dialogs = rows[:limit]
    next_cursor = dialogs[-1].dialog_id if has_more else None
    return dialogs, next_cursor


async def get_dialog_detail(
    session: AsyncSession,
    *,
    dialog_id: str,
) -> tuple[Dialog, list[TranscriptSegment], list[DialogTurn]] | None:
    """Return a dialog with its ordered transcript and turns, if it exists."""
    dialog = await session.scalar(sa.select(Dialog).where(Dialog.dialog_id == dialog_id))
    if dialog is None:
        return None

    segments = list(
        await session.scalars(
            sa.select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == dialog.meeting_id)
            .order_by(TranscriptSegment.position.asc())
        )
    )
    turns = list(
        await session.scalars(
            sa.select(DialogTurn)
            .where(DialogTurn.dialog_id == dialog.dialog_id)
            .order_by(DialogTurn.position.asc())
        )
    )
    return dialog, segments, turns


__all__ = ["get_dialog_detail", "list_dialogs"]
