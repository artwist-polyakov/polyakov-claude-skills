#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

REAL_UV="$(command -v uv)"
mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/uv" <<EOF
#!/bin/sh
if [ "\$1" = "run" ]; then
    shift
    exec "$REAL_UV" run --no-index --no-cache "\$@"
fi
exec "$REAL_UV" "\$@"
EOF
chmod +x "$TMP_DIR/bin/uv"

# Non-PDF formats must not resolve PDF-only uv dependencies. The wrapper forces
# prepare_source.sh to run uv with --no-index and --no-cache, so this fails before
# Python starts if extract_source.py declares pdfminer/PyPDF2 at top level.
PATH="$TMP_DIR/bin:$PATH" \
KNOWLEDGE_COMPILER_CACHE_DIR="$TMP_DIR/cache" \
    sh "$SKILL_DIR/scripts/prepare_source.sh" \
    --input "$TEST_DIR/fixtures/sample-source.txt" \
    --job-id text-no-pdf-deps \
    >/dev/null

test -s "$TMP_DIR/cache/jobs/text-no-pdf-deps/source.txt"
test -s "$TMP_DIR/cache/jobs/text-no-pdf-deps/metadata.json"

uv run python - "$TMP_DIR/cache/jobs/text-no-pdf-deps/metadata.json" <<'PY'
import json
import sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert meta["format"] == "text"
assert meta["extraction_method"] == "plain-text"
PY
