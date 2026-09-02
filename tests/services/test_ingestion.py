import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from app.db.models import (
    Dialog,
    DialogTurn,
    Transcript,
    TranscriptSegment,
    TranscriptSummary,
)
from app.db.session import SessionFactory
from app.services.ingestion import (
    NormalizedSegment,
    ingest_dataset,
    render_transcript,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
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


@pytest.fixture(autouse=True)
async def clean_fixture_rows() -> AsyncIterator[None]:
    await _delete_fixture_rows()
    yield
    await _delete_fixture_rows()


def test_render_transcript_formats_speakers_and_without_speaker_lines() -> None:
    segments = (
        NormalizedSegment(position=0, speaker="Speaker A", text="First line."),
        NormalizedSegment(position=1, speaker=None, text="Second line."),
    )

    assert render_transcript(segments) == "Speaker A: First line.\nSecond line."


async def test_ingests_all_splits_and_shares_transcripts() -> None:
    result = await ingest_dataset(CLEAN_FIXTURES)

    assert result.exit_code == 0
    assert result.report.created == 4
    assert result.report.updated == 0
    assert result.report.skipped == 0

    async with SessionFactory() as session:
        assert await _count(session, Transcript) == 3
        assert await _count(session, Dialog) == 4
        assert await _count(session, TranscriptSegment) == 4
        assert await _count(session, DialogTurn) == 4

        shared_dialogs = await session.scalars(
            sa.select(Dialog.dialog_id)
            .where(Dialog.meeting_id == "fixture-meeting-shared")
            .order_by(Dialog.dialog_id)
        )
        assert list(shared_dialogs) == [
            "fixture-dialog-train-1",
            "fixture-dialog-train-2",
        ]

        turn = await session.scalar(
            sa.select(DialogTurn).where(DialogTurn.dialog_id == "fixture-dialog-train-1")
        )
        assert turn is not None
        assert turn.attributions == {"indexRanges": [{"startIndex": 0, "endIndex": 0}]}
        assert turn.references == []


async def test_identical_reimport_is_idempotent_and_preserves_summary() -> None:
    first_result = await ingest_dataset(CLEAN_FIXTURES)

    async with SessionFactory() as session, session.begin():
        session.add(
            TranscriptSummary(
                meeting_id="fixture-meeting-shared",
                summary="Keep this cached summary.",
            )
        )

    second_result = await ingest_dataset(CLEAN_FIXTURES)

    assert first_result.report.created == 4
    assert second_result.report.created == 0
    assert second_result.report.updated == 4
    assert second_result.report.skipped == 0

    async with SessionFactory() as session:
        assert await _count(session, Transcript) == 3
        assert await _count(session, Dialog) == 4
        summary = await session.scalar(
            sa.select(TranscriptSummary.summary).where(
                TranscriptSummary.meeting_id == "fixture-meeting-shared"
            )
        )
        assert summary == "Keep this cached summary."


async def test_reimport_replaces_children_without_touching_summary(tmp_path: Path) -> None:
    await ingest_dataset(CLEAN_FIXTURES)

    async with SessionFactory() as session, session.begin():
        session.add(
            TranscriptSummary(
                meeting_id="fixture-meeting-shared",
                summary="Keep this summary during replacement.",
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

    result = await ingest_dataset(changed_fixture_dir)

    assert result.report.created == 0
    assert result.report.updated == 4
    async with SessionFactory() as session:
        segments = await session.scalars(
            sa.select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == "fixture-meeting-shared")
            .order_by(TranscriptSegment.position)
        )
        assert [(segment.position, segment.text) for segment in segments] == [
            (0, "The replacement segment.")
        ]
        turn = await session.scalar(
            sa.select(DialogTurn).where(DialogTurn.dialog_id == "fixture-dialog-train-1")
        )
        assert turn is not None
        assert turn.query == "What changed?"
        summary = await session.scalar(
            sa.select(TranscriptSummary.summary).where(
                TranscriptSummary.meeting_id == "fixture-meeting-shared"
            )
        )
        assert summary == "Keep this summary during replacement."


async def test_malformed_records_are_skipped_while_valid_records_commit() -> None:
    result = await ingest_dataset(MIXED_FIXTURES)

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

    async with SessionFactory() as session:
        assert await _count(session, Dialog) == 4


async def test_missing_file_is_fatal_before_any_database_write(tmp_path: Path) -> None:
    (tmp_path / "train.jsonl").write_text(
        (CLEAN_FIXTURES / "train.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = await ingest_dataset(tmp_path)

    assert result.exit_code == 2
    assert result.report.created == 0
    assert result.fatal_message == "Missing required dataset file(s): validation.jsonl, test.jsonl"


async def test_database_failure_is_fatal() -> None:
    class FailingSessionFactory:
        def __call__(self) -> "FailingSessionFactory":
            return self

        async def __aenter__(self) -> None:
            raise OperationalError("connect", {}, RuntimeError("database unavailable"))

        async def __aexit__(self, *_: object) -> None:
            return None

    result = await ingest_dataset(
        CLEAN_FIXTURES,
        session_factory=cast(Any, FailingSessionFactory()),
    )

    assert result.exit_code == 2
    assert result.fatal_message == "Database failure during ingestion"


async def _delete_fixture_rows() -> None:
    async with SessionFactory() as session, session.begin():
        await session.execute(
            sa.delete(DialogTurn).where(DialogTurn.dialog_id.in_(FIXTURE_DIALOG_IDS))
        )
        await session.execute(sa.delete(Dialog).where(Dialog.dialog_id.in_(FIXTURE_DIALOG_IDS)))
        await session.execute(
            sa.delete(TranscriptSegment).where(
                TranscriptSegment.meeting_id.in_(FIXTURE_MEETING_IDS)
            )
        )
        await session.execute(
            sa.delete(TranscriptSummary).where(
                TranscriptSummary.meeting_id.in_(FIXTURE_MEETING_IDS)
            )
        )
        await session.execute(
            sa.delete(Transcript).where(Transcript.meeting_id.in_(FIXTURE_MEETING_IDS))
        )


async def _count(session: Any, model: Any) -> int:
    return int(await session.scalar(sa.select(sa.func.count()).select_from(model)))


def _write_fixture_set(
    destination: Path,
    *,
    train_records: list[dict[str, Any]] | None = None,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for split in ("train", "validation", "test"):
        if split == "train" and train_records is not None:
            lines = [json.dumps(record) for record in train_records]
        else:
            lines = (CLEAN_FIXTURES / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
        (destination / f"{split}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination
