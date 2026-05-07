#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "$TMP_DIR/source.txt" <<'EOF'
1. First chapter

Main chapter text with enough words to be a useful segment. It explains a practical idea,
then repeats the idea through a small example and a check.

2. A very long
multiline heading

Second chapter text. The heading is split across lines in the source, but the table of
contents stores it as one line. Segmentation should still find it.

Appendix

Short.
EOF

cat > "$TMP_DIR/decision.json" <<'EOF'
{
  "model_decision": {
    "structure_strategy": "toc_then_body_headings",
    "toc_items": [
      {"label": "1. First chapter", "href": "ch1.xhtml", "level": 1},
      {"label": "2. A very long multiline heading", "href": "ch2.xhtml", "level": 1},
      {"label": "Appendix", "href": "app.xhtml", "level": 1}
    ],
    "confidence": 0.9
  }
}
EOF

uv run --script "$SKILL_DIR/scripts/segment_text.py" \
    --input "$TMP_DIR/source.txt" \
    --out "$TMP_DIR/segments" \
    --decision "$TMP_DIR/decision.json" \
    >/dev/null

uv run python - "$TMP_DIR/segments/outline.json" <<'PY'
import json
import sys
from pathlib import Path

outline = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
titles = [seg["title"] for seg in outline["segments"]]
assert titles == [
    "1. First chapter",
    "2. A very long multiline heading",
    "Appendix",
], titles
assert outline["segments"][-1]["method"] == "toc"
assert outline["segments"][-1]["line_end"] >= outline["segments"][-1]["line_start"]
PY
