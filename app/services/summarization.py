"""Meeting transcript summarization and cache orchestration."""

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TranscriptSummary
from app.integrations.openai import (
    OpenAIProvider,
    request_transcript_summary,
)
from app.services.dialogs import get_dialog_transcript
from app.services.ingestion import render_transcript

logger = logging.getLogger(__name__)


async def summarize_dialog(
    session: AsyncSession,
    *,
    dialog_id: str,
    refresh: bool,
    provider: OpenAIProvider | None,
) -> str | None:
    """Return a cached summary or generate and cache one for the dialog's meeting."""
    async with session.begin():
        dialog_transcript = await get_dialog_transcript(session, dialog_id=dialog_id)
        if dialog_transcript is None:
            logger.warning("Cannot summarize unknown dialog: dialog_id=%r", dialog_id)
            return None

        dialog, segments = dialog_transcript
        if not refresh:
            cached_summary = await session.scalar(
                sa.select(TranscriptSummary.summary).where(
                    TranscriptSummary.meeting_id == dialog.meeting_id
                )
            )
            if cached_summary is not None:
                logger.info(
                    "Summary cache hit: dialog_id=%r meeting_id=%r",
                    dialog_id,
                    dialog.meeting_id,
                )
                return cached_summary

    logger.info(
        "Summary cache miss or refresh: dialog_id=%r meeting_id=%r refresh=%s",
        dialog_id,
        dialog.meeting_id,
        refresh,
    )
    if provider is None:
        raise RuntimeError("OPENAI_API_KEY is required before using the OpenAI integration")
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

    logger.info(
        "Summary cache updated: dialog_id=%r meeting_id=%r summary_chars=%d",
        dialog_id,
        dialog.meeting_id,
        len(summary),
    )
    return summary


__all__ = ["summarize_dialog"]
