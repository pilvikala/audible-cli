#!/usr/bin/env python3
"""Download every audiobook listed in library.json using audible-cli.

Reads a JSON file of the form written by get_library.py:
    {"start_date": ..., "end_date": ...,
     "books": [{"id": "ASIN", "author": "...", "title": "..."}, ...]}
(a bare list of books, the older format, is also accepted) and invokes this repo's `pyi_entrypoint.py download` command once per
book, downloading each into its own "<author> - <title>" subfolder under
the destination directory. Each downloaded AAX/AAXC file is then converted
to .m4b with ffmpeg, using this repo's `decrypt` plugin command (which
pulls activation bytes / voucher key+iv via audible-cli automatically).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ENTRYPOINT = REPO_ROOT / "pyi_entrypoint.py"
PLUGIN_DIR = REPO_ROOT / "plugin_cmds"

DEFAULT_LIBRARY_FILE = REPO_ROOT / "library.json"
DEFAULT_DEST_DIR = "/home/michal/Downloads/audiobooks"

# Characters not allowed in filenames on common filesystems (Windows, plus
# "/" for POSIX).
INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_path_component(text: str) -> str:
    cleaned = INVALID_CHARS.sub("-", text)
    cleaned = " ".join(cleaned.split())  # collapse whitespace
    return cleaned.strip(" .") or "unknown"


def book_folder_name(book: dict) -> str:
    author = sanitize_path_component(book.get("author") or "Unknown Author")
    title = sanitize_path_component(book.get("title") or "Unknown Title")
    return f"{author} - {title}"


def load_library(library_file: Path) -> dict:
    """Return the library as {"start_date", "end_date", "books"}, accepting
    both the current object format and the older bare list of books."""
    with library_file.open("r", encoding="utf-8") as f:
        library = json.load(f)

    if isinstance(library, list):
        library = {"start_date": None, "end_date": None, "books": library}

    if not isinstance(library, dict) or not isinstance(library.get("books"), list):
        raise ValueError(
            f"{library_file} must contain a list of books or an object "
            'with a "books" list'
        )

    library.setdefault("start_date", None)
    library.setdefault("end_date", None)
    return library


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library-file",
        type=Path,
        default=DEFAULT_LIBRARY_FILE,
        help=f"path to library.json (default: {DEFAULT_LIBRARY_FILE})",
    )
    parser.add_argument(
        "--dest",
        default=DEFAULT_DEST_DIR,
        help=f"destination folder for downloads (default: {DEFAULT_DEST_DIR})",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=3,
        help="number of simultaneous downloads (default: 3)",
    )
    parser.add_argument(
        "--quality",
        "-q",
        default="best",
        help="download quality (default: best)",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        default=True,
        help="also download the PDF companion file, if any (default: on)",
    )
    parser.add_argument(
        "--no-pdf",
        dest="pdf",
        action="store_false",
        help="do not download PDF companion files",
    )
    parser.add_argument(
        "--cover",
        action="store_true",
        default=True,
        help="also download the cover image (default: on)",
    )
    parser.add_argument(
        "--no-cover",
        dest="cover",
        action="store_false",
        help="do not download cover images",
    )
    parser.add_argument(
        "--no-convert",
        dest="convert",
        action="store_false",
        default=True,
        help="skip converting downloaded AAX/AAXC files to .m4b",
    )
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="delete the original AAX/AAXC (+ voucher) files after a successful .m4b conversion",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="retry an interrupted/failed download or conversion this many times before giving up (default: 3)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=10.0,
        help="seconds to wait between retries (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the audible-cli command that would run, without executing it",
    )
    return parser.parse_args()


def run_cli(args, cwd=None, env=None):
    command = [sys.executable, str(ENTRYPOINT), *args]
    full_env = os.environ.copy()
    full_env.setdefault("AUDIBLE_PLUGIN_DIR", str(PLUGIN_DIR))
    if env:
        full_env.update(env)
    return subprocess.run(command, cwd=cwd or REPO_ROOT, env=full_env)


def run_cli_with_retries(args, cwd, retries, delay, label):
    """Run an audible-cli command, retrying on failure (e.g. a dropped
    connection). Already-downloaded files are detected and skipped by
    audible-cli itself, and partial downloads are resumed where the source
    supports it, so re-running the same command picks up where it left off.
    """
    attempt = 1
    while True:
        result = run_cli(args, cwd=cwd)
        if result.returncode == 0:
            return result

        if attempt >= retries:
            return result

        print(
            f"{label} failed (attempt {attempt}/{retries}), "
            f"retrying in {delay:g}s...",
            file=sys.stderr,
        )
        time.sleep(delay)
        attempt += 1


def fetch_activation_bytes() -> None:
    """Make sure activation bytes are cached in the auth file (needed to
    decrypt AAX files). AAXC files use per-book voucher key/iv instead and
    don't need this, so a failure here is not fatal."""
    print("Fetching activation bytes...")
    result = run_cli(["activation-bytes"])
    if result.returncode != 0:
        print(
            "Warning: could not fetch activation bytes. AAX files will fail "
            "to convert (AAXC files are unaffected).",
            file=sys.stderr,
        )


SOURCE_SUFFIXES = (".aax", ".aaxc", ".voucher")


def convert_book_to_m4b(book_dir: Path, delete_source: bool, retries: int, delay: float) -> bool:
    existing_m4b = set(book_dir.glob("*.m4b"))
    result = run_cli_with_retries(
        ["decrypt", "--all", "--dir", str(book_dir)],
        cwd=book_dir,
        retries=retries,
        delay=delay,
        label=f"Conversion of {book_dir.name}",
    )
    if result.returncode != 0:
        return False

    if delete_source:
        new_m4b = set(book_dir.glob("*.m4b")) - existing_m4b
        if new_m4b:
            for suffix in SOURCE_SUFFIXES:
                for source_file in book_dir.glob(f"*{suffix}"):
                    source_file.unlink()

    return True


def main() -> int:
    args = parse_args()

    library = load_library(args.library_file)
    books = library["books"]
    if not books:
        print(f"No books found in {args.library_file}", file=sys.stderr)
        return 1

    if args.convert and not args.dry_run and not shutil.which("ffmpeg"):
        print(
            "ffmpeg not found on PATH; install it or pass --no-convert.",
            file=sys.stderr,
        )
        return 1

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    base_command = [
        "download",
        "--aax-fallback",
        "--quality", args.quality,
        "--jobs", str(args.jobs),
        "--ignore-errors",
        "--no-confirm",
    ]
    if args.pdf:
        base_command.append("--pdf")
    if args.cover:
        base_command.append("--cover")

    date_range = ", ".join(
        f"{name} {library[key]}"
        for name, key in (("from", "start_date"), ("until", "end_date"))
        if library[key]
    )
    print(f"Downloading {len(books)} book(s) to {dest_dir}"
          + (f" (added {date_range})" if date_range else ""))

    if args.convert and not args.dry_run:
        fetch_activation_bytes()

    download_failures = []
    convert_failures = []
    interrupted = False
    for index, book in enumerate(books, start=1):
        book_dir = dest_dir / book_folder_name(book)
        command = [
            *base_command,
            "--output-dir", str(book_dir),
            "--asin", book["id"],
        ]

        label = f"[{index}/{len(books)}] {book.get('author')} - {book.get('title')}"
        print(label)
        if args.dry_run:
            print(" ".join([sys.executable, str(ENTRYPOINT), *command]))
            continue

        book_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = run_cli_with_retries(
                command,
                cwd=REPO_ROOT,
                retries=args.retries,
                delay=args.retry_delay,
                label=label,
            )
            if result.returncode != 0:
                download_failures.append(book)
                continue

            if args.convert:
                if not convert_book_to_m4b(
                    book_dir, args.delete_source, args.retries, args.retry_delay
                ):
                    convert_failures.append(book)
        except KeyboardInterrupt:
            print("\nInterrupted by user.", file=sys.stderr)
            interrupted = True
            break

    if download_failures:
        print(f"\n{len(download_failures)} book(s) failed to download:", file=sys.stderr)
        for book in download_failures:
            print(f"  - {book.get('author')} - {book.get('title')} ({book.get('id')})", file=sys.stderr)

    if convert_failures:
        print(f"\n{len(convert_failures)} book(s) failed to convert to .m4b:", file=sys.stderr)
        for book in convert_failures:
            print(f"  - {book.get('author')} - {book.get('title')} ({book.get('id')})", file=sys.stderr)

    if interrupted:
        print(
            "\nRe-run this script to resume: already-downloaded files are "
            "detected and skipped automatically.",
            file=sys.stderr,
        )
        return 130

    return 1 if (download_failures or convert_failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
