#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pdfminer.six>=20231228",
# ]
# ///
"""pdfminer.six PDF extraction helper. Used only after a source is known to be PDF."""

from __future__ import annotations

import sys

from pdfminer.high_level import extract_text


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_pdfminer.py <pdf-path>", file=sys.stderr)
        return 2
    text = extract_text(sys.argv[1])
    if not text.strip():
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
