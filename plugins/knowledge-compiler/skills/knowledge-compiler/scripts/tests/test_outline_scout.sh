#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

uv run --script "$SKILL_DIR/scripts/outline_scout.py" \
    --input "$TEST_DIR/fixtures/sample-source.txt" \
    --out "$TMP_DIR/outline-scout.json" \
    --sample-chars 300 \
    >/dev/null

test -s "$TMP_DIR/outline-scout.json"
test -s "$TMP_DIR/outline-head.txt"
test -s "$TMP_DIR/outline-tail.txt"
test -s "$TMP_DIR/outline-candidates.txt"

uv run python - "$TMP_DIR/outline-scout.json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data["version"] == 1
assert "head_path" in data["samples"]
assert "tail_path" in data["samples"]
assert "head" not in data["samples"]
assert data["script_observations"]["heading_candidates_count"] >= 2
assert data["expected_agent_output"]["domain"]
PY
