import json
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Dialog,
    DialogTurn,
    Transcript,
    TranscriptSegment,
    TranscriptSummary,
)
from app.services.ingestion import (
    ingest_dataset,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures"
CLEAN_FIXTURES = FIXTURE_ROOT / "mised_valid"
MIXED_FIXTURES = FIXTURE_ROOT / "mised"
FIXTURE_DIALOG_IDS = (
    "fixture-dialog-train-1",
    "fixture-dialog-train-2",
    "fixture-dialog-validation-1",
    "fixture-dialog-test-1",
)
FIXTURE_MEETING_IDS = (
    "fixture-meeting-shared",
    "fixture-meeting-validation",
    "fixture-meeting-test",
)


async def test_ingests_all_files_and_shares_transcripts(
    db_session: AsyncSession,
) -> None:
    result = await ingest_dataset(CLEAN_FIXTURES, session=db_session)

    assert result.exit_code == 0
    assert result.report.created == 4
    assert result.report.updated == 0
    assert result.report.skipped == 0

    assert await _count(db_session, Transcript, Transcript.meeting_id, FIXTURE_MEETING_IDS) == 3
    assert await _count(db_session, Dialog, Dialog.dialog_id, FIXTURE_DIALOG_IDS) == 4
    assert (
        await _count(
            db_session,
            TranscriptSegment,
            TranscriptSegment.meeting_id,
            FIXTURE_MEETING_IDS,
        )
        == 4
    )
    assert await _count(db_session, DialogTurn, DialogTurn.dialog_id, FIXTURE_DIALOG_IDS) == 4

    shared_dialogs = await db_session.scalars(
        sa.select(Dialog.dialog_id)
        .where(Dialog.meeting_id == "fixture-meeting-shared")
        .order_by(Dialog.dialog_id)
    )
    assert list(shared_dialogs) == [
        "fixture-dialog-train-1",
        "fixture-dialog-train-2",
    ]

    turn = await db_session.scalar(
        sa.select(DialogTurn).where(DialogTurn.dialog_id == "fixture-dialog-train-1")
    )
    assert turn is not None
    assert turn.attributions == {"indexRanges": [{"startIndex": 0, "endIndex": 0}]}
    assert turn.references == []


async def test_identical_reimport_is_idempotent_and_preserves_summary(
    db_session: AsyncSession,
) -> None:
    first_result = await ingest_dataset(CLEAN_FIXTURES, session=db_session)

    async with db_session.begin():
        db_session.add(
            TranscriptSummary(
                meeting_id="fixture-meeting-shared",
                summary="Keep this cached summary.",
            )
        )

    second_result = await ingest_dataset(CLEAN_FIXTURES, session=db_session)

    assert first_result.report.created == 4
    assert second_result.report.created == 0
    assert second_result.report.updated == 4
    assert second_result.report.skipped == 0

    assert await _count(db_session, Transcript, Transcript.meeting_id, FIXTURE_MEETING_IDS) == 3
    assert await _count(db_session, Dialog, Dialog.dialog_id, FIXTURE_DIALOG_IDS) == 4
    summary = await db_session.scalar(
        sa.select(TranscriptSummary.summary).where(
            TranscriptSummary.meeting_id == "fixture-meeting-shared"
        )
    )
    assert summary == "Keep this cached summary."


async def test_reimport_replaces_children_and_generated_responses_without_touching_summary(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    await ingest_dataset(CLEAN_FIXTURES, session=db_session)

    async with db_session.begin():
        db_session.add(
            TranscriptSummary(
                meeting_id="fixture-meeting-shared",
                summary="Keep this summary during replacement.",
            )
        )
        turn = await db_session.scalar(
            sa.select(DialogTurn).where(
                DialogTurn.dialog_id == "fixture-dialog-train-1",
                DialogTurn.position == 0,
            )
        )
        assert turn is not None
        turn.generated_response = "Delete this generated response during replacement."
        db_session.add(
            DialogTurn(
                dialog_id="fixture-dialog-train-1",
                position=1,
                query="Removed query?",
                query_metadata={},
                response="Removed stored response.",
                generated_response="Remove this generated response.",
                attributions=[],
                references=[],
            )
        )

    changed_train_records = [
        json.loads(line)
        for line in (CLEAN_FIXTURES / "train.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for record in changed_train_records:
        record["meeting"]["transcriptSegments"] = [
            {"text": "The replacement segment.", "speakerName": "Speaker Z"}
        ]
    changed_train_records[0]["dialog"]["dialogTurns"] = [
        {
            "query": "What changed?",
            "response": "The source record was replaced.",
            "queryMetadata": {"queryType": "QUERY_TYPE_SPECIFIC"},
            "responseAttribution": {"indexRanges": []},
        }
    ]
    changed_fixture_dir = _write_fixture_set(tmp_path, train_records=changed_train_records)

    result = await ingest_dataset(changed_fixture_dir, session=db_session)

    assert result.report.created == 0
    assert result.report.updated == 4
    db_session.expire_all()
    segments = await db_session.scalars(
        sa.select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == "fixture-meeting-shared")
        .order_by(TranscriptSegment.position)
    )
    assert [(segment.position, segment.text) for segment in segments] == [
        (0, "The replacement segment.")
    ]
    turns = list(
        await db_session.scalars(
            sa.select(DialogTurn)
            .where(DialogTurn.dialog_id == "fixture-dialog-train-1")
            .order_by(DialogTurn.position)
        )
    )
    assert len(turns) == 1
    turn = turns[0]
    assert turn is not None
    assert turn.query == "What changed?"
    assert turn.generated_response is None
    summary = await db_session.scalar(
        sa.select(TranscriptSummary.summary).where(
            TranscriptSummary.meeting_id == "fixture-meeting-shared"
        )
    )
    assert summary == "Keep this summary during replacement."


async def test_malformed_records_are_skipped_while_valid_records_commit(
    db_session: AsyncSession,
) -> None:
    result = await ingest_dataset(MIXED_FIXTURES, session=db_session)

    assert result.exit_code == 1
    assert result.report.created == 4
    assert result.report.updated == 0
    assert result.report.skipped == 4
    assert [(error.file, error.line) for error in result.report.errors] == [
        ("train.jsonl", 3),
        ("train.jsonl", 4),
        ("train.jsonl", 5),
        ("train.jsonl", 6),
    ]
    assert result.report.errors[0].dialog_id is None
    assert result.report.errors[0].message == "invalid JSON"

    assert await _count(db_session, Dialog, Dialog.dialog_id, FIXTURE_DIALOG_IDS) == 4


async def test_missing_file_is_fatal_before_any_database_write(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    (tmp_path / "train.jsonl").write_text(
        (CLEAN_FIXTURES / "train.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = await ingest_dataset(tmp_path, session=db_session)

    assert result.exit_code == 2
    assert result.report.created == 0
    assert result.fatal_message == "Missing required dataset file(s): validation.jsonl, test.jsonl"


async def _count(session: Any, model: Any, column: Any, values: tuple[str, ...]) -> int:
    statement = sa.select(sa.func.count()).select_from(model).where(column.in_(values))
    return int(await session.scalar(statement))


def _write_fixture_set(
    destination: Path,
    *,
    train_records: list[dict[str, Any]] | None = None,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for file in ("train", "validation", "test"):
        if file == "train" and train_records is not None:
            lines = [json.dumps(record) for record in train_records]
        else:
            lines = (CLEAN_FIXTURES / f"{file}.jsonl").read_text(encoding="utf-8").splitlines()
        (destination / f"{file}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination
