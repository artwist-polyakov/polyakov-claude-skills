#!/bin/sh
# Прогнать все офлайн-тесты. Ни сети, ни сервисного аккаунта не нужно.
# Usage: sh scripts/tests/run.sh
#
# Тест обязан закончиться строкой PASS (или SKIP). Упавший `.` внутри sh-скрипта
# умеет вернуть 0, поэтому одного кода возврата для зелёного результата мало.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0
SKIP=0
FAIL=0
FAILED_NAMES=""

for t in "$TESTS_DIR"/test_*.sh; do
    name=$(basename "$t" .sh)
    printf '  %-28s ' "$name"
    if out=$(sh "$t" 2>&1); then
        case "$(printf '%s' "$out" | tail -1)" in
            PASS) echo "OK";   PASS=$((PASS + 1)); continue ;;
            SKIP) echo "SKIP"; SKIP=$((SKIP + 1)); continue ;;
        esac
    fi
    echo "FAIL"
    FAIL=$((FAIL + 1))
    FAILED_NAMES="${FAILED_NAMES}${name} "
    # Без этого падение выглядит как голое FAIL: сообщение теста, ради которого
    # он и писался, оставалось в проглоченной переменной. Хвост — чтобы
    # питоновский traceback не вынес буфер stdout песочницы.
    printf '%s\n' "$out" | tail -20 | sed 's/^/      /'
done

echo ""
echo "Results: ${PASS} passed, ${SKIP} skipped, ${FAIL} failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed: ${FAILED_NAMES}"
    echo "Полный вывод одного теста: sh scripts/tests/<name>.sh"
    exit 1
fi
