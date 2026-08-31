#!/bin/sh
# Разбор ответа Search API: XML обычной выдачи и JSON с инфоконтекстами.

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

# --- обычная выдача: XML ---
parse_search_response "$TESTS_DIR/fixtures/search_response.xml" > "$TMP_DIR/xml.json"

check "$TMP_DIR/xml.json" 'len(data) == 3' "expected 3 documents from XML"
check "$TMP_DIR/xml.json" '[d["position"] for d in data] == [1, 2, 3]' "positions must be 1..3"
check "$TMP_DIR/xml.json" 'data[0]["url"] == "https://example.com/a"' "url not parsed from XML"
check "$TMP_DIR/xml.json" 'data[0]["domain"] == "example.com"' "domain not parsed from XML"
check "$TMP_DIR/xml.json" 'all(d["extract"] == "" for d in data)' \
    "plain SERP must not invent extracts"

# <hlword> внутри заголовка и сниппета склеивается в обычный текст
check "$TMP_DIR/xml.json" 'data[0]["title"] == "Пример дымоход купить"' \
    "hlword not merged into the title"
check "$TMP_DIR/xml.json" 'data[0]["snippet"] == "Короткий сниппет выдачи."' \
    "hlword not merged into the snippet"

# --- инфоконтексты: JSON, тот же формат разбора на выходе ---
parse_search_response "$TESTS_DIR/fixtures/infocontext_response.json" > "$TMP_DIR/ic.json"

check "$TMP_DIR/ic.json" 'len(data) == 3' "expected 3 documents from the infocontext JSON"
check "$TMP_DIR/ic.json" '[d["position"] for d in data] == [1, 2, 3]' "Num must become position"
check "$TMP_DIR/ic.json" 'data[0]["title"] == "Yandex Cloud"' "DocumentTitle must become title"
check "$TMP_DIR/ic.json" 'data[0]["url"] == "https://yandex.cloud/ru/docs"' \
    "FullUrl must become url"
check "$TMP_DIR/ic.json" 'data[0]["snippet"] == "Облачная платформа Yandex Cloud"' \
    "Description must become snippet"
check "$TMP_DIR/ic.json" '"более 100 сервисов" in data[0]["extract"]' \
    "info_context must become extract"
check "$TMP_DIR/ic.json" 'len(data[0]["extract"]) > len(data[0]["snippet"])' \
    "an infocontext must be richer than the description"

# домена в JSON нет — он выводится из URL
check "$TMP_DIR/ic.json" 'data[0]["domain"] == "yandex.cloud"' "domain must be derived from FullUrl"
check "$TMP_DIR/ic.json" 'data[2]["domain"] == "split.example.net"' \
    "query string must not leak into the domain"

# пустой инфоконтекст остаётся пустым, описание не выдаётся за него
check "$TMP_DIR/ic.json" 'data[1]["extract"] == ""' "empty info_context must stay empty"
check "$TMP_DIR/ic.json" 'data[1]["snippet"] == "Описание есть, инфоконтекста нет"' \
    "description lost for a document without an infocontext"
check "$TMP_DIR/ic.json" 'data[2]["snippet"] == ""' "missing Description must be an empty string"

# --- битый ответ не роняет уже оплаченный поиск ---
parse_search_response "$TESTS_DIR/fixtures/broken_response.xml" > "$TMP_DIR/broken.json"
check "$TMP_DIR/broken.json" 'isinstance(data, dict) and "error" in data' \
    "broken XML must produce an error object, not a crash"

printf '{"docs": [{"Num": 1,\n' > "$TMP_DIR/broken.json.in"
parse_search_response "$TMP_DIR/broken.json.in" > "$TMP_DIR/broken2.json"
check "$TMP_DIR/broken2.json" 'isinstance(data, dict) and "error" in data' \
    "broken JSON must produce an error object, not a crash"

# --- пустой массив docs — это ноль результатов, а не ошибка ---
printf '{"docs": []}' > "$TMP_DIR/empty.json.in"
parse_search_response "$TMP_DIR/empty.json.in" > "$TMP_DIR/empty.json"
check "$TMP_DIR/empty.json" 'data == []' "an empty docs[] must parse to an empty list"

echo PASS
