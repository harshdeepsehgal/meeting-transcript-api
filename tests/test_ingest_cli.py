import json
from pathlib import Path

from app.cli.ingest import main
from app.services import ingestion


def test_ingest_cli_emits_report_from_ingestion_service(monkeypatch, capsys) -> None:
    async def fake_ingest(dataset_dir: Path) -> ingestion.IngestionResult:
        assert dataset_dir == Path("example-data")
        return ingestion.IngestionResult(
            report=ingestion.IngestionReport(created=2),
            exit_code=0,
        )

    monkeypatch.setattr(ingestion, "ingest_dataset", fake_ingest)

    exit_code = main(["--dataset-dir", "example-data"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out) == {
        "created": 2,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    assert captured.err == ""


def test_ingest_cli_reports_missing_dataset_files(capsys, tmp_path) -> None:
    exit_code = main(["--dataset-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert json.loads(captured.out) == {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    assert "Missing required dataset file(s)" in captured.err
