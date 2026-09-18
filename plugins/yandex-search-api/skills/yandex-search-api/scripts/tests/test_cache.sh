#!/bin/sh
# Гигиена кеша: пак не переживает прогон, который инфоконтекстов не принёс.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ysa_cache_test.XXXXXX")
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

mkdir -p "$CACHE_DIR/results"

# Хэш зависит только от текста запроса, поэтому один и тот же запрос,
# выполненный без инфоконтекстов (--no-snippets или асинхронно), обязан снести
# пак от прошлого раза: иначе агент прочитает вчерашние выдержки как свежие.
HASH=$(file_hash "купить дымоход")
[ -n "$HASH" ] || fail "file_hash returned nothing"
[ "$HASH" = "$(file_hash "купить дымоход")" ] || fail "file_hash must be stable for one query"
[ "$HASH" != "$(file_hash "другой запрос")" ] || fail "different queries must hash differently"

PACK="$CACHE_DIR/results/$HASH.md"
printf '# вчерашний пак\n' > "$PACK"
[ -f "$PACK" ] || fail "fixture pack was not created"

drop_stale_pack "$HASH"
[ ! -f "$PACK" ] || fail "a stale pack must be removed"

# Повторный вызов на уже удалённом паке не должен падать: скрипты зовут его
# на каждом прогоне без инфоконтекстов, в том числе на первом.
drop_stale_pack "$HASH"

# Чужие файлы под тем же хэшем трогать нельзя — это сырой ответ и разбор.
printf 'raw' > "$CACHE_DIR/results/$HASH.raw"
printf '[]' > "$CACHE_DIR/results/$HASH.json"
drop_stale_pack "$HASH"
[ -f "$CACHE_DIR/results/$HASH.raw" ] || fail "drop_stale_pack must not touch .raw"
[ -f "$CACHE_DIR/results/$HASH.json" ] || fail "drop_stale_pack must not touch .json"

# --- оба скрипта действительно зовут уборку ---
grep -q 'drop_stale_pack' "$SKILL_DIR/scripts/web_search_sync.sh" \
    || fail "web_search_sync.sh must drop a stale pack when it produced none"
grep -q 'drop_stale_pack' "$SKILL_DIR/scripts/web_search_async.sh" \
    || fail "web_search_async.sh must drop a stale pack: async never returns infocontexts"

# Уборка обязана стоять до запроса: у search_single два ранних `return 1`
# (упавший вызов и ответ без rawData), и после них до поздней уборки дело
# не дойдёт — пак от прошлого раза останется лежать как свежий.
DROP_LINE=$(grep -n 'drop_stale_pack' "$SKILL_DIR/scripts/web_search_sync.sh" | head -1 | cut -d: -f1)
CALL_LINE=$(grep -n 'auth_request' "$SKILL_DIR/scripts/web_search_sync.sh" | head -1 | cut -d: -f1)
[ -n "$DROP_LINE" ] && [ -n "$CALL_LINE" ] || fail "could not locate the drop / request lines"
[ "$DROP_LINE" -lt "$CALL_LINE" ] \
    || fail "drop_stale_pack must run before the API call, not only after a successful parse"

echo PASS
