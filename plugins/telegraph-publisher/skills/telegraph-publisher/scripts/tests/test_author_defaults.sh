#!/bin/sh
# Автор из config/env, приоритет флагов и пустые значения. Сеть не используется.
# Запуск: sh scripts/tests/test_author_defaults.sh

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
TEST_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/tph_author_test.XXXXXX")
trap 'rm -rf "$TEST_TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

TEST_SCRIPTS="$TEST_TMP_DIR/skill/scripts"
TEST_CONFIG="$TEST_TMP_DIR/skill/config/.env"
mkdir -p "$TEST_SCRIPTS" "$TEST_TMP_DIR/skill/config" "$TEST_TMP_DIR/bin" "$TEST_TMP_DIR/tmp"
for script in common.sh create_page.sh edit_page.sh create_account.sh content_converter.py; do
    cp "$SCRIPTS_DIR/$script" "$TEST_SCRIPTS/$script"
done

# Копия скриптов использует только наш .env, а curl лишь записывает argv.
TPH_TEST_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
export TPH_TEST_PYTHON
cat > "$TEST_TMP_DIR/bin/curl" <<'CURL'
#!/bin/sh
exec "$TPH_TEST_PYTHON" - "$@" <<'PY'
import json
import os
import sys

with open(os.environ['TPH_TEST_CURL_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\n')
print(json.dumps({'ok': True, 'result': {
    'url': 'https://telegra.ph/Test-01-01',
    'path': 'Test-01-01',
    'short_name': 'Test',
    'access_token': 'new-test-token',
    'auth_url': 'https://example.invalid/auth',
}}))
PY
CURL
chmod +x "$TEST_TMP_DIR/bin/curl"

PATH="$TEST_TMP_DIR/bin:$PATH"
TMPDIR="$TEST_TMP_DIR/tmp"
PYTHONDONTWRITEBYTECODE=1
export PATH TMPDIR PYTHONDONTWRITEBYTECODE
unset TELEGRAPH_ACCESS_TOKEN TELEGRAPH_AUTHOR_NAME TELEGRAPH_AUTHOR_URL

ABSENT='__ABSENT__'
AUTHOR_DEFAULT='Поляков считает | Про ИИ, рекламу и аналитику данных'
URL_DEFAULT='https://t.me/polyakov_schitaet'

# Все значения здесь — фиксированные тестовые строки без одинарных кавычек.
write_config() {
    {
        if [ "$1" != "$ABSENT" ]; then
            printf "TELEGRAPH_ACCESS_TOKEN='%s'\n" "$1"
        fi
        if [ "$2" != "$ABSENT" ]; then
            printf "TELEGRAPH_AUTHOR_NAME='%s'\n" "$2"
        fi
        if [ "$3" != "$ABSENT" ]; then
            printf "TELEGRAPH_AUTHOR_URL='%s'\n" "$3"
        fi
    } > "$TEST_CONFIG"
}

run_case() {
    TEST_CASE="$1"
    _case_expected="$2"
    _case_script="$3"
    shift 3
    TPH_TEST_CURL_LOG="$TEST_TMP_DIR/$TEST_CASE.requests.jsonl"
    export TPH_TEST_CURL_LOG
    : > "$TPH_TEST_CURL_LOG"
    _case_status=0
    (cd "$TEST_TMP_DIR" && sh "$TEST_SCRIPTS/$_case_script" "$@") \
        > "$TEST_TMP_DIR/$TEST_CASE.out" 2> "$TEST_TMP_DIR/$TEST_CASE.err" || _case_status=$?

    if [ "$_case_expected" = ok ] && [ "$_case_status" -ne 0 ]; then
        printf '%s: неожиданный код выхода %s\n' "$TEST_CASE" "$_case_status" >&2
        cat "$TEST_TMP_DIR/$TEST_CASE.err" >&2
        exit 1
    fi
    if [ "$_case_expected" = fail ]; then
        if [ "$_case_status" -eq 0 ]; then
            printf '%s: ожидалась ошибка\n' "$TEST_CASE" >&2
            exit 1
        fi
        if [ -s "$TPH_TEST_CURL_LOG" ]; then
            printf '%s: curl не должен вызываться при ошибке\n' "$TEST_CASE" >&2
            exit 1
        fi
    fi
}

# Ожидания: key=value — точное значение, !key — поле отсутствует.
assert_requests() {
    python3 - "$TPH_TEST_CURL_LOG" "$TEST_CASE" "$@" <<'PY'
import json
import sys

log_path, label, count, method, *expected = sys.argv[1:]
with open(log_path, encoding='utf-8') as log:
    requests = [json.loads(line) for line in log]

def check(condition, message):
    if not condition:
        raise SystemExit(f'{label}: {message}')

check(len(requests) == int(count), f'ожидалось {count} запросов, получено {len(requests)}')
for number, argv in enumerate(requests, 1):
    check(argv[-1] == f'https://api.telegra.ph/{method}', f'запрос {number}: неверный метод')
    fields = {}
    for index, arg in enumerate(argv):
        if arg not in ('-d', '--data-urlencode'):
            continue
        key, value = argv[index + 1].split('=', 1)
        check(key not in fields, f'запрос {number}: поле {key} передано дважды')
        fields[key] = value
    for item in expected:
        if item.startswith('!'):
            check(item[1:] not in fields, f'запрос {number}: лишнее поле {item[1:]}')
        else:
            key, value = item.split('=', 1)
            check(key in fields, f'запрос {number}: нет поля {key}')
            check(fields[key] == value, f'запрос {number}: {key}={fields[key]!r}, ожидалось {value!r}')
PY
}

# Без настроек автора прежнее поведение сохраняется: поля не отправляются.
write_config test-token "$ABSENT" "$ABSENT"
run_case create_unset ok create_page.sh --title Test --html '<p>Hello</p>'
assert_requests 1 createPage '!author_name' '!author_url'
run_case edit_unset ok edit_page.sh --path Test-01-01 --title Test --html '<p>Hello</p>'
assert_requests 1 editPage/Test-01-01 '!author_name' '!author_url'

# Объявленные поля .env выше окружения — это существующий порядок load_config.
TELEGRAPH_AUTHOR_NAME='Автор из окружения'
TELEGRAPH_AUTHOR_URL='https://example.invalid/environment'
export TELEGRAPH_AUTHOR_NAME TELEGRAPH_AUTHOR_URL
write_config test-token "$AUTHOR_DEFAULT" "$URL_DEFAULT"
run_case create_config ok create_page.sh --title Test --html '<p>Hello</p>'
assert_requests 1 createPage "author_name=$AUTHOR_DEFAULT" "author_url=$URL_DEFAULT"
unset TELEGRAPH_AUTHOR_NAME TELEGRAPH_AUTHOR_URL

# Без .env значения берутся из окружения.
rm -f "$TEST_CONFIG"
TELEGRAPH_ACCESS_TOKEN=test-token
TELEGRAPH_AUTHOR_NAME="$AUTHOR_DEFAULT"
TELEGRAPH_AUTHOR_URL="$URL_DEFAULT"
export TELEGRAPH_ACCESS_TOKEN TELEGRAPH_AUTHOR_NAME TELEGRAPH_AUTHOR_URL
run_case edit_environment ok edit_page.sh --path Test-01-01 --title Test --html '<p>Hello</p>'
assert_requests 1 editPage/Test-01-01 "author_name=$AUTHOR_DEFAULT" "author_url=$URL_DEFAULT"
unset TELEGRAPH_ACCESS_TOKEN TELEGRAPH_AUTHOR_NAME TELEGRAPH_AUTHOR_URL

write_config test-token "$AUTHOR_DEFAULT" "$URL_DEFAULT"
run_case create_name_override ok create_page.sh --title Test --html '<p>Hello</p>' --author-name 'Явный автор'
assert_requests 1 createPage 'author_name=Явный автор' "author_url=$URL_DEFAULT"
run_case edit_url_override ok edit_page.sh --path Test-01-01 --title Test --html '<p>Hello</p>' --author-url 'https://example.invalid/explicit'
assert_requests 1 editPage/Test-01-01 "author_name=$AUTHOR_DEFAULT" 'author_url=https://example.invalid/explicit'

# Пустое значение — явная передача пустого поля, а не возврат к default.
run_case create_empty ok create_page.sh --title Test --html '<p>Hello</p>' --author-name '' --author-url ''
assert_requests 1 createPage 'author_name=' 'author_url='
run_case edit_empty ok edit_page.sh --path Test-01-01 --title Test --html '<p>Hello</p>' --author-name '' --author-url ''
assert_requests 1 editPage/Test-01-01 'author_name=' 'author_url='

# Объявленное пустое поле .env отличается от необъявленного.
write_config test-token '' ''
run_case create_empty_config ok create_page.sh --title Test --html '<p>Hello</p>'
assert_requests 1 createPage 'author_name=' 'author_url='
write_config test-token "$AUTHOR_DEFAULT" "$ABSENT"
run_case edit_name_only ok edit_page.sh --path Test-01-01 --title Test --html '<p>Hello</p>'
assert_requests 1 editPage/Test-01-01 "author_name=$AUTHOR_DEFAULT" '!author_url'
write_config test-token "$AUTHOR_DEFAULT" "$URL_DEFAULT"

# Несколько небольших узлов: суммарно >60 КБ, каждый отдельно меньше лимита.
python3 - "$TEST_TMP_DIR/large.json" <<'PY'
import json
import sys

nodes = [{'tag': 'p', 'children': ['x' * 11000]} for _ in range(6)]
with open(sys.argv[1], 'w', encoding='utf-8') as target:
    json.dump(nodes, target)
PY
run_case split_config ok create_page.sh --title Test --content-file "$TEST_TMP_DIR/large.json"
assert_requests 3 createPage "author_name=$AUTHOR_DEFAULT" "author_url=$URL_DEFAULT"
grep -q '^Parts:     2$' "$TEST_TMP_DIR/split_config.out"
run_case split_empty ok create_page.sh --title Test --content-file "$TEST_TMP_DIR/large.json" --author-name '' --author-url ''
assert_requests 3 createPage 'author_name=' 'author_url='
grep -q '^Parts:     2$' "$TEST_TMP_DIR/split_empty.out"

# Создание аккаунта не требует токена. Публичное имя не обрезается.
write_config "$ABSENT" "$AUTHOR_DEFAULT" "$URL_DEFAULT"
run_case account_config ok create_account.sh
assert_requests 1 createAccount \
    'short_name=Поляков считает | Про ИИ, реклам' \
    "author_name=$AUTHOR_DEFAULT" "author_url=$URL_DEFAULT" '!access_token'

run_case account_override ok create_account.sh --name 'Явный автор' --author-url ''
assert_requests 1 createAccount 'short_name=Явный автор' 'author_name=Явный автор' 'author_url=' '!access_token'
run_case account_empty_name fail create_account.sh --name ''
write_config "$ABSENT" "$ABSENT" "$ABSENT"
run_case account_missing_name fail create_account.sh

# Отзыв токена использует только токен; настройки автора ему не нужны.
# В его PATH нет python3: новая обрезка имени не должна затрагивать --revoke.
NO_PYTHON_BIN="$TEST_TMP_DIR/no-python-bin"
mkdir -p "$NO_PYTHON_BIN"
for command_name in sh dirname mkdir grep head sed cat rm; do
    ln -s "$(command -v "$command_name")" "$NO_PYTHON_BIN/$command_name"
done
ln -s "$TEST_TMP_DIR/bin/curl" "$NO_PYTHON_BIN/curl"
write_config test-token "$AUTHOR_DEFAULT" "$URL_DEFAULT"
TEST_ORIGINAL_PATH="$PATH"
PATH="$NO_PYTHON_BIN"
run_case revoke ok create_account.sh --revoke
PATH="$TEST_ORIGINAL_PATH"
assert_requests 1 revokeAccessToken 'access_token=test-token' '!short_name' '!author_name' '!author_url'
write_config "$ABSENT" "$AUTHOR_DEFAULT" "$URL_DEFAULT"
run_case revoke_without_token fail create_account.sh --revoke

echo PASS
