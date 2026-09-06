#!/bin/sh
# Автономная проверка команд, авторизации и предохранителей.

set -e

TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SKILL_DIR=$(CDPATH= cd -- "$TESTS_DIR/../.." && pwd)
ZOOMKIT_SCRIPT="$SKILL_DIR/scripts/zoomkit.sh"
FAKE_CURL="$TESTS_DIR/fake_curl.sh"
TEST_TMP=$(mktemp -d "${TMPDIR:-/tmp}/zoomkit_test.XXXXXX")
mkdir -p "$TEST_TMP/request-tmp"
trap 'rm -rf "$TEST_TMP"' 0
trap 'exit 130' 2
trap 'exit 143' 15

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

assert_contains() {
    _ztac_file=$1
    _ztac_text=$2
    grep -F -- "$_ztac_text" "$_ztac_file" >/dev/null 2>&1 || fail "в выводе нет: $_ztac_text"
}

assert_not_contains() {
    _ztanc_file=$1
    _ztanc_text=$2
    if grep -F -- "$_ztanc_text" "$_ztanc_file" >/dev/null 2>&1; then
        fail "в вывод попал секрет: $_ztanc_text"
    fi
}

assert_before() {
    _ztab_file=$1
    _ztab_first=$2
    _ztab_second=$3
    _ztab_first_line=$(awk -v needle="$_ztab_first" 'index($0, needle) { print NR; exit }' "$_ztab_file")
    _ztab_second_line=$(awk -v needle="$_ztab_second" 'index($0, needle) { print NR; exit }' "$_ztab_file")
    [ -n "$_ztab_first_line" ] && [ -n "$_ztab_second_line" ] && \
        [ "$_ztab_first_line" -lt "$_ztab_second_line" ] || \
        fail "нарушен порядок: $_ztab_first → $_ztab_second"
}

run_zoomkit() {
    if [ "${ZOOMKIT_API_TOKEN+x}" = x ]; then
        export ZOOMKIT_API_TOKEN
    fi
    if [ "${MOCK_BODY_FILE+x}" = x ]; then
        export MOCK_BODY_FILE
    fi
    if [ "${MOCK_STATUS+x}" = x ]; then
        export MOCK_STATUS
    fi
    if [ "${MOCK_RATE_RESET+x}" = x ]; then
        export MOCK_RATE_RESET
    fi
    if [ "${MOCK_REPORT_SEQUENCE_DIR+x}" = x ]; then
        export MOCK_REPORT_SEQUENCE_DIR
    fi
    if [ "${MOCK_REPORT_COUNTER_FILE+x}" = x ]; then
        export MOCK_REPORT_COUNTER_FILE
    fi
    ZOOMKIT_CONFIG_FILE="$TEST_TMP/config.env" \
    ZOOMKIT_CACHE_DIR="$TEST_TMP/cache" \
    ZOOMKIT_API_BASE_URL="https://api.example.test/v1" \
    ZOOMKIT_ALLOW_CUSTOM_API_BASE=1 \
    ZOOMKIT_CURL_BIN="$FAKE_CURL" \
    MOCK_LOG="$TEST_TMP/curl.log" \
    TMPDIR="$TEST_TMP/request-tmp" \
    sh "$ZOOMKIT_SCRIPT" "$@"
}

assert_write_guard() {
    _ztwg_name=$1
    shift
    rm -f "$TEST_TMP/curl.log"
    if ZOOMKIT_API_TOKEN="test-token" run_zoomkit "$@" > "$TEST_TMP/out" 2>&1; then
        fail "$_ztwg_name: изменение без --confirm было разрешено"
    fi
    assert_contains "$TEST_TMP/out" "--confirm"
    [ ! -e "$TEST_TMP/curl.log" ] || fail "$_ztwg_name: заблокированное изменение дошло до curl"
}

assert_dry_route() {
    _ztdr_method=$1
    _ztdr_path=$2
    shift 2
    rm -f "$TEST_TMP/curl.log"
    ZOOMKIT_API_TOKEN= run_zoomkit "$@" --dry-run > "$TEST_TMP/out" 2>&1
    assert_contains "$TEST_TMP/out" "Метод: $_ztdr_method"
    assert_contains "$TEST_TMP/out" "Адрес: https://api.example.test/v1$_ztdr_path"
    [ ! -e "$TEST_TMP/curl.log" ] || fail "$*: --dry-run вызвал curl"
}

# Запрос подключённых проектов без ключа: понятная инструкция и ни одного сетевого вызова.
rm -f "$TEST_TMP/config.env" "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN= run_zoomkit clients > "$TEST_TMP/out" 2>&1; then
    fail "запрос проектов без ключа завершился успешно"
fi
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/profile/api"
assert_contains "$TEST_TMP/out" "ZoomKit помогает вести рекламу"
assert_contains "$TEST_TMP/out" "50 руб./сутки + 2 руб./сутки"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/register"
assert_contains "$TEST_TMP/out" "можно начать с 500 руб."
assert_contains "$TEST_TMP/out" "cp config/.env.example config/.env"
assert_contains "$TEST_TMP/out" "chmod 600 config/.env"
assert_contains "$TEST_TMP/out" "config/.env сохраняется между сессиями"
assert_before "$TEST_TMP/out" "https://zoomkit.ru/register" "можно начать с 500 руб."
assert_before "$TEST_TMP/out" "можно начать с 500 руб." "https://zoomkit.ru/profile/api"
assert_before "$TEST_TMP/out" "https://zoomkit.ru/profile/api" "config/.env сохраняется между сессиями"
assert_before "$TEST_TMP/out" "config/.env сохраняется между сессиями" "sh scripts/zoomkit.sh token"
[ ! -e "$TEST_TMP/curl.log" ] || fail "без ключа был вызван curl"

# Первое знакомство с пользой и тарифами доступно без аккаунта, ключа и сети.
rm -f "$TEST_TMP/curl.log"
ZOOMKIT_API_TOKEN= run_zoomkit getting-started > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "Почему сервис может быть полезен"
assert_contains "$TEST_TMP/out" "5 руб./сутки за рекламный канал"
assert_contains "$TEST_TMP/out" "50 руб./сутки за аккаунт"
assert_contains "$TEST_TMP/out" "30 руб./сутки за сообщество"
assert_contains "$TEST_TMP/out" "около 1560 руб. за 30 дней"
assert_contains "$TEST_TMP/out" "добавляемых при пополнении 6%"
assert_contains "$TEST_TMP/out" "точный общий расход показывает команда balance"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/help"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/register"
assert_contains "$TEST_TMP/out" "можно начать с 500 руб."
assert_contains "$TEST_TMP/out" "config/.env сохраняется между сессиями"
[ ! -e "$TEST_TMP/curl.log" ] || fail "первое знакомство вызвало curl"

# Справка о возможностях счетов доступна без ключа и без сети.
rm -f "$TEST_TMP/curl.log"
ZOOMKIT_CONFIG_FILE="$TEST_TMP/missing.env" sh "$ZOOMKIT_SCRIPT" billing-capabilities > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "недоступно:  создание счета"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/profile"
[ ! -e "$TEST_TMP/curl.log" ] || fail "справка о возможностях вызвала curl"

# Заглушка из примера тоже не считается ключом.
printf '%s\n' 'ZOOMKIT_API_TOKEN="your_api_token_here"' > "$TEST_TMP/config.env"
rm -f "$TEST_TMP/curl.log"
if (unset ZOOMKIT_API_TOKEN; run_zoomkit token) > "$TEST_TMP/out" 2>&1; then
    fail "заглушка была принята за ключ"
fi
[ ! -e "$TEST_TMP/curl.log" ] || fail "с заглушкой был вызван curl"

# Значения .env разбираются как данные: команды не исполняются, посторонние ключи запрещены.
SENTINEL="$TEST_TMP/config-command-was-run"
printf 'ZOOMKIT_API_TOKEN="$(touch %s)"\n' "$SENTINEL" > "$TEST_TMP/config.env"
(unset ZOOMKIT_API_TOKEN; run_zoomkit token) > "$TEST_TMP/out" 2>&1
[ ! -e "$SENTINEL" ] || fail "команда из config/.env была исполнена"

{
    printf '%s\n' 'ZOOMKIT_API_TOKEN="file-token"'
    printf '%s\n' 'CONFIRM=1'
} > "$TEST_TMP/config.env"
rm -f "$TEST_TMP/curl.log"
if (unset ZOOMKIT_API_TOKEN; run_zoomkit token) > "$TEST_TMP/out" 2>&1; then
    fail "посторонний параметр config/.env был принят"
fi
assert_contains "$TEST_TMP/out" "неизвестный параметр CONFIRM"
[ ! -e "$TEST_TMP/curl.log" ] || fail "ошибка config/.env дошла до curl"

# Ключ из окружения имеет приоритет, запрос содержит нужные заголовки.
printf '%s\n' 'ZOOMKIT_API_TOKEN="file-token"' > "$TEST_TMP/config.env"
ZOOMKIT_API_TOKEN="environment-token" run_zoomkit token > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/curl.log" "METHOD=GET"
assert_contains "$TEST_TMP/curl.log" "URL=https://api.example.test/v1/token"
assert_contains "$TEST_TMP/curl.log" "AUTH=Authorization: Bearer environment-token"
assert_contains "$TEST_TMP/curl.log" "ACCEPT=Accept: application/json"
assert_contains "$TEST_TMP/curl.log" "CURLRC_DISABLED=1"
assert_contains "$TEST_TMP/curl.log" "CURLRC_FIRST=1"
assert_contains "$TEST_TMP/curl.log" "REQUEST_HEADERS_MODE=600"
assert_not_contains "$TEST_TMP/out" "environment-token"

# config/.env остаётся основным хранилищем между отдельными запусками и из другого каталога.
mkdir -p "$TEST_TMP/other-cwd"
rm -f "$TEST_TMP/curl.log"
(unset ZOOMKIT_API_TOKEN; run_zoomkit token) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/curl.log" "AUTH=Authorization: Bearer file-token"
assert_not_contains "$TEST_TMP/out" "file-token"

rm -f "$TEST_TMP/curl.log"
(cd "$TEST_TMP/other-cwd" && unset ZOOMKIT_API_TOKEN && run_zoomkit token) > "$TEST_TMP/out-second" 2>&1
assert_contains "$TEST_TMP/curl.log" "AUTH=Authorization: Bearer file-token"
assert_not_contains "$TEST_TMP/out-second" "file-token"

# Пустая переменная окружения не скрывает сохранённый ключ.
rm -f "$TEST_TMP/curl.log"
ZOOMKIT_API_TOKEN= run_zoomkit token > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/curl.log" "AUTH=Authorization: Bearer file-token"
assert_not_contains "$TEST_TMP/out" "file-token"

# Список клиентов не теряет ошибки, комментарии и логины без кабинетов.
printf '%s\n' '{"yandex.direct":[{"id":7,"type":"yandex.direct","name":"login-empty","errors":"нужно обновить доступ","clients":[]},{"id":8,"type":"yandex.direct","name":"login-main","errors":"","clients":[{"id":81,"name":"cabinet-one","comment":"основной кабинет"}]}]}' > "$TEST_TMP/clients.json"
(MOCK_BODY_FILE="$TEST_TMP/clients.json" ZOOMKIT_API_TOKEN="test-token" run_zoomkit clients) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "login-empty"
assert_contains "$TEST_TMP/out" "нужно обновить доступ"
assert_contains "$TEST_TMP/out" "cabinet-one"
assert_contains "$TEST_TMP/out" "основной кабинет"
assert_contains "$TEST_TMP/out" "Проектов/аккаунтов: 1"
assert_contains "$TEST_TMP/out" "Строк таблицы: 2"
assert_contains "$TEST_TMP/out" "API не показывает, какой кабинет включён в списания"

# Перевод строки в ключе не может добавить произвольный HTTP-заголовок.
TOKEN_WITH_NEWLINE=$(printf 'test-token\nX-Injected: value')
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN="$TOKEN_WITH_NEWLINE" run_zoomkit token > "$TEST_TMP/out" 2>&1; then
    fail "ключ с переводом строки был принят"
fi
assert_contains "$TEST_TMP/out" "не должен содержать перевод строки"
[ ! -e "$TEST_TMP/curl.log" ] || fail "ключ с переводом строки дошёл до curl"

# Команда без тела не может незаметно отправить локальный файл.
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN="test-token" run_zoomkit balance --body /etc/hosts > "$TEST_TMP/out" 2>&1; then
    fail "команда balance приняла --body"
fi
assert_contains "$TEST_TMP/out" "не принимает --body"
[ ! -e "$TEST_TMP/curl.log" ] || fail "запрещённое тело дошло до curl"

# Ключ нельзя отправить на произвольный HTTP-адрес.
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_CONFIG_FILE="$TEST_TMP/missing.env" \
    ZOOMKIT_API_TOKEN="test-token" \
    ZOOMKIT_API_BASE_URL="http://collector.example/v1" \
    ZOOMKIT_ALLOW_CUSTOM_API_BASE=1 \
    ZOOMKIT_CURL_BIN="$FAKE_CURL" \
    MOCK_LOG="$TEST_TMP/curl.log" \
    sh "$ZOOMKIT_SCRIPT" token > "$TEST_TMP/out" 2>&1; then
    fail "произвольный HTTP-адрес был разрешён"
fi
assert_contains "$TEST_TMP/out" "нельзя отправлять по незащищённому HTTP"
[ ! -e "$TEST_TMP/curl.log" ] || fail "запрещённый HTTP-адрес дошёл до curl"

# Userinfo не позволяет замаскировать внешний узел под localhost.
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_CONFIG_FILE="$TEST_TMP/missing.env" \
    ZOOMKIT_API_TOKEN="test-token" \
    ZOOMKIT_API_BASE_URL="http://localhost:80@collector.example/v1" \
    ZOOMKIT_ALLOW_CUSTOM_API_BASE=1 \
    ZOOMKIT_ALLOW_INSECURE_HTTP=1 \
    ZOOMKIT_CURL_BIN="$FAKE_CURL" \
    MOCK_LOG="$TEST_TMP/curl.log" \
    sh "$ZOOMKIT_SCRIPT" token > "$TEST_TMP/out" 2>&1; then
    fail "HTTP-адрес с userinfo был разрешён"
fi
assert_contains "$TEST_TMP/out" "не должен содержать пустой хост или userinfo"
[ ! -e "$TEST_TMP/curl.log" ] || fail "HTTP-адрес с userinfo дошёл до curl"

# Незащищённый адрес можно включить только для явной локальной проверки.
ZOOMKIT_CONFIG_FILE="$TEST_TMP/missing.env" \
ZOOMKIT_API_TOKEN= \
ZOOMKIT_API_BASE_URL="http://127.0.0.1:8765/v1" \
ZOOMKIT_ALLOW_CUSTOM_API_BASE=1 \
ZOOMKIT_ALLOW_INSECURE_HTTP=1 \
sh "$ZOOMKIT_SCRIPT" token --dry-run > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "Адрес: http://127.0.0.1:8765/v1/token"

# Счета читаются, фильтруются локально и сохраняются полностью.
printf '%s\n' "не менять" > "$TEST_TMP/symlink-target"
mkdir -p "$TEST_TMP/cache"
ln -s "$TEST_TMP/symlink-target" "$TEST_TMP/cache/latest-invoices.json"
ZOOMKIT_API_TOKEN="test-token" run_zoomkit invoices --status wait --limit 1 > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "ZK/2"
assert_contains "$TEST_TMP/out" "ожидает оплаты"
if grep -F -- "ZK/1" "$TEST_TMP/out" >/dev/null 2>&1; then
    fail "локальный фильтр счетов не применился"
fi
[ -s "$TEST_TMP/cache/latest-invoices.json" ] || fail "полный список счетов не сохранён"
[ ! -L "$TEST_TMP/cache/latest-invoices.json" ] || fail "файл ответа остался символической ссылкой"
[ "$(cat "$TEST_TMP/symlink-target")" = "не менять" ] || fail "запись прошла по символической ссылке"
if [ "$(find "$TEST_TMP/request-tmp" -mindepth 1 -print | wc -l | tr -d ' ')" -ne 0 ]; then
    fail "временный каталог запроса не очищен"
fi
if MODE=$(stat -f '%Lp' "$TEST_TMP/cache/latest-invoices.json" 2>/dev/null); then
    [ "$MODE" = 600 ] || fail "ответ сохранён с правами $MODE вместо 600"
elif MODE=$(stat -c '%a' "$TEST_TMP/cache/latest-invoices.json" 2>/dev/null); then
    [ "$MODE" = 600 ] || fail "ответ сохранён с правами $MODE вместо 600"
fi
assert_not_contains "$TEST_TMP/out" "test-token"

# Недокументированное создание счёта блокируется до curl.
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN="test-token" run_zoomkit invoice-create --amount 5000 --requisites arbitrary > "$TEST_TMP/out" 2>&1; then
    fail "создание счёта ошибочно разрешено"
fi
assert_contains "$TEST_TMP/out" "не поддерживает эту операцию"
[ ! -e "$TEST_TMP/curl.log" ] || fail "для создания счёта был вызван curl"

# Все изменяющие команды блокируются без --confirm, а сухой прогон строит точный путь.
printf '%s\n' '{"type":"CAMPAIGN_ADSET_REPORT","date_start":"2026-08-01","date_end":"2026-08-31","clients":["yandex.direct:1"]}' > "$TEST_TMP/report.json"
printf '%s\n' '{broken' > "$TEST_TMP/invalid.json"
if ZOOMKIT_API_TOKEN= run_zoomkit report-create --body "$TEST_TMP/invalid.json" --dry-run > "$TEST_TMP/out" 2>&1; then
    fail "некорректный JSON прошёл проверку"
fi
assert_contains "$TEST_TMP/out" "корректный JSON-объект"

printf '%s\n' '[]' > "$TEST_TMP/array.json"
if ZOOMKIT_API_TOKEN= run_zoomkit report-create --body "$TEST_TMP/array.json" --dry-run > "$TEST_TMP/out" 2>&1; then
    fail "JSON-массив был принят вместо объекта"
fi
assert_contains "$TEST_TMP/out" "корректный JSON-объект"

# Сухой прогон проверяет не только JSON, но и документированную схему отчёта.
printf '%s\n' '{"type":"NOT_A_REPORT","date_start":"2026-08-01","date_end":"2026-08-31","clients":[]}' > "$TEST_TMP/report-invalid-schema.json"
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN= run_zoomkit report-create --body "$TEST_TMP/report-invalid-schema.json" --dry-run > "$TEST_TMP/out" 2>&1; then
    fail "недопустимый тип отчёта и пустой список клиентов прошли проверку"
fi
assert_contains "$TEST_TMP/out" "некорректное тело report-create"
[ ! -e "$TEST_TMP/curl.log" ] || fail "некорректный отчёт дошёл до curl"

printf '%s\n' '{"type":"CAMPAIGN_QUERIES_REPORT","date_start":"2026-08-01","date_end":"2026-08-31","clients":["google.ads:1"]}' > "$TEST_TMP/report-incompatible.json"
if ZOOMKIT_API_TOKEN= run_zoomkit report-create --body "$TEST_TMP/report-incompatible.json" --dry-run > "$TEST_TMP/out" 2>&1; then
    fail "несовместимый клиент отчёта прошёл проверку"
fi
assert_contains "$TEST_TMP/out" "совместимость интеграций"

printf '%s\n' '{"type":"CAMPAIGN_ADSET_REPORT","date_start":"2026-08-01","date_end":"2026-08-31","clients":["yandex.direct:1"],"callback":"abc"}' > "$TEST_TMP/report-invalid-callback.json"
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN= run_zoomkit report-create --body "$TEST_TMP/report-invalid-callback.json" --dry-run > "$TEST_TMP/out" 2>&1; then
    fail "неабсолютный callback отчёта прошёл проверку"
fi
assert_contains "$TEST_TMP/out" "абсолютный http(s)-адрес callback"
[ ! -e "$TEST_TMP/curl.log" ] || fail "некорректный callback дошёл до curl"

# Текстовые настройки проверки ссылок нельзя ошибочно отправить через API.
printf '%s\n' '{"check_urls_substring":"В наличии"}' > "$TEST_TMP/url-check-text.json"
rm -f "$TEST_TMP/curl.log"
if ZOOMKIT_API_TOKEN= run_zoomkit url-check-settings-update --campaign 56 --body "$TEST_TMP/url-check-text.json" --dry-run > "$TEST_TMP/out" 2>&1; then
    fail "текстовая настройка проверки ссылок была принята для API"
fi
assert_contains "$TEST_TMP/out" "текстовые поля меняются вручную по settings_url"
[ ! -e "$TEST_TMP/curl.log" ] || fail "текстовая настройка дошла до curl"

assert_write_guard report-create report-create --body "$TEST_TMP/report.json"
assert_write_guard report-delete report-delete --id 12
assert_write_guard client-update client-update --client 34
assert_write_guard autotargeting autotargeting --token-id 78
assert_write_guard bidrule-common-update bidrule-common-update --campaign 56 --body "$SKILL_DIR/assets/bidrule-update.example.json"
assert_write_guard bidrule-keywords-create bidrule-keywords-create --campaign 56 --body "$SKILL_DIR/assets/bidrule-keywords.example.json"
assert_write_guard bidrule-keywords-update bidrule-keywords-update --campaign 56 --bidrule 90 --body "$SKILL_DIR/assets/bidrule-keywords.example.json"
assert_write_guard bidrule-keywords-delete bidrule-keywords-delete --campaign 56 --bidrule 90
assert_write_guard url-check-settings-update url-check-settings-update --campaign 56 --body "$SKILL_DIR/assets/url-check-settings-update.example.json"

assert_dry_route POST /stats/reports report-create --body "$TEST_TMP/report.json"
assert_dry_route DELETE /stats/reports/12 report-delete --id 12
assert_dry_route POST /yandex/direct/clients/34/update client-update --client 34
assert_dry_route POST /yandex/direct/tokens/78/autotargeting autotargeting --token-id 78
assert_dry_route PUT /yandex/direct/campaigns/56/bidrules/common bidrule-common-update --campaign 56 --body "$SKILL_DIR/assets/bidrule-update.example.json"
assert_dry_route POST /yandex/direct/campaigns/56/bidrules/keywords bidrule-keywords-create --campaign 56 --body "$SKILL_DIR/assets/bidrule-keywords.example.json"
assert_dry_route PUT /yandex/direct/campaigns/56/bidrules/keywords/90 bidrule-keywords-update --campaign 56 --bidrule 90 --body "$SKILL_DIR/assets/bidrule-keywords.example.json"
assert_dry_route DELETE /yandex/direct/campaigns/56/bidrules/keywords/90 bidrule-keywords-delete --campaign 56 --bidrule 90
assert_dry_route PUT /yandex/direct/campaigns/56/url-check-settings url-check-settings-update --campaign 56 --body "$SKILL_DIR/assets/url-check-settings-update.example.json"
assert_dry_route PUT /yandex/direct/campaigns/56/url-check-settings url-check-settings-update --campaign 56 --body "$SKILL_DIR/assets/url-check-product-protection.example.json"
assert_contains "$TEST_TMP/out" "Тело JSON:"

# Все документированные команды чтения также строят правильный метод и путь.
assert_dry_route GET /token token
assert_dry_route GET /billing/balance balance
assert_dry_route GET /billing/invoices invoices
assert_dry_route GET /stats/clients clients
assert_dry_route GET /stats/reports reports
assert_dry_route GET /stats/reports/12 report --id 12
assert_dry_route GET /yandex/direct/campaigns/56/bidrules bidrules --campaign 56
assert_dry_route GET /yandex/direct/campaigns/56/url-check-settings url-check-settings --campaign 56
assert_dry_route GET /yandex/direct/campaigns/56/url-check-tasks url-check-tasks --campaign 56
assert_dry_route GET /yandex/direct/campaigns/56/url-check-tasks/91 url-check-task --campaign 56 --task 91

# Краткий вывод показывает настройки правил ставок, строковые правила и найденные проблемы.
printf '%s\n' '{"campaign_id":56,"search_strategy":"HIGHEST_POSITION","network_strategy":"SERVING_OFF","warnings":["проверочное предупреждение"],"common":{"id":401,"match":null,"search_enabled":true,"search_traffic_volume":100,"search_increase_percent":5,"search_max_bid":150,"search_max_bid_from_price":false},"keywords":[{"id":402,"match":"купить","search_enabled":true,"search_traffic_volume":95,"search_increase_percent":7,"search_max_bid":120,"search_max_bid_from_price":true}]}' > "$TEST_TMP/bidrules.json"
(MOCK_BODY_FILE="$TEST_TMP/bidrules.json" ZOOMKIT_API_TOKEN="test-token" run_zoomkit bidrules --campaign 56) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "Общее правило"
assert_contains "$TEST_TMP/out" "401"
assert_contains "$TEST_TMP/out" "купить"
assert_contains "$TEST_TMP/out" "фактическую ставку каждой фразы"
assert_contains "$TEST_TMP/out" "Полный список кампаний есть в интерфейсе управления ставками"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/yandex/direct/campaigns"

printf '%s\n' '{"campaign_id":57,"common":{"id":null},"keywords":[]}' > "$TEST_TMP/bidrules-unsaved.json"
(MOCK_BODY_FILE="$TEST_TMP/bidrules-unsaved.json" ZOOMKIT_API_TOKEN="test-token" run_zoomkit bidrules --campaign 57) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "не сохранено"

printf '%s\n' '{"campaign_id":56,"check_urls_enabled":true,"suspend_urls_enabled":true,"suspend_urls_if_301":false,"suspend_urls_if_302":false,"suspend_urls_if_303":false,"suspend_urls_if_307":false,"suspend_urls_if_400_499":true,"suspend_urls_if_500_599":false,"suspend_urls_if_other":false,"suspend_urls_if_substring":true,"suspend_urls_if_numeral":false,"text_settings":{"check_urls_substring":"В корзину","check_urls_substring_must_present":true},"settings_url":"https://zoomkit.ru/yandex/direct/56/url-check-settings"}' > "$TEST_TMP/url-check-settings.json"
(MOCK_BODY_FILE="$TEST_TMP/url-check-settings.json" ZOOMKIT_API_TOKEN="test-token" run_zoomkit url-check-settings --campaign 56) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "Проверка ссылок включена: true"
assert_contains "$TEST_TMP/out" "Приостанавливать при HTTP 301: false"
assert_contains "$TEST_TMP/out" "Приостанавливать при HTTP 4xx: true"
assert_contains "$TEST_TMP/out" "Искомая строка: В корзину"
assert_contains "$TEST_TMP/out" "Ошибка при отсутствии строки: true"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/yandex/direct/56/url-check-settings"
assert_contains "$TEST_TMP/out" "Полный список кампаний есть в интерфейсе управления ставками"

printf '%s\n' '{"id":91,"campaign_id":56,"complete":true,"sent":false,"status":"has_problems","groups":[{"adgroup_id":501,"problems":[{"type":"error","url":"https://shop.example/item","source_url":"https://shop.example/item?utm_source=yandex","ad_id":601,"more_ads":0,"sitelink":false,"message":"HTTP 404"}],"suspended":["ad:601"],"resumed":[]}]}' > "$TEST_TMP/url-check-task.json"
(MOCK_BODY_FILE="$TEST_TMP/url-check-task.json" ZOOMKIT_API_TOKEN="test-token" run_zoomkit url-check-task --campaign 56 --task 91) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "Состояние: has_problems"
assert_contains "$TEST_TMP/out" "https://shop.example/item"
assert_contains "$TEST_TMP/out" "HTTP 404"
assert_contains "$TEST_TMP/out" "Приостановлено объектов: 1"

# Явно подтверждённое создание отчёта печатает ID и следующий шаг.
printf '%s\n' '{"id":12,"status":"CREATED","type":"CAMPAIGN_ADSET_REPORT","date_start":"2026-08-01","date_end":"2026-08-31","clients":["yandex.direct:1"]}' > "$TEST_TMP/report-created.json"
(MOCK_BODY_FILE="$TEST_TMP/report-created.json" ZOOMKIT_API_TOKEN="test-token" run_zoomkit report-create --body "$TEST_TMP/report.json" --confirm) > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/curl.log" "METHOD=POST"
assert_contains "$TEST_TMP/curl.log" "URL=https://api.example.test/v1/stats/reports"
assert_contains "$TEST_TMP/curl.log" "CONTENT_TYPE=Content-Type: application/json"
assert_contains "$TEST_TMP/curl.log" '"CAMPAIGN_ADSET_REPORT"'
assert_contains "$TEST_TMP/out" "Отчёт поставлен в очередь: 12"
assert_contains "$TEST_TMP/out" "report-wait --id 12"
assert_not_contains "$TEST_TMP/out" "test-token"

# Ожидание проходит состояния CREATED -> PROCESSED -> READY и сохраняет результат.
mkdir -p "$TEST_TMP/report-sequence"
printf '%s\n' '{"id":12,"status":"CREATED","type":"CAMPAIGN_ADSET_REPORT","clients":["yandex.direct:1"]}' > "$TEST_TMP/report-sequence/1.json"
printf '%s\n' '{"id":12,"status":"PROCESSED","type":"CAMPAIGN_ADSET_REPORT","clients":["yandex.direct:1"]}' > "$TEST_TMP/report-sequence/2.json"
printf '%s\n' '{"id":12,"status":"READY","type":"CAMPAIGN_ADSET_REPORT","date_start":"2026-08-01","date_end":"2026-08-31","clients":["yandex.direct:1","yandex.direct:2"],"reports":{"yandex.direct:1":[],"yandex.direct:2":{"error":"нет доступа"}}}' > "$TEST_TMP/report-sequence/3.json"
rm -f "$TEST_TMP/report-counter"
(MOCK_REPORT_SEQUENCE_DIR="$TEST_TMP/report-sequence" \
 MOCK_REPORT_COUNTER_FILE="$TEST_TMP/report-counter" \
 ZOOMKIT_API_TOKEN="test-token" \
 run_zoomkit report-wait --id 12 --interval 1 --timeout 5 --output "$TEST_TMP/report-ready.json") > "$TEST_TMP/out" 2>&1
assert_contains "$TEST_TMP/out" "CREATED"
assert_contains "$TEST_TMP/out" "PROCESSED"
assert_contains "$TEST_TMP/out" "Отчёт 12 готов"
assert_contains "$TEST_TMP/out" "Клиентских ошибок: 1"
assert_contains "$TEST_TMP/out" "yandex.direct:2: нет доступа"
assert_contains "$TEST_TMP/curl.log" "MAX_TIME="
assert_not_contains "$TEST_TMP/curl.log" "MAX_TIME=120"
[ "$(cat "$TEST_TMP/report-counter")" = 3 ] || fail "ожидание выполнило неверное число запросов"
jq -e '.status == "READY"' "$TEST_TMP/report-ready.json" >/dev/null || fail "готовый отчёт не сохранён"

# Без --output ожидания разных отчётов получают разные имена файлов.
rm -f "$TEST_TMP/cache/report-12.json"
(MOCK_BODY_FILE="$TEST_TMP/report-ready.json" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit report-wait --id 12 --timeout 0) > "$TEST_TMP/out" 2>&1
[ -s "$TEST_TMP/cache/report-12.json" ] || fail "report-wait не сохранил ответ под ID отчёта"
jq -e '.id == 12 and .status == "READY"' "$TEST_TMP/cache/report-12.json" >/dev/null || \
    fail "report-wait сохранил неверное состояние по умолчанию"

# Ответ другого отчёта не публикуется даже при совпадающем состоянии READY.
printf '%s\n' '{"id":99,"status":"READY","type":"CAMPAIGN_ADSET_REPORT","reports":{}}' > "$TEST_TMP/report-wrong-id.json"
if (MOCK_BODY_FILE="$TEST_TMP/report-wrong-id.json" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit report-wait --id 12 --output "$TEST_TMP/report-wrong-output.json") > "$TEST_TMP/out" 2>&1; then
    fail "ожидание приняло ответ другого отчёта"
fi
assert_contains "$TEST_TMP/out" "вместо 12"
[ ! -e "$TEST_TMP/report-wrong-output.json" ] || fail "ответ другого отчёта был опубликован"

# Промежуточный статус с нулевым сроком завершается понятным тайм-аутом.
if (MOCK_BODY_FILE="$TEST_TMP/report-created.json" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit report-wait --id 12 --timeout 0) > "$TEST_TMP/out" 2>&1; then
    fail "ожидание CREATED с нулевым сроком завершилось успешно"
fi
assert_contains "$TEST_TMP/out" "Ожидание остановлено"
assert_contains "$TEST_TMP/out" "report-wait --id 12"

# FAILED останавливает ожидание, а 409 направляет к существующему отчёту.
printf '%s\n' '{"id":12,"status":"FAILED","type":"CAMPAIGN_ADSET_REPORT"}' > "$TEST_TMP/report-failed.json"
if (MOCK_BODY_FILE="$TEST_TMP/report-failed.json" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit report-wait --id 12) > "$TEST_TMP/out" 2>&1; then
    fail "отчёт FAILED был принят за готовый"
fi
assert_contains "$TEST_TMP/out" "статусом FAILED"

printf '%s\n' '{"conflict_report_id":44}' > "$TEST_TMP/report-conflict.json"
if (MOCK_STATUS=409 MOCK_BODY_FILE="$TEST_TMP/report-conflict.json" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit report-create --body "$TEST_TMP/report.json" --confirm) > "$TEST_TMP/out" 2>&1; then
    fail "конфликт создания отчёта завершился успешно"
fi
assert_contains "$TEST_TMP/out" "пересекающийся отчёт 44"
assert_contains "$TEST_TMP/out" "report-wait --id 44"

# Успешный HTTP-код с не-JSON телом считается ошибкой, но ответ сохраняется для диагностики.
printf '%s\n' '<html>not-json</html>' > "$TEST_TMP/not-json.txt"
if MOCK_STATUS=200 MOCK_BODY_FILE="$TEST_TMP/not-json.txt" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit balance > "$TEST_TMP/out" 2>&1; then
    fail "не-JSON ответ с HTTP 200 был принят за успех"
fi
assert_contains "$TEST_TMP/out" "успешный код HTTP, но не JSON-объект или массив"
assert_contains "$TEST_TMP/cache/latest-balance.json" "<html>not-json</html>"

if MOCK_STATUS=200 MOCK_BODY_FILE="$TEST_TMP/not-json.txt" ZOOMKIT_API_TOKEN="test-token" \
    run_zoomkit balance --raw > "$TEST_TMP/out" 2>&1; then
    fail "--raw принял не-JSON ответ с HTTP 200"
fi
assert_contains "$TEST_TMP/out" "успешный код HTTP, но не JSON-объект или массив"
assert_not_contains "$TEST_TMP/out" "<html>not-json</html>"

# HTTP 401 снова даёт инструкцию по выпуску ключа и не печатает секрет.
MOCK_STATUS=401 ZOOMKIT_API_TOKEN="expired-secret" run_zoomkit token > "$TEST_TMP/out" 2>&1 || true
assert_contains "$TEST_TMP/out" "ключ ZoomKit API не найден или недействителен"
assert_contains "$TEST_TMP/out" "https://zoomkit.ru/profile/api"
assert_not_contains "$TEST_TMP/out" "expired-secret"

# HTTP 429 показывает момент снятия ограничения.
MOCK_STATUS=429 MOCK_RATE_RESET=1800000000 ZOOMKIT_API_TOKEN="test-token" run_zoomkit balance > "$TEST_TMP/out" 2>&1 || true
assert_contains "$TEST_TMP/out" "HTTP 429"
assert_contains "$TEST_TMP/out" "1800000000"

printf '%s\n' "PASS"
