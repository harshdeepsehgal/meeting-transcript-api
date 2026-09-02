import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


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
    print(
        f"MISeD ingestion is not implemented yet (dataset directory: {args.dataset_dir}).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
