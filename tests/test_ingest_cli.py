from app.cli.ingest import main


def test_ingest_cli_documents_deferred_implementation(capsys) -> None:
    exit_code = main(["--dataset-dir", "example-data"])

    assert exit_code == 2
    assert "not implemented yet" in capsys.readouterr().err
