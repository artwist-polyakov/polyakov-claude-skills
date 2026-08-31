#!/bin/sh
# Отрисовка: markdown-пак с инфоконтекстами и компактный индекс в stdout.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ysa_render_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

YSA_SCRIPT_DIR="$SKILL_DIR/scripts"
YSA_SKILL_DIR="$SKILL_DIR"
YSA_CONFIG_FILE="$TESTS_DIR/fixtures/config.json"
YSA_CACHE_DIR="$TMP_DIR/cache"
export YSA_SCRIPT_DIR YSA_SKILL_DIR YSA_CONFIG_FILE YSA_CACHE_DIR

# shellcheck disable=SC1091
. "$SKILL_DIR/scripts/common.sh"

parse_search_response "$TESTS_DIR/fixtures/infocontext_response.json" > "$TMP_DIR/results.json"
parse_search_response "$TESTS_DIR/fixtures/search_response.xml" > "$TMP_DIR/plain.json"

fail() { echo "$1"; exit 1; }

# --- markdown-пак ---
COUNT=$(render_snippet_pack "$TMP_DIR/results.json" "$TMP_DIR/pack.md" "Yandex Cloud" "225")
[ "$COUNT" = "2" ] || fail "expected 2 documents with extracts, got '$COUNT'"

head -1 "$TMP_DIR/pack.md" | grep -q '^# Yandex Cloud$' || fail "pack title missing"
grep -q 'Регион 225 · документов: 3 · с выдержками: 2' "$TMP_DIR/pack.md" || fail "pack header wrong"
grep -q '^## 1\. Yandex Cloud$' "$TMP_DIR/pack.md" || fail "document heading missing"
grep -q '^https://yandex.cloud/ru/docs$' "$TMP_DIR/pack.md" || fail "document url missing"
grep -q 'более 100 сервисов' "$TMP_DIR/pack.md" || fail "infocontext text missing from the pack"
grep -q 'Compute Cloud — сервис виртуальных машин' "$TMP_DIR/pack.md" || fail "third extract missing"

# документ без инфоконтекста честно помечен, а не выдан за инфоконтекст
grep -q 'Выдержка недоступна' "$TMP_DIR/pack.md" || fail "missing extract not flagged in the pack"
grep -q 'Описание есть, инфоконтекста нет' "$TMP_DIR/pack.md" || fail "fallback snippet missing"

# --- индекс в stdout: с инфоконтекстами ---
render_results_table "$TMP_DIR/results.json" "$TMP_DIR/pack.md" "full" "Yandex Cloud" \
    > "$TMP_DIR/table.txt"
grep -q 'символов' "$TMP_DIR/table.txt" || fail "table header missing"
grep -q 'yandex.cloud' "$TMP_DIR/table.txt" || fail "domain missing from the table"
grep -q 'Всего: 3, с выдержками: 2' "$TMP_DIR/table.txt" || fail "totals line wrong"
grep -q "$TMP_DIR/pack.md" "$TMP_DIR/table.txt" || fail "pack path not printed"

# тексты инфоконтекстов в stdout не уходят — иначе песочница молча обрежет вывод
grep -q 'более 100 сервисов' "$TMP_DIR/table.txt" && fail "extract text leaked into stdout"
[ "$(wc -l < "$TMP_DIR/table.txt" | tr -d ' ')" -le 12 ] || fail "table is too tall for one query"

# --- индекс в stdout: без инфоконтекстов остаётся прежний вид со ссылками ---
render_results_table "$TMP_DIR/plain.json" "" "full" "купить дымоход" > "$TMP_DIR/plain.txt"
grep -q 'символов' "$TMP_DIR/plain.txt" && fail "snippet table shown for a plain search"
grep -q 'https://example.com/a' "$TMP_DIR/plain.txt" || fail "url must stay in stdout without snippets"
grep -q 'Всего: 3, с выдержками: 0' "$TMP_DIR/plain.txt" || fail "totals line wrong without snippets"

# --- режим одной строки для батча ---
render_results_table "$TMP_DIR/results.json" "$TMP_DIR/pack.md" "line" "Yandex Cloud" \
    > "$TMP_DIR/line.txt"
[ "$(wc -l < "$TMP_DIR/line.txt" | tr -d ' ')" = "1" ] || fail "line mode must print exactly one line"
grep -q 'Yandex Cloud' "$TMP_DIR/line.txt" || fail "line mode must name the query"
grep -q 'выдержек  2' "$TMP_DIR/line.txt" || fail "line mode must report the extract count"

# --- лимит печати ---
YSA_PRINT_LIMIT=1 render_results_table "$TMP_DIR/results.json" "" "full" "q" > "$TMP_DIR/limit.txt"
grep -q 'ещё 2 результат' "$TMP_DIR/limit.txt" || fail "print limit not applied or not reported"

# --- ошибка разбора показывается, а не проглатывается ---
printf '{"error": "XML parse error: boom", "raw_saved": true}' > "$TMP_DIR/err.json"
render_results_table "$TMP_DIR/err.json" "" "full" "q" > "$TMP_DIR/err.txt"
grep -q 'Ошибка разбора ответа' "$TMP_DIR/err.txt" || fail "parse error not surfaced"

echo PASS
