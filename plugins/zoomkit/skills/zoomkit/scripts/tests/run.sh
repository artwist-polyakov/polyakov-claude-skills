#!/bin/sh
# Запуск всех автономных проверок ZoomKit. Сеть и настоящий ключ не нужны.

set -e

TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PASSED=0
FAILED=0
FAILED_NAMES=""

for TEST_FILE in "$TESTS_DIR"/test_*.sh; do
    TEST_NAME=$(basename "$TEST_FILE" .sh)
    printf '  %-28s ' "$TEST_NAME"
    if TEST_OUTPUT=$(sh "$TEST_FILE" 2>&1) && [ "$(printf '%s' "$TEST_OUTPUT" | tail -1)" = PASS ]; then
        printf '%s\n' "OK"
        PASSED=$((PASSED + 1))
    else
        printf '%s\n' "FAIL"
        printf '%s\n' "$TEST_OUTPUT"
        FAILED=$((FAILED + 1))
        FAILED_NAMES="$FAILED_NAMES $TEST_NAME"
    fi
done

printf '\nПроверки: %s успешно, %s с ошибкой.\n' "$PASSED" "$FAILED"
if [ "$FAILED" -gt 0 ]; then
    printf 'Ошибки:%s\n' "$FAILED_NAMES" >&2
    exit 1
fi
