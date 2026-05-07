#!/bin/sh
# Test runner for knowledge-compiler scripts.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0
FAIL=0
FAILED=""

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found"
    exit 1
fi

for t in "$TESTS_DIR"/test_*.sh; do
    [ -f "$t" ] || continue
    name="$(basename "$t" .sh)"
    printf '%s ... ' "$name"
    if sh "$t" >/dev/null 2>&1; then
        printf 'PASS\n'
        PASS=$((PASS + 1))
    else
        printf 'FAIL\n'
        FAIL=$((FAIL + 1))
        FAILED="$FAILED $name"
        echo "--- output of $name ---"
        sh "$t" 2>&1 || true
        echo "--- end ---"
    fi
done

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed:$FAILED"
    exit 1
fi
