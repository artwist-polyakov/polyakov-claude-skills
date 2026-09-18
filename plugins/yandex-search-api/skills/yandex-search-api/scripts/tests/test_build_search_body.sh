#!/bin/sh
# Тело запроса POST /v2/web/search: флаг smart snippets и защита от инъекций.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ysa_body_test.XXXXXX")
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

SEARCH_TYPE="SEARCH_TYPE_RU"
FAMILY_MODE="FAMILY_MODE_MODERATE"
FIX_TYPO="FIX_TYPO_MODE_ON"

# shellcheck disable=SC1091
. "$TESTS_DIR/helpers.sh"

export _YSA_T_KEY="$SMART_SNIPPETS_FLAG_KEY"
export _YSA_T_VALUE="$SMART_SNIPPETS_FLAG_VALUE"

# --- без snippets: metadata в теле быть не должно ---
build_search_body "купить дымоход" "213" "10" "0" "0" > "$TMP_DIR/plain.json"
check "$TMP_DIR/plain.json" '"metadata" not in body' "metadata leaked into a --no-snippets request"
check "$TMP_DIR/plain.json" 'body["groupSpec"]["groupsOnPage"] == 10' "groupsOnPage not carried through"
check "$TMP_DIR/plain.json" 'body["query"]["queryText"] == "купить дымоход"' "queryText mangled"
check "$TMP_DIR/plain.json" 'body["region"] == "213"' "region not carried through"
check "$TMP_DIR/plain.json" 'body["folderId"] == "b1gtestfolder000000"' "folderId not read from config"
check "$TMP_DIR/plain.json" 'body["query"]["searchType"] == "SEARCH_TYPE_RU"' "searchType not carried through"

# --- со snippets: ровно один флаг в metadata.fields ---
build_search_body "купить дымоход" "225" "20" "0" "1" > "$TMP_DIR/snip.json"
check "$TMP_DIR/snip.json" 'list(body["metadata"]["fields"]) == [os.environ["_YSA_T_KEY"]]' \
    "smart snippets flag key missing from metadata.fields"
check "$TMP_DIR/snip.json" \
    'body["metadata"]["fields"][os.environ["_YSA_T_KEY"]] == os.environ["_YSA_T_VALUE"]' \
    "smart snippets flag value wrong"
check "$TMP_DIR/snip.json" 'body["groupSpec"]["groupsOnPage"] == 20' "groupsOnPage not 20 with snippets"

# --- числовые поля остаются числами, а не строками ---
build_search_body "тест" "225" "7" "3" "1" > "$TMP_DIR/nums.json"
check "$TMP_DIR/nums.json" 'body["query"]["page"] == 3 and isinstance(body["query"]["page"], int)' \
    "page must be an int"
check "$TMP_DIR/nums.json" 'isinstance(body["groupSpec"]["groupsOnPage"], int)' \
    "groupsOnPage must be an int"

# --- запрос с кавычками и переводом строки не ломает JSON ---
TRICKY=$(printf 'он сказал "привет"\n{"injected": true}')
export _YSA_T_QUERY="$TRICKY"
build_search_body "$TRICKY" "225" "10" "0" "1" > "$TMP_DIR/tricky.json"
check "$TMP_DIR/tricky.json" 'body["query"]["queryText"] == os.environ["_YSA_T_QUERY"]' \
    "quotes or newlines corrupted the query"
check "$TMP_DIR/tricky.json" '"injected" not in body' "query text escaped into the request body"

# --- folderId читается из конфига один раз на прогон ---
# Тело собирается как _body=$(build_search_body ...), то есть функция целиком
# уходит в подоболочку: кеш, наполненный внутри неё, умирает вместе с ней.
# Греть его обязан сам скрипт, а подоболочки — наследовать готовое значение.
CALLS="$TMP_DIR/cfg_calls"
: > "$CALLS"
cfg_get() { echo "call" >> "$CALLS"; echo "b1gtestfolder000000"; }

_YSA_FOLDER_ID_CACHED=""
ysa_warm_folder_id
for _i in 1 2 3; do
    _body=$(build_search_body "запрос $_i" "225" "10" "0" "1")
done
COUNT=$(wc -l < "$CALLS" | tr -d ' ')
[ "$COUNT" = "1" ] \
    || fail "folderId must be read once per run, got $COUNT config reads for 3 queries"
check_body() { printf '%s' "$_body" > "$TMP_DIR/last.json"; }
check_body
check "$TMP_DIR/last.json" 'body["folderId"] == "b1gtestfolder000000"' \
    "the warmed folderId must still reach the request body"

# Без прогрева тело всё равно обязано собраться: build_search_body сам сходит
# в конфиг, просто без экономии.
_YSA_FOLDER_ID_CACHED=""
: > "$CALLS"
_body=$(build_search_body "без прогрева" "225" "10" "0" "0")
check_body
check "$TMP_DIR/last.json" 'body["folderId"] == "b1gtestfolder000000"' \
    "folderId must be read on demand when the cache was never warmed"

# --- оба скрипта греют кеш ---
grep -q 'ysa_warm_folder_id' "$SKILL_DIR/scripts/web_search_sync.sh" \
    || fail "web_search_sync.sh must warm the folderId cache in the script body"
grep -q 'ysa_warm_folder_id' "$SKILL_DIR/scripts/web_search_async.sh" \
    || fail "web_search_async.sh must warm the folderId cache in the script body"

echo PASS
