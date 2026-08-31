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

# check <json-file> <python-expr> <message-on-failure>
# Выражение видит тело запроса как `body` и окружение как `os.environ`.
check() {
    _chk_file="$1"
    _chk_expr="$2"
    _chk_msg="$3"
    if ! _YSA_T_FILE="$_chk_file" _YSA_T_EXPR="$_chk_expr" python3 -c '
import json, os, sys
with open(os.environ["_YSA_T_FILE"], encoding="utf-8") as fh:
    body = json.load(fh)
sys.exit(0 if eval(os.environ["_YSA_T_EXPR"]) else 1)
'; then
        echo "$_chk_msg"
        exit 1
    fi
}

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

echo PASS
