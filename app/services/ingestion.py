"""Streaming MISeD JSONL ingestion and transcript rendering."""

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment

FILES = ("train", "validation", "test")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormalizedSegment:
    """One validated transcript segment in source order."""

    position: int
    speaker: str | None
    text: str


@dataclass(frozen=True, slots=True)
class NormalizedTurn:
    """One validated dialog turn in source order."""

    position: int
    query: str
    query_metadata: dict[str, Any]
    response: str
    attributions: Any
    references: Any


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    """The bounded in-memory representation of one source dialog."""

    dialog_id: str
    meeting_id: str
    segments: tuple[NormalizedSegment, ...]
    turns: tuple[NormalizedTurn, ...]


@dataclass(frozen=True, slots=True)
class IngestionError:
    """A safe description of one skipped source line."""

    file: str
    line: int
    dialog_id: str | None
    message: str


@dataclass(slots=True)
class IngestionReport:
    """The stable JSON report emitted by the ingestion command."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[IngestionError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the report in its public JSON shape."""
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": [asdict(error) for error in self.errors],
        }


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Report, exit status, and optional safe fatal message."""

    report: IngestionReport
    exit_code: int
    fatal_message: str | None = None


def normalize_record(raw_record: object) -> NormalizedRecord:
    """Validate and normalize one published-shape MISeD record."""
    record = _require_object(raw_record, "record")
    dialog_id = _normalize_identifier(record.get("dialogId"), "dialogId")

    meeting = _require_object(record.get("meeting"), "meeting")
    meeting_id = _normalize_identifier(meeting.get("meetingId"), "meeting.meetingId")
    raw_segments = _require_non_empty_list(
        meeting.get("transcriptSegments"),
        "meeting.transcriptSegments",
    )
    segments = tuple(
        _normalize_segment(segment, position) for position, segment in enumerate(raw_segments)
    )

    dialog = _require_object(record.get("dialog"), "dialog")
    raw_turns = _require_non_empty_list(dialog.get("dialogTurns"), "dialog.dialogTurns")
    turns = tuple(_normalize_turn(turn, position) for position, turn in enumerate(raw_turns))

    return NormalizedRecord(
        dialog_id=dialog_id,
        meeting_id=meeting_id,
        segments=segments,
        turns=turns,
    )


def render_transcript(
    segments: Sequence[NormalizedSegment | TranscriptSegment],
) -> str:
    """Render ordered segments as the full meeting transcript."""
    rendered_segments = (
        f"{segment.speaker}: {segment.text}" if segment.speaker else segment.text
        for segment in segments
    )
    return "\n".join(rendered_segments)


async def ingest_dataset(
    dataset_dir: Path,
    session: AsyncSession,
) -> IngestionResult:
    """Stream all MISeD files into PostgreSQL and return the command result."""
    report = IngestionReport()
    logger.info("Starting dataset ingestion: dataset_dir=%r", str(dataset_dir))

    try:
        dataset_files = _required_dataset_files(dataset_dir)
    except OSError as exc:
        return _fatal_result(report, str(exc))

    try:
        for _, dataset_file in dataset_files:
            logger.info("Ingesting dataset file=%s", dataset_file.name)
            with dataset_file.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    raw_record = None
                    try:
                        raw_record = json.loads(line)
                        normalized = normalize_record(raw_record)
                    except json.JSONDecodeError as exc:
                        report.skipped += 1
                        logger.warning(
                            "Skipping invalid JSON record: file=%s line=%d error_type=%s",
                            dataset_file.name,
                            line_number,
                            type(exc).__name__,
                        )
                        report.errors.append(
                            IngestionError(
                                file=dataset_file.name,
                                line=line_number,
                                dialog_id=None,
                                message="invalid JSON",
                            )
                        )
                        continue
                    except (TypeError, ValueError) as exc:
                        report.skipped += 1
                        logger.warning(
                            "Skipping invalid dataset record: file=%s line=%d "
                            "dialog_id=%r error_type=%s",
                            dataset_file.name,
                            line_number,
                            _dialog_id_for_error(raw_record),
                            type(exc).__name__,
                        )
                        report.errors.append(
                            IngestionError(
                                file=dataset_file.name,
                                line=line_number,
                                dialog_id=_dialog_id_for_error(raw_record),
                                message=str(exc),
                            )
                        )
                        continue

                    try:
                        async with session.begin():
                            created = await _persist_record(session, normalized)
                    except SQLAlchemyError as exc:
                        logger.error(
                            "Database failure during ingestion: file=%s line=%d error_type=%s",
                            dataset_file.name,
                            line_number,
                            type(exc).__name__,
                        )
                        return _fatal_result(report, "Database failure during ingestion")

                    if created:
                        report.created += 1
                    else:
                        report.updated += 1
    except OSError as exc:
        logger.error("Unable to read dataset files: error_type=%s", type(exc).__name__)
        return _fatal_result(report, "Unable to read dataset files")
    except SQLAlchemyError as exc:
        logger.error("Database failure during ingestion: error_type=%s", type(exc).__name__)
        return _fatal_result(report, "Database failure during ingestion")

    exit_code = 1 if report.skipped else 0
    logger.info(
        "Dataset ingestion complete: created=%d updated=%d skipped=%d exit_code=%d",
        report.created,
        report.updated,
        report.skipped,
        exit_code,
    )
    return IngestionResult(report=report, exit_code=exit_code)


def _required_dataset_files(dataset_dir: Path) -> tuple[tuple[str, Path], ...]:
    dataset_files = tuple((file, dataset_dir / f"{file}.jsonl") for file in FILES)
    missing_files = tuple(path.name for _, path in dataset_files if not path.is_file())
    if missing_files:
        names = ", ".join(missing_files)
        raise OSError(f"Missing required dataset file(s): {names}")
    return dataset_files


def _normalize_segment(raw_segment: object, position: int) -> NormalizedSegment:
    path = f"meeting.transcriptSegments[{position}]"
    segment = _require_object(raw_segment, path)
    text = _require_string(segment.get("text"), f"{path}.text")

    speaker_value = segment.get("speakerName")
    if speaker_value is not None and not isinstance(speaker_value, str):
        raise ValueError(f"{path}.speakerName must be a string or null")
    speaker = speaker_value.strip() if speaker_value and speaker_value.strip() else None

    return NormalizedSegment(position=position, speaker=speaker, text=text)


def _normalize_turn(raw_turn: object, position: int) -> NormalizedTurn:
    path = f"dialog.dialogTurns[{position}]"
    turn = _require_object(raw_turn, path)
    query = _require_string(turn.get("query"), f"{path}.query")
    query_metadata = _require_object(turn.get("queryMetadata"), f"{path}.queryMetadata")
    response = _require_string(turn.get("response"), f"{path}.response", non_empty=False)

    attributions = _optional_json_value(turn, "responseAttribution", default=[])
    references = _optional_json_value(turn, "references", default=[])
    return NormalizedTurn(
        position=position,
        query=query,
        query_metadata=query_metadata,
        response=response,
        attributions=attributions,
        references=references,
    )


def _require_object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _require_non_empty_list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty list")
    return value


def _require_string(value: object, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if non_empty and not value.strip():
        raise ValueError(f"{path} must be non-empty")
    return value


def _normalize_identifier(value: object, path: str) -> str:
    identifier = _require_string(value, path)
    return identifier.strip()


def _optional_json_value(mapping: dict[str, Any], key: str, *, default: Any) -> Any:
    if key not in mapping:
        return default
    value = mapping[key]
    if value is None:
        raise ValueError(f"{key} must not be null")
    return value


def _dialog_id_for_error(raw_record: object) -> str | None:
    if not isinstance(raw_record, dict):
        return None
    value = raw_record.get("dialogId")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


async def _persist_record(session: AsyncSession, record: NormalizedRecord) -> bool:
    existing_dialog_id = await session.scalar(
        sa.select(Dialog.dialog_id).where(Dialog.dialog_id == record.dialog_id)
    )

    transcript_insert = insert(Transcript).values(meeting_id=record.meeting_id)
    await session.execute(
        transcript_insert.on_conflict_do_update(
            index_elements=[Transcript.meeting_id],
            set_={"meeting_id": transcript_insert.excluded.meeting_id},
        )
    )
    await session.execute(
        sa.delete(TranscriptSegment).where(TranscriptSegment.meeting_id == record.meeting_id)
    )
    await session.execute(
        insert(TranscriptSegment).values(
            [
                {
                    "meeting_id": record.meeting_id,
                    "position": segment.position,
                    "speaker": segment.speaker,
                    "text": segment.text,
                }
                for segment in record.segments
            ]
        )
    )

    dialog_insert = insert(Dialog).values(
        dialog_id=record.dialog_id,
        meeting_id=record.meeting_id,
    )
    await session.execute(
        dialog_insert.on_conflict_do_update(
            index_elements=[Dialog.dialog_id],
            set_={"meeting_id": dialog_insert.excluded.meeting_id},
        )
    )
    await session.execute(sa.delete(DialogTurn).where(DialogTurn.dialog_id == record.dialog_id))
    await session.execute(
        insert(DialogTurn).values(
            [
                {
                    "dialog_id": record.dialog_id,
                    "position": turn.position,
                    "query": turn.query,
                    "query_metadata": turn.query_metadata,
                    "response": turn.response,
                    "attributions": turn.attributions,
                    "references": turn.references,
                }
                for turn in record.turns
            ]
        )
    )

    return existing_dialog_id is None


def _fatal_result(report: IngestionReport, message: str) -> IngestionResult:
    logger.error("Dataset ingestion failed: %s", message)
    return IngestionResult(report=report, exit_code=2, fatal_message=message)
