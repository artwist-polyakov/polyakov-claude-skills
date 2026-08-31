#!/bin/sh
# Чтение config.json: вложенные ключи, дефолты и булевы значения.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ysa_config_test.XXXXXX")
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

expect() {
    [ "$1" = "$2" ] || { echo "expected '$2', got '$1' ($3)"; exit 1; }
}

expect "$(cfg_get 'yandex_cloud_folder_id')" "b1gtestfolder000000" "top-level key"
expect "$(cfg_get 'search.region_id')" "225" "nested key"
expect "$(cfg_get 'search.smart_snippets.docs')" "20" "three levels deep"

# Булево из JSON должно приезжать как true/false, иначе shell-сравнение
# с "true" молча проваливается и smart snippets тихо выключаются.
expect "$(cfg_get 'search.smart_snippets.enabled')" "true" "json boolean"

# Отсутствующий ключ отдаёт дефолт, а не мусор.
expect "$(cfg_get 'search.no_such_key' 'fallback')" "fallback" "default for a missing key"
expect "$(cfg_get 'search.no_such_key')" "" "empty string without a default"
expect "$(cfg_get 'search.region_id.deeper' 'fallback')" "fallback" "default when the path is too deep"

# Конфиг без секции smart_snippets не должен ронять скрипты: дефолт включён.
printf '{"yandex_cloud_folder_id": "b1g", "search": {}}\n' > "$TMP_DIR/minimal.json"
CONFIG_FILE="$TMP_DIR/minimal.json"
expect "$(cfg_get 'search.smart_snippets.enabled' 'true')" "true" "default for an older config"
expect "$(cfg_get 'search.smart_snippets.docs' '20')" "20" "docs default for an older config"

echo PASS
