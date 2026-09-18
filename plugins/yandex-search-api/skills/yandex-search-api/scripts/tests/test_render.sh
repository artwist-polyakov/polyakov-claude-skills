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

# shellcheck disable=SC1091
. "$TESTS_DIR/helpers.sh"

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

# без пака строка батча всё равно обязана назвать файл с результатами,
# иначе после `--file --no-snippets` до ответа не добраться
render_results_table "$TMP_DIR/plain.json" "" "line" "купить дымоход" > "$TMP_DIR/line_plain.txt"
grep -q "$TMP_DIR/plain.json" "$TMP_DIR/line_plain.txt" \
    || fail "line mode must fall back to the JSON path when there is no pack"
[ "$(wc -l < "$TMP_DIR/line_plain.txt" | tr -d ' ')" = "1" ] \
    || fail "line mode must stay at one line without a pack"

# --- пак с нулём выдержек не должен обещать выдержки ---
# Иначе шапка зовёт «читай его вместо повторного поиска» у пака, где читать
# нечего, а вводная обещает «фрагменты страниц», которых не пришло.
ZERO=$(render_snippet_pack "$TMP_DIR/plain.json" "$TMP_DIR/zero.md" "купить дымоход" "225")
[ "$ZERO" = "0" ] || fail "expected 0 documents with extracts, got '$ZERO'"
grep -q 'Инфоконтекстов в ответе не оказалось' "$TMP_DIR/zero.md" \
    || fail "a pack with no extracts must say so"
grep -q 'подготовленные фрагменты' "$TMP_DIR/zero.md" \
    && fail "a pack with no extracts must not promise page fragments"

render_results_table "$TMP_DIR/plain.json" "$TMP_DIR/zero.md" "full" "q" > "$TMP_DIR/zerotab.txt"
grep -q 'выдержек нет, внутри сниппеты выдачи' "$TMP_DIR/zerotab.txt" \
    || fail "the pack line must not advertise an empty pack as a substitute for searching"
grep -q 'читай его вместо повторного поиска' "$TMP_DIR/zerotab.txt" \
    && fail "an empty pack must not be advertised as a substitute for searching"

# а с выдержками — по-прежнему зовёт читать
grep -q 'читай его вместо повторного поиска' "$TMP_DIR/table.txt" \
    || fail "a pack with extracts must still be advertised"

# --- флаг не сработал: пак есть, выдержек ноль ---
# Вид выбирается по запрошенным выдержкам, а не по пришедшим: развёрнутый
# список на 20 документов молча выносит буфер stdout песочницы.
render_results_table "$TMP_DIR/plain.json" "$TMP_DIR/pack.md" "full" "q" > "$TMP_DIR/nofx.txt"
grep -q 'символов' "$TMP_DIR/nofx.txt" \
    || fail "a requested-but-empty snippet run must keep the compact table"
[ "$(wc -l < "$TMP_DIR/nofx.txt" | tr -d ' ')" -le 12 ] \
    || fail "empty-extract output must stay compact"

# развёрнутый вид ограничен жёстче таблицы: три строки на документ
OUT="$TMP_DIR/many.json" python3 -c '
import json, os
docs = [{"position": i, "url": "https://e%d.ru/p" % i, "title": "T%d" % i,
         "snippet": "s" * 200, "domain": "e%d.ru" % i, "extract": ""}
        for i in range(1, 21)]
with open(os.environ["OUT"], "w") as fh:
    json.dump(docs, fh)
'
render_results_table "$TMP_DIR/many.json" "" "full" "q" > "$TMP_DIR/many.txt"
[ "$(wc -l < "$TMP_DIR/many.txt" | tr -d ' ')" -le 45 ] \
    || fail "the verbose listing must stay within the sandbox stdout budget"
grep -q 'ещё 10 результатов' "$TMP_DIR/many.txt" \
    || fail "the verbose listing must say how many results it hid"

# --- лимит печати ---
YSA_PRINT_LIMIT=1 render_results_table "$TMP_DIR/results.json" "" "full" "q" > "$TMP_DIR/limit.txt"
grep -q 'ещё 2 результат' "$TMP_DIR/limit.txt" || fail "print limit not applied or not reported"

# --- ошибка разбора показывается, а не проглатывается ---
printf '{"error": "XML parse error: boom", "raw_saved": true}' > "$TMP_DIR/err.json"
render_results_table "$TMP_DIR/err.json" "" "full" "q" > "$TMP_DIR/err.txt"
grep -q 'Ошибка разбора ответа' "$TMP_DIR/err.txt" || fail "parse error not surfaced"

echo PASS
