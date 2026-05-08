#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "PyPDF2>=3.0.0",
# ]
# ///
"""PyPDF2 PDF extraction helper. Used only after a source is known to be PDF."""

from __future__ import annotations

import sys
from pathlib import Path

import PyPDF2


def main() -> int:
    args = sys.argv[1:]
    count_pages = False
    if args and args[0] == "--count-pages":
        count_pages = True
        args = args[1:]
    if len(args) != 1:
        print("Usage: extract_pypdf.py [--count-pages] <pdf-path>", file=sys.stderr)
        return 2

    path = Path(args[0])
    with path.open("rb") as f:
        reader = PyPDF2.PdfReader(f)
        if count_pages:
            print(len(reader.pages))
            return 0
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
    text = "\n\n".join(parts)
    if not text.strip():
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
