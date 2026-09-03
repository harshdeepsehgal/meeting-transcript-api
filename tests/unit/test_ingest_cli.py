import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.cli import ingest as ingest_cli
from app.services import ingestion


@pytest.fixture
def command_database(monkeypatch) -> tuple[SimpleNamespace, object]:
    database_engine = SimpleNamespace(dispose=AsyncMock())
    session = object()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_factory = Mock(return_value=session_context)
    monkeypatch.setattr(ingest_cli, "build_engine", Mock(return_value=database_engine))
    monkeypatch.setattr(
        ingest_cli,
        "build_session_factory",
        Mock(return_value=session_factory),
    )
    return database_engine, session


def test_ingest_cli_emits_report_from_ingestion_service(
    monkeypatch,
    capsys,
    command_database,
) -> None:
    database_engine, expected_session = command_database

    async def fake_ingest(
        dataset_dir: Path,
        session: object,
    ) -> ingestion.IngestionResult:
        assert dataset_dir == Path("example-data")
        assert session is expected_session
        return ingestion.IngestionResult(
            report=ingestion.IngestionReport(created=2),
            exit_code=0,
        )

    monkeypatch.setattr(ingest_cli, "ingest_dataset", fake_ingest)

    exit_code = ingest_cli.main(["--dataset-dir", "example-data"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out) == {
        "created": 2,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    assert captured.err == ""
    database_engine.dispose.assert_awaited_once_with()


def test_ingest_cli_reports_missing_dataset_files(capsys, tmp_path) -> None:
    exit_code = ingest_cli.main(["--dataset-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert json.loads(captured.out) == {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    assert "Missing required dataset file(s)" in captured.err


async def test_run_ingestion_disposes_engine_after_failure(
    monkeypatch,
    command_database,
) -> None:
    database_engine, _ = command_database
    monkeypatch.setattr(
        ingest_cli,
        "ingest_dataset",
        AsyncMock(side_effect=RuntimeError("ingestion failed")),
    )

    with pytest.raises(RuntimeError, match="ingestion failed"):
        await ingest_cli.run_ingestion(Path("example-data"))

    database_engine.dispose.assert_awaited_once_with()
