#!/bin/sh
# Offline test runner for yandex-search-api.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
passed=0
failed=0

for test_file in "$TESTS_DIR"/test_*.sh; do
    [ -f "$test_file" ] || continue
    test_name=$(basename "$test_file" .sh)
    printf '%s ... ' "$test_name"
    if sh "$test_file" >/dev/null 2>&1; then
        echo PASS
        passed=$((passed + 1))
    else
        echo FAIL
        failed=$((failed + 1))
        echo "--- $test_name output ---"
        sh "$test_file" 2>&1 || true
        echo "--- end output ---"
    fi
done

echo ""
echo "Results: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
