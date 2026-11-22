"""SQLAlchemy models for the normalized MISeD data store."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import SCHEMA_NAME, Base


class Transcript(Base):
    """A meeting transcript shared by one or more dialogs."""

    __tablename__ = "transcripts"

    meeting_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)


class TranscriptSegment(Base):
    """An ordered source segment belonging to a meeting transcript."""

    __tablename__ = "transcript_segments"

    meeting_id: Mapped[str] = mapped_column(
        sa.Text,
        sa.ForeignKey(
            f"{SCHEMA_NAME}.transcripts.meeting_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    speaker: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.CheckConstraint(
            "position >= 0",
            name="ck_transcript_segments_position_nonnegative",
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0",
            name="ck_transcript_segments_text_nonempty",
        ),
    )


class Dialog(Base):
    """A stable MISeD dialog associated with one meeting transcript."""

    __tablename__ = "dialogs"

    dialog_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    meeting_id: Mapped[str] = mapped_column(
        sa.Text,
        sa.ForeignKey(
            f"{SCHEMA_NAME}.transcripts.meeting_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )


class DialogTurn(Base):
    """An ordered query/response turn in a dialog."""

    __tablename__ = "dialog_turns"

    dialog_id: Mapped[str] = mapped_column(
        sa.Text,
        sa.ForeignKey(
            f"{SCHEMA_NAME}.dialogs.dialog_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    query: Mapped[str] = mapped_column(sa.Text, nullable=False)
    query_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[str] = mapped_column(sa.Text, nullable=False)
    generated_response: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    attributions: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    references: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "position >= 0",
            name="ck_dialog_turns_position_nonnegative",
        ),
        sa.CheckConstraint(
            "length(btrim(query)) > 0",
            name="ck_dialog_turns_query_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(query_metadata) = 'object'",
            name="ck_dialog_turns_query_metadata_object",
        ),
        sa.CheckConstraint(
            "generated_response IS NULL OR length(btrim(generated_response)) > 0",
            name="ck_dialog_turns_generated_response_nonempty",
        ),
    )


class TranscriptSummary(Base):
    """The single cached summary for a meeting transcript."""

    __tablename__ = "transcript_summaries"

    meeting_id: Mapped[str] = mapped_column(
        sa.Text,
        sa.ForeignKey(
            f"{SCHEMA_NAME}.transcripts.meeting_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.CheckConstraint(
            "length(btrim(summary)) > 0",
            name="ck_transcript_summaries_summary_nonempty",
        ),
    )


__all__ = [
    "Dialog",
    "DialogTurn",
    "Transcript",
    "TranscriptSegment",
    "TranscriptSummary",
]
