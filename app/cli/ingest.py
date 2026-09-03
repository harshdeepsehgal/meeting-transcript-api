import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest MISeD JSONL data into PostgreSQL.")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("mised"),
        help="Directory containing train.jsonl, validation.jsonl, and test.jsonl.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:     %(asctime)s - %(name)s - %(message)s",
    )
    logger.info("Starting ingestion command: dataset_dir=%r", str(args.dataset_dir))
    try:
        from app.services.ingestion import ingest_dataset

        result = asyncio.run(ingest_dataset(args.dataset_dir))
    except Exception as exc:
        logger.error("Fatal ingestion command failure: error_type=%s", type(exc).__name__)
        print(
            json.dumps(
                {"created": 0, "updated": 0, "skipped": 0, "errors": []},
                separators=(",", ":"),
            )
        )
        print("Fatal ingestion failure", file=sys.stderr)
        return 2

    print(json.dumps(result.report.to_dict(), separators=(",", ":"), ensure_ascii=False))
    if result.fatal_message:
        print(result.fatal_message, file=sys.stderr)
    logger.info("Ingestion command complete: exit_code=%d", result.exit_code)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
