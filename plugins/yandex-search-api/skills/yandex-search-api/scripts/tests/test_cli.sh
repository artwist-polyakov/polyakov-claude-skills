#!/bin/sh
# CLI синхронного поиска: дефолты, --snippets/--no-snippets, потолок документов.
# Ни одного сетевого вызова: YSA_DRY_RUN печатает тело запроса и выходит.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
SYNC="$SKILL_DIR/scripts/web_search_sync.sh"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ysa_cli_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

YSA_CONFIG_FILE="$TESTS_DIR/fixtures/config.json"
YSA_CACHE_DIR="$TMP_DIR/cache"
export YSA_CONFIG_FILE YSA_CACHE_DIR

# shellcheck disable=SC1091
. "$TESTS_DIR/helpers.sh"

# --- без аргументов: подсказка и ненулевой код ---
if sh "$SYNC" > "$TMP_DIR/usage.txt" 2>&1; then
    fail "running without --query must fail"
fi
grep -q -- '--snippets' "$TMP_DIR/usage.txt" || fail "usage must document --snippets"
grep -q -- '--no-snippets' "$TMP_DIR/usage.txt" || fail "usage must document --no-snippets"

# --- неизвестный флаг отвергается ---
if sh "$SYNC" --query q --bogus > "$TMP_DIR/bogus.txt" 2>&1; then
    fail "an unknown option must fail"
fi

# --- дефолт: snippets включены, 20 документов ---
YSA_DRY_RUN=1 sh "$SYNC" --query "купить дымоход" > "$TMP_DIR/default.json" 2>"$TMP_DIR/default.err"
check "$TMP_DIR/default.json" '"metadata" in body' "snippets must be on by default"
check "$TMP_DIR/default.json" 'body["groupSpec"]["groupsOnPage"] == 20' \
    "default must request 20 documents with snippets"

# --- --no-snippets: обычная выдача и results_per_page из конфига ---
YSA_DRY_RUN=1 sh "$SYNC" --query "купить дымоход" --no-snippets > "$TMP_DIR/plain.json"
check "$TMP_DIR/plain.json" '"metadata" not in body' "--no-snippets must drop the flag"
check "$TMP_DIR/plain.json" 'body["groupSpec"]["groupsOnPage"] == 10' \
    "--no-snippets must fall back to search.results_per_page"

# --- явный --results перекрывает дефолт ---
YSA_DRY_RUN=1 sh "$SYNC" --query q --results 5 > "$TMP_DIR/five.json"
check "$TMP_DIR/five.json" 'body["groupSpec"]["groupsOnPage"] == 5' "--results 5 not honoured"

# --- инфоконтексты есть только в русской поисковой базе ---
YSA_DRY_RUN=1 sh "$SYNC" --query q --search-type SEARCH_TYPE_TR \
    > "$TMP_DIR/tr.json" 2>"$TMP_DIR/tr.err"
check "$TMP_DIR/tr.json" '"metadata" not in body' \
    "smart snippets must be dropped for a non-RU search type"
check "$TMP_DIR/tr.json" 'body["query"]["searchType"] == "SEARCH_TYPE_TR"' \
    "the requested search type must be kept, not silently swapped for RU"
grep -q 'SEARCH_TYPE_RU' "$TMP_DIR/tr.err" || fail "the search type conflict must be reported"

# --- --file: образец собирается по первой строке файла, а не по пустому запросу ---
printf '\n  первый запрос  \nвторой запрос\n' > "$TMP_DIR/queries.txt"
YSA_DRY_RUN=1 sh "$SYNC" --file "$TMP_DIR/queries.txt" > "$TMP_DIR/batch.json"
check "$TMP_DIR/batch.json" 'body["query"]["queryText"] == "первый запрос"' \
    "--file dry run must show the first query, not an empty one"

# --- регион и страница доезжают до тела запроса ---
YSA_DRY_RUN=1 sh "$SYNC" --query q --region-id 213 --page 2 > "$TMP_DIR/region.json"
check "$TMP_DIR/region.json" 'body["region"] == "213" and body["query"]["page"] == 2' \
    "--region-id / --page not carried through"

echo PASS
