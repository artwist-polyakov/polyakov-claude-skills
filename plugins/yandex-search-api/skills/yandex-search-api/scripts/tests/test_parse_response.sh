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

# shellcheck disable=SC1091
. "$TESTS_DIR/helpers.sh"

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

# Форма Num взята из документации, а не из живого ответа: строка не должна
# ронять отрисовку (она печатает позицию через %d) уже после оплаченного поиска.
printf '{"docs": [{"Num": "7", "DocumentTitle": "T", "FullUrl": "https://e.ru/", "info_context": "x"}]}' \
    > "$TMP_DIR/strnum.json.in"
parse_search_response "$TMP_DIR/strnum.json.in" > "$TMP_DIR/strnum.json"
check "$TMP_DIR/strnum.json" 'data[0]["position"] == 7 and isinstance(data[0]["position"], int)' \
    "a stringified Num must be coerced to an int"

# Num вовсе нет — позиция берётся из порядка документов.
printf '{"docs": [{"DocumentTitle": "A"}, {"DocumentTitle": "B"}]}' > "$TMP_DIR/nonum.json.in"
parse_search_response "$TMP_DIR/nonum.json.in" > "$TMP_DIR/nonum.json"
check "$TMP_DIR/nonum.json" '[d["position"] for d in data] == [1, 2]' \
    "a missing Num must fall back to the document order"

# Мусор в Num тоже не должен ронять разбор.
printf '{"docs": [{"Num": "abc", "DocumentTitle": "A"}]}' > "$TMP_DIR/badnum.json.in"
parse_search_response "$TMP_DIR/badnum.json.in" > "$TMP_DIR/badnum.json"
check "$TMP_DIR/badnum.json" 'data[0]["position"] == 1' "an unparsable Num must fall back to the order"

# --- живая форма ответа: метаданные лежат в rich_data ---
# Документация показывает Num/DocumentTitle/Description на верхнем уровне
# документа, живой API кладёт их во вложенный rich_data. Пока парсер читал
# только верхний уровень, у КАЖДОГО документа пропадали заголовок и описание,
# а офлайн-тесты этого не видели: фикстура повторяла документацию.
parse_search_response "$TESTS_DIR/fixtures/infocontext_live.json" > "$TMP_DIR/live.json"

check "$TMP_DIR/live.json" 'len(data) == 3' "expected 3 documents from the live-shape JSON"
check "$TMP_DIR/live.json" 'data[0]["title"] == "Об изменениях в главе 26.2 НК РФ"' \
    "DocumentTitle must be read out of rich_data"
check "$TMP_DIR/live.json" 'data[0]["snippet"] == "ФНС России разъясняет порядок уплаты НДС на УСН"' \
    "Description must be read out of rich_data"
check "$TMP_DIR/live.json" '[d["position"] for d in data] == [1, 2, 3]' \
    "Num must be read out of rich_data"
check "$TMP_DIR/live.json" 'data[0]["domain"] == "www.nalog.gov.ru"' \
    "domain must still come from the url"
check "$TMP_DIR/live.json" 'all(d["title"] for d in data)' \
    "no document may end up without a title when the API sent one"

# info_context остаётся на верхнем уровне документа, не в rich_data
check "$TMP_DIR/live.json" '"снижен порог дохода" in data[0]["extract"]' \
    "info_context must be read from the top level"
check "$TMP_DIR/live.json" 'data[2]["extract"] == ""' "an empty info_context must stay empty"

# пустое поле в rich_data не должно выдаваться за значение
check "$TMP_DIR/live.json" 'data[1]["snippet"] == ""' \
    "an empty Description in rich_data must not become a non-empty snippet"

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
