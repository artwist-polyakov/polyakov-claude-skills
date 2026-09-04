#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
UV_BIN="${UV:-uv}"
exec "$UV_BIN" run --script "$TEST_DIR/test_quality_gate.py"
