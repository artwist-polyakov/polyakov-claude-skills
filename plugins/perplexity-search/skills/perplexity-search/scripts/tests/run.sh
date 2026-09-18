#!/bin/sh
# Run every offline test. No network, no API key required.
# Usage: sh scripts/tests/run.sh
#
# A test must end by printing PASS (or SKIP). A shell that dies early — a failed
# `.` source, for instance — can exit 0 in sh mode, so exit status alone is not
# enough to call a test green.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0
SKIP=0
FAIL=0
FAILED_NAMES=""

for t in "$TESTS_DIR"/test_*.sh; do
    name=$(basename "$t" .sh)
    printf '  %-32s ' "$name"
    if out=$(sh "$t" 2>&1); then
        case "$(printf '%s' "$out" | tail -1)" in
            PASS) echo "OK";   PASS=$((PASS + 1)); continue ;;
            SKIP) echo "SKIP"; SKIP=$((SKIP + 1)); continue ;;
        esac
    fi
    echo "FAIL"
    FAIL=$((FAIL + 1))
    FAILED_NAMES="${FAILED_NAMES}${name} "
done

echo ""
echo "Results: ${PASS} passed, ${SKIP} skipped, ${FAIL} failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed: ${FAILED_NAMES}"
    echo "Re-run one with full output: sh scripts/tests/<name>.sh"
    exit 1
fi
