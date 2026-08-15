#!/usr/bin/env python3
"""Generate library.json directly from Audible via audible-cli.

Runs `pyi_entrypoint.py library export --format json` to fetch the live
library, then reduces each entry down to the {"id", "author", "title"}
shape that download_all.py expects.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ENTRYPOINT = REPO_ROOT / "pyi_entrypoint.py"

DEFAULT_OUTPUT = REPO_ROOT / "library.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"where to write the simplified library JSON (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--resolve-podcasts",
        action="store_true",
        help="resolve podcasts to include their individual episodes",
    )
    parser.add_argument(
        "--start-date",
        help="only include books added to the library on or after this UTC date",
    )
    parser.add_argument(
        "--end-date",
        help="only include books added to the library on or before this UTC date",
    )
    return parser.parse_args()


def export_raw_library(args: argparse.Namespace, raw_output: Path) -> None:
    command = [
        sys.executable,
        str(ENTRYPOINT),
        "library", "export",
        "--format", "json",
        "--output", str(raw_output),
    ]
    if args.resolve_podcasts:
        command.append("--resolve-podcasts")
    if args.start_date:
        command += ["--start-date", args.start_date]
    if args.end_date:
        command += ["--end-date", args.end_date]

    subprocess.run(command, cwd=REPO_ROOT, check=True)


def simplify(raw_books: list[dict]) -> list[dict]:
    return [
        {
            "id": item["asin"],
            "author": item.get("authors", ""),
            "title": item["title"],
        }
        for item in raw_books
    ]


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory() as tempdir:
        raw_output = Path(tempdir) / "library.raw.json"
        try:
            export_raw_library(args, raw_output)
        except subprocess.CalledProcessError as e:
            print(f"audible-cli library export failed: {e}", file=sys.stderr)
            return e.returncode or 1

        raw_books = json.loads(raw_output.read_text(encoding="utf-8"))

    books = simplify(raw_books)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as outfile:
        json.dump(books, outfile, indent=4)

    print(f"Wrote {len(books)} book(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
