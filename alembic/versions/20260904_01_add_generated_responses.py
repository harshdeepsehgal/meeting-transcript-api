"""Add generated responses to dialog turns.

Revision ID: 20260904_01
Revises: 20260902_01
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260904_01"
down_revision: str | None = "20260902_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = "meeting_transcript"


def upgrade() -> None:
    op.add_column(
        "dialog_turns",
        sa.Column("generated_response", sa.Text(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.create_check_constraint(
        "ck_dialog_turns_generated_response_nonempty",
        "dialog_turns",
        "generated_response IS NULL OR length(btrim(generated_response)) > 0",
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_dialog_turns_generated_response_nonempty",
        "dialog_turns",
        type_="check",
        schema=SCHEMA_NAME,
    )
    op.drop_column("dialog_turns", "generated_response", schema=SCHEMA_NAME)
