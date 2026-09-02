"""Streaming MISeD JSONL ingestion and transcript rendering."""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Dialog, DialogTurn, Transcript, TranscriptSegment
from app.db.session import SessionFactory

SPLITS = ("train", "validation", "test")


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

    split: str
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
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> IngestionResult:
    """Stream all MISeD splits into PostgreSQL and return the command result."""
    report = IngestionReport()

    try:
        dataset_files = _required_dataset_files(dataset_dir)
    except OSError as exc:
        return _fatal_result(report, str(exc))

    factory = session_factory or SessionFactory
    try:
        async with factory() as session:
            for split, dataset_file in dataset_files:
                with dataset_file.open("r", encoding="utf-8") as stream:
                    for line_number, line in enumerate(stream, start=1):
                        try:
                            raw_record = json.loads(line)
                            normalized = normalize_record(raw_record)
                        except json.JSONDecodeError:
                            report.skipped += 1
                            report.errors.append(
                                IngestionError(
                                    split=split,
                                    file=dataset_file.name,
                                    line=line_number,
                                    dialog_id=None,
                                    message="invalid JSON",
                                )
                            )
                            continue
                        except (TypeError, ValueError) as exc:
                            report.skipped += 1
                            report.errors.append(
                                IngestionError(
                                    split=split,
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
                        except SQLAlchemyError:
                            return _fatal_result(report, "Database failure during ingestion")

                        if created:
                            report.created += 1
                        else:
                            report.updated += 1
    except OSError:
        return _fatal_result(report, "Unable to read dataset files")
    except SQLAlchemyError:
        return _fatal_result(report, "Database failure during ingestion")

    exit_code = 1 if report.skipped else 0
    return IngestionResult(report=report, exit_code=exit_code)


def _required_dataset_files(dataset_dir: Path) -> tuple[tuple[str, Path], ...]:
    dataset_files = tuple((split, dataset_dir / f"{split}.jsonl") for split in SPLITS)
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
    return IngestionResult(report=report, exit_code=2, fatal_message=message)
