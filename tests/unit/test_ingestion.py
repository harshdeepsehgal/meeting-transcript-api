from pathlib import Path
from typing import cast

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ingestion import NormalizedSegment, ingest_dataset, render_transcript

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "mised_valid"


def test_render_transcript_formats_speakers_and_without_speaker_lines() -> None:
    segments = (
        NormalizedSegment(position=0, speaker="Speaker A", text="First line."),
        NormalizedSegment(position=1, speaker=None, text="Second line."),
    )

    assert render_transcript(segments) == "Speaker A: First line.\nSecond line."


async def test_database_failure_is_fatal() -> None:
    class FailingSession:
        def begin(self) -> None:
            raise OperationalError("connect", {}, RuntimeError("database unavailable"))

    result = await ingest_dataset(
        FIXTURE_ROOT,
        session=cast(AsyncSession, FailingSession()),
    )

    assert result.exit_code == 2
    assert result.fatal_message == "Database failure during ingestion"
