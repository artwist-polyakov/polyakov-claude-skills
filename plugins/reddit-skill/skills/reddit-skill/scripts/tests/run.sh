#!/bin/sh
# Run all reddit-skill no-network tests.
# Usage: sh scripts/tests/run.sh

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0
FAIL=0
FAILED_NAMES=""

for t in "$TESTS_DIR"/test_*.sh; do
    name=$(basename "$t" .sh)
    printf "  %-40s " "$name"
    if sh "$t" >/dev/null 2>&1; then
        echo "OK"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        FAIL=$((FAIL + 1))
        FAILED_NAMES="${FAILED_NAMES}${name} "
    fi
done

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed: ${FAILED_NAMES}"
    echo "Re-run individually with full output: sh scripts/tests/<name>.sh"
    exit 1
fi
