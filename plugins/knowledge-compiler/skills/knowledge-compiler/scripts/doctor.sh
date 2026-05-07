#!/bin/sh
# Environment check for knowledge-compiler.

set -e

ok() { printf 'OK   %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; }

STATUS=0

UV_BIN="${UV:-uv}"

if command -v "$UV_BIN" >/dev/null 2>&1; then
    ok "uv: $($UV_BIN --version 2>&1)"
else
    fail "uv not found"
    STATUS=1
fi

if command -v pdftotext >/dev/null 2>&1; then
    ok "pdftotext: available"
else
    warn "pdftotext not found; PDF extraction will use Python fallbacks if installed"
fi

if command -v pdfinfo >/dev/null 2>&1; then
    ok "pdfinfo: available"
else
    warn "pdfinfo not found; PDF page count may be approximate"
fi

if command -v "$UV_BIN" >/dev/null 2>&1; then
    ok "python deps: declared in scripts/*.py and installed by uv on first run"
    warn "docling is isolated in extract_docling.py and runs only with --mode technical"
fi

exit "$STATUS"
