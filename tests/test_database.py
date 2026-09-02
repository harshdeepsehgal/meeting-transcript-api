import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.base import SCHEMA_NAME, Base
from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment, TranscriptSummary
from app.db.session import build_engine, get_session


def test_domain_models_declare_the_normalized_tables() -> None:
    assert set(Base.metadata.tables) == {
        f"{SCHEMA_NAME}.transcripts",
        f"{SCHEMA_NAME}.transcript_segments",
        f"{SCHEMA_NAME}.dialogs",
        f"{SCHEMA_NAME}.dialog_turns",
        f"{SCHEMA_NAME}.transcript_summaries",
    }
    assert Transcript.__table__.c.meeting_id.primary_key
    assert {column.name for column in TranscriptSegment.__table__.primary_key} == {
        "meeting_id",
        "position",
    }
    assert {column.name for column in DialogTurn.__table__.primary_key} == {
        "dialog_id",
        "position",
    }
    assert Dialog.__table__.c.dialog_id.primary_key
    assert TranscriptSummary.__table__.c.meeting_id.primary_key


def test_domain_models_declare_postgresql_json_fields_and_defaults() -> None:
    turns = DialogTurn.__table__

    assert isinstance(turns.c.query_metadata.type, JSONB)
    assert isinstance(turns.c.attributions.type, JSONB)
    assert isinstance(turns.c.references.type, JSONB)
    assert turns.c.attributions.server_default is not None
    assert turns.c.references.server_default is not None


def test_domain_models_declare_foreign_key_actions_and_checks() -> None:
    segment_fk = next(iter(TranscriptSegment.__table__.c.meeting_id.foreign_keys))
    dialog_fk = next(iter(Dialog.__table__.c.meeting_id.foreign_keys))
    turn_fk = next(iter(DialogTurn.__table__.c.dialog_id.foreign_keys))
    summary_fk = next(iter(TranscriptSummary.__table__.c.meeting_id.foreign_keys))

    assert segment_fk.ondelete == "CASCADE"
    assert dialog_fk.ondelete == "RESTRICT"
    assert turn_fk.ondelete == "CASCADE"
    assert summary_fk.ondelete == "CASCADE"

    checks = {
        check.name
        for table in Base.metadata.tables.values()
        for check in table.constraints
        if isinstance(check, sa.CheckConstraint)
    }
    assert checks == {
        "ck_transcript_segments_position_nonnegative",
        "ck_transcript_segments_text_nonempty",
        "ck_dialog_turns_position_nonnegative",
        "ck_dialog_turns_query_nonempty",
        "ck_dialog_turns_query_metadata_object",
        "ck_transcript_summaries_summary_nonempty",
    }


async def test_build_engine_does_not_require_a_live_database() -> None:
    engine = build_engine("postgresql+psycopg://postgres:postgres@localhost/test")

    assert isinstance(engine, AsyncEngine)
    assert engine.dialect.driver == "psycopg"
    await engine.dispose()


async def test_session_dependency_yields_an_async_session() -> None:
    dependency = get_session()
    session = await anext(dependency)

    assert isinstance(session, AsyncSession)

    await dependency.aclose()
