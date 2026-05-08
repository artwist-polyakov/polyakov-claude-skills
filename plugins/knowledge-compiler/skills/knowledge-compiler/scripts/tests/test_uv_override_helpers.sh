#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAKE_UV="$TMP_DIR/custom-uv"
cat > "$FAKE_UV" <<'EOF'
#!/bin/sh
test "$1" = "run"
test "$2" = "--script"
printf 'helper=%s arg=%s\n' "$3" "$4"
EOF
chmod +x "$FAKE_UV"

uv run python - "$SKILL_DIR/scripts/extract_source.py" "$FAKE_UV" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

module_path = Path(sys.argv[1])
fake_uv = sys.argv[2]
spec = importlib.util.spec_from_file_location("extract_source", module_path)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

os.environ["UV"] = fake_uv
os.environ["PATH"] = "/usr/bin:/bin"
result = mod.run_uv_helper(Path("/tmp/helper.py"), "payload", timeout=10)
assert result is not None
assert result.strip() == "helper=/tmp/helper.py arg=payload", result
PY
