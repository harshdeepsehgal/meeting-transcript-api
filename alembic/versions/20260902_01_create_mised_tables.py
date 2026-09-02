"""Create normalized MISeD tables.

Revision ID: 20260902_01
Revises:
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260902_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = "meeting_transcript"


def upgrade() -> None:
    op.create_table(
        "transcripts",
        sa.Column("meeting_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("meeting_id", name="pk_transcripts"),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "transcript_segments",
        sa.Column("meeting_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_transcript_segments_position_nonnegative",
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0",
            name="ck_transcript_segments_text_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            [f"{SCHEMA_NAME}.transcripts.meeting_id"],
            name="fk_transcript_segments_meeting_id_transcripts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "meeting_id",
            "position",
            name="pk_transcript_segments",
        ),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "dialogs",
        sa.Column("dialog_id", sa.Text(), nullable=False),
        sa.Column("meeting_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            [f"{SCHEMA_NAME}.transcripts.meeting_id"],
            name="fk_dialogs_meeting_id_transcripts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("dialog_id", name="pk_dialogs"),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "dialog_turns",
        sa.Column("dialog_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "query_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column(
            "attributions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "references",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["dialog_id"],
            [f"{SCHEMA_NAME}.dialogs.dialog_id"],
            name="fk_dialog_turns_dialog_id_dialogs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "dialog_id",
            "position",
            name="pk_dialog_turns",
        ),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        "transcript_summaries",
        sa.Column("meeting_id", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "length(btrim(summary)) > 0",
            name="ck_transcript_summaries_summary_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            [f"{SCHEMA_NAME}.transcripts.meeting_id"],
            name="fk_transcript_summaries_meeting_id_transcripts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("meeting_id", name="pk_transcript_summaries"),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table("transcript_summaries", schema=SCHEMA_NAME)
    op.drop_table("dialog_turns", schema=SCHEMA_NAME)
    op.drop_table("dialogs", schema=SCHEMA_NAME)
    op.drop_table("transcript_segments", schema=SCHEMA_NAME)
    op.drop_table("transcripts", schema=SCHEMA_NAME)
