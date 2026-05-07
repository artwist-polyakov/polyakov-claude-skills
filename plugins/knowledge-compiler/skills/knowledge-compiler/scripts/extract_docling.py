#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "docling>=2.0.0",
# ]
# ///
"""Docling PDF extraction helper. Used only for explicit technical mode."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_docling.py <pdf-path>", file=sys.stderr)
        return 2

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.do_table_structure = True

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    result = converter.convert(sys.argv[1])
    text = result.document.export_to_markdown()
    if not text.strip():
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
