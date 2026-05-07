#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cp "$TEST_DIR/fixtures/sample-source.txt" "$TMP_DIR/source.txt"

uv run --script "$SKILL_DIR/scripts/outline_scout.py" \
    --input "$TMP_DIR/source.txt" \
    --out "$TMP_DIR/outline-scout.json" \
    >/dev/null

uv run python - "$TMP_DIR/outline-scout.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
source = Path(data["source_file"]).read_text(encoding="utf-8")
body_anchor = source.index("Введение\n\nЭтот материал")
start = source.index("Глава 1", body_anchor)
end = source.rindex("Приложение")
data["model_decision"] = {
    "language": "ru",
    "domain": "management",
    "structure_strategy": "body_headings",
    "chapter_patterns": ["^Глава\\s+\\d+"],
    "section_patterns": [],
    "exclude_patterns": ["^Приложение"],
    "content_start_hint": {"char_start": start, "confidence": 0.9},
    "content_end_hint": {"char_end": end, "confidence": 0.9},
    "confidence": 0.9,
    "warnings": [],
}
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY

uv run --script "$SKILL_DIR/scripts/segment_text.py" \
    --input "$TMP_DIR/source.txt" \
    --out "$TMP_DIR/segments" \
    --decision "$TMP_DIR/outline-scout.json" \
    >/dev/null

uv run python - "$TMP_DIR/segments/outline.json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
titles = [seg["title"] for seg in data["segments"]]
assert len(titles) == 2, titles
assert titles[0].startswith("Глава 1"), titles
assert titles[1].startswith("Глава 2"), titles
assert data["model_decision"]["domain"] == "management"
PY
