"""Meeting transcript summarization and cache orchestration."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TranscriptSummary
from app.integrations.openai import (
    build_openai_provider,
    request_transcript_summary,
)
from app.services.dialogs import get_dialog_transcript
from app.services.ingestion import render_transcript


async def summarize_dialog(
    session: AsyncSession,
    *,
    dialog_id: str,
    refresh: bool,
) -> str | None:
    """Return a cached summary or generate and cache one for the dialog's meeting."""
    async with session.begin():
        dialog_transcript = await get_dialog_transcript(session, dialog_id=dialog_id)
        if dialog_transcript is None:
            return None

        dialog, segments = dialog_transcript
        if not refresh:
            cached_summary = await session.scalar(
                sa.select(TranscriptSummary.summary).where(
                    TranscriptSummary.meeting_id == dialog.meeting_id
                )
            )
            if cached_summary is not None:
                return cached_summary

    provider = build_openai_provider()
    summary = await request_transcript_summary(provider, render_transcript(segments))
    if not summary.strip():
        raise ValueError("OpenAI returned empty summary output")

    async with session.begin():
        summary_insert = insert(TranscriptSummary).values(
            meeting_id=dialog.meeting_id,
            summary=summary,
        )
        await session.execute(
            summary_insert.on_conflict_do_update(
                index_elements=[TranscriptSummary.meeting_id],
                set_={"summary": summary_insert.excluded.summary},
            )
        )

    return summary


__all__ = ["summarize_dialog"]
