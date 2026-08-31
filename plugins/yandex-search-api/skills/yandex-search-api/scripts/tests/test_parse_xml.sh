#!/bin/sh
# Разбор XML-ответа: выдержки smart snippets, обратная совместимость, ошибки.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ysa_parse_test.XXXXXX")
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

# Фикстура помечает выдержки собственным тегом: настоящее имя живёт в
# SMART_SNIPPETS_XML_TAG и проверяется на живом API, а здесь проверяется механика.
SMART_SNIPPETS_XML_TAG="ysa-test-extract"

check() {
    _chk_file="$1"
    _chk_expr="$2"
    _chk_msg="$3"
    if ! _YSA_T_FILE="$_chk_file" _YSA_T_EXPR="$_chk_expr" python3 -c '
import json, os, sys
with open(os.environ["_YSA_T_FILE"], encoding="utf-8") as fh:
    data = json.load(fh)
sys.exit(0 if eval(os.environ["_YSA_T_EXPR"]) else 1)
'; then
        echo "$_chk_msg"
        exit 1
    fi
}

# --- разбор с выдержками ---
parse_search_xml "$TESTS_DIR/fixtures/search_response.xml" > "$TMP_DIR/results.json"

check "$TMP_DIR/results.json" 'len(data) == 3' "expected 3 documents"
check "$TMP_DIR/results.json" '[d["position"] for d in data] == [1, 2, 3]' "positions must be 1..3"
check "$TMP_DIR/results.json" 'data[0]["url"] == "https://example.com/a"' "url not parsed"
check "$TMP_DIR/results.json" 'data[0]["domain"] == "example.com"' "domain not parsed"

# <hlword> внутри заголовка склеивается в обычный текст
check "$TMP_DIR/results.json" 'data[0]["title"] == "Пример дымоход купить"' \
    "hlword not merged into the title"
check "$TMP_DIR/results.json" 'data[0]["snippet"] == "Короткий сниппет выдачи."' \
    "hlword not merged into the snippet"

# выдержка попадает в extract целиком
check "$TMP_DIR/results.json" '"годится для цитирования" in data[0]["extract"]' \
    "extract text missing"
check "$TMP_DIR/results.json" 'len(data[0]["extract"]) > len(data[0]["snippet"])' \
    "extract must be richer than the snippet"

# документ без выдержки: extract пустой, сниппет на месте
check "$TMP_DIR/results.json" 'data[1]["extract"] == ""' "missing extract must be an empty string"
check "$TMP_DIR/results.json" 'data[1]["snippet"].startswith("Только обычный сниппет")' \
    "snippet lost for a document without an extract"

# выдержка из нескольких элементов склеивается через пустую строку
check "$TMP_DIR/results.json" 'data[2]["extract"] == "Кусок один.\n\nКусок два."' \
    "multi-part extract not joined"

# --- обратная совместимость: без тега выдержек всё работает как раньше ---
SMART_SNIPPETS_XML_TAG=""
parse_search_xml "$TESTS_DIR/fixtures/search_response.xml" > "$TMP_DIR/plain.json"
check "$TMP_DIR/plain.json" 'len(data) == 3' "expected 3 documents without the snippet tag"
check "$TMP_DIR/plain.json" 'all(d["extract"] == "" for d in data)' \
    "extracts must be empty when the tag is not configured"
check "$TMP_DIR/plain.json" 'all(d["title"] and d["url"] for d in data)' \
    "titles and urls must survive without the snippet tag"

# --- битый XML не роняет уже оплаченный поиск ---
parse_search_xml "$TESTS_DIR/fixtures/broken_response.xml" > "$TMP_DIR/broken.json"
check "$TMP_DIR/broken.json" 'isinstance(data, dict) and "error" in data' \
    "broken XML must produce an error object, not a crash"

echo PASS
