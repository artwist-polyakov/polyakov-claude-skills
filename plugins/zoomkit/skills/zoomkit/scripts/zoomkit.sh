#!/bin/sh
# Единый клиент официального ZoomKit API 1.8.0.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SKILL_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
CONFIG_FILE=${ZOOMKIT_CONFIG_FILE:-"$SKILL_DIR/config/.env"}
CACHE_DIR=${ZOOMKIT_CACHE_DIR:-"$SKILL_DIR/cache"}
PROFILE_API_URL="https://zoomkit.ru/profile/api"
PROFILE_URL="https://zoomkit.ru/profile"
REGISTER_URL="https://zoomkit.ru/register"
HELP_URL="https://zoomkit.ru/help"
DEFAULT_API_BASE_URL="https://zoomkit.ru/api/v1"
PRICE_SNAPSHOT_DATE="06.09.2026"

usage() {
    cat <<'EOF'
ZoomKit API

Использование:
  sh scripts/zoomkit.sh <команда> [параметры]

Чтение:
  token
  balance
  invoices [--status wait|paid|cancelled] [--limit N]
  clients
  reports
  report --id ID
  report-wait --id ID [--interval СЕК] [--timeout СЕК] [--output ФАЙЛ]
  bidrules --campaign ID
  url-check-settings --campaign ID
  url-check-tasks --campaign ID
  url-check-task --campaign ID --task ID

Изменение (нужен --confirm):
  report-create --body ФАЙЛ
  report-delete --id ID
  client-update --client ID
  autotargeting --token-id ID
  bidrule-common-update --campaign ID --body ФАЙЛ
  bidrule-keywords-create --campaign ID --body ФАЙЛ
  bidrule-keywords-update --campaign ID --bidrule ID --body ФАЙЛ
  bidrule-keywords-delete --campaign ID --bidrule ID
  url-check-settings-update --campaign ID --body ФАЙЛ

Служебное:
  getting-started         Объяснить пользу, тарифы и подключение, ключ не нужен
  billing-capabilities    Показать возможности API по счетам, ключ не нужен

Общие параметры:
  --dry-run              Показать запрос без сети и без ключа
  --confirm              Разрешить изменяющий запрос
  --output ФАЙЛ          Сохранить полный JSON по указанному пути
  --raw                  Также напечатать полный JSON
  --help                 Показать эту справку
EOF
}

die() {
    printf 'Ошибка: %s\n' "$1" >&2
    exit "${2:-1}"
}

show_connection_steps() {
    cat <<EOF
Порядок подключения:
  1. Если учётной записи ещё нет, зарегистрируйтесь: $REGISTER_URL
     Если она уже есть, войдите: https://zoomkit.ru/login
  2. Пополните баланс в профиле: $PROFILE_URL
     Для первого запуска можно начать с 500 руб. Это пример, а не заявленная
     минимальная сумма. Перед оплатой проверьте итог с добавляемыми 6%.
  3. После пополнения создайте ключ API: $PROFILE_API_URL
  4. Сохраните ключ в штатном постоянном файле навыка:
     cd "$SKILL_DIR"
     [ -f config/.env ] || cp config/.env.example config/.env
     chmod 600 config/.env
     Затем замените your_api_token_here в ZOOMKIT_API_TOKEN.
     config/.env сохраняется между сессиями; создавать его заново не нужно.
  5. Проверьте подключение:
     sh scripts/zoomkit.sh token

Ключ не нужно отправлять в чат. Подробная инструкция: $SKILL_DIR/config/README.md
EOF
}

show_auth_steps() {
    show_connection_steps >&2
}

show_auth_help() {
    printf '%s\n' "Ошибка: ключ ZoomKit API не найден или недействителен." >&2
    show_auth_steps
}

show_getting_started() {
    cat <<EOF
ZoomKit помогает вести рекламу без постоянной ручной проверки:
  - управляет ставками Яндекс.Директа и проверяет посадочные страницы;
  - прогнозирует, когда закончатся средства, и напоминает пополнить баланс;
  - упрощает массовые и отложенные изменения в нескольких кабинетах;
  - передаёт расходы в Яндекс.Метрику и помогает собирать данные о конверсиях.

Почему сервис может быть полезен:
  автоматизация особенно оправдана, когда стоимость ручного контроля и риск
  ошибок выше суточного тарифа. Для одного простого кабинета без
  регулярных операций сначала сравните нужные возможности с расходами.

Справочный снимок опубликованных тарифов, проверен $PRICE_SNAPSHOT_DATE:
  - Яндекс.Директ, управление ставками: 50 руб./сутки + 2 руб./сутки
    за каждый подключённый рекламный кабинет;
  - загрузка расходов в Яндекс.Метрику: 5 руб./сутки за рекламный канал;
  - импорт статистики Битрикс24 в Яндекс.Метрику: 50 руб./сутки за аккаунт;
  - товары ВКонтакте: 30 руб./сутки за сообщество.

Пример: управление ставками одного кабинета — 52 руб./сутки,
или около 1560 руб. за 30 дней при неизменном тарифе и без учёта
добавляемых при пополнении 6% по публичной оферте.
После подключения точный общий расход показывает команда balance.
Перед расчётом проверьте актуальные цены: $HELP_URL

EOF
    show_connection_steps
}

show_missing_token_help() {
    printf '%s\n' "Ошибка: ключ ZoomKit API не найден." >&2
    printf '\n' >&2
    show_getting_started >&2
}

show_billing_capabilities() {
    cat <<EOF
ZoomKit API 1.8.0 по счетам:
  доступно:    чтение баланса и списка уже выставленных счетов;
  недоступно:  создание счета, передача реквизитов, печатная форма,
               создание или получение ссылки на оплату.

Профиль для выставления и оплаты счетов: $PROFILE_URL
Перед ручным выставлением проверьте ожидающие счета командой:
  sh scripts/zoomkit.sh invoices --status wait
EOF
}

validate_positive_integer() {
    _zvpi_value=$1
    _zvpi_name=$2
    case "$_zvpi_value" in
        ''|*[!0-9]*) die "$_zvpi_name должен быть положительным целым числом" ;;
    esac
    [ "$_zvpi_value" -gt 0 ] || die "$_zvpi_name должен быть больше нуля"
}

require_value() {
    [ $# -ge 2 ] || die "после $1 требуется значение"
}

load_settings() {
    _zls_env_token_set=${ZOOMKIT_API_TOKEN+x}
    _zls_env_token=${ZOOMKIT_API_TOKEN-}
    _zls_env_base_set=${ZOOMKIT_API_BASE_URL+x}
    _zls_env_base=${ZOOMKIT_API_BASE_URL-}

    if [ -f "$CONFIG_FILE" ]; then
        _zls_line_number=0
        while IFS= read -r _zls_line || [ -n "$_zls_line" ]; do
            _zls_line_number=$((_zls_line_number + 1))
            _zls_trimmed=$(printf '%s' "$_zls_line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
            case "$_zls_trimmed" in
                ''|\#*) continue ;;
            esac

            case "$_zls_trimmed" in
                *=*) ;;
                *) die "некорректная строка $CONFIG_FILE:$_zls_line_number" ;;
            esac

            _zls_key=${_zls_trimmed%%=*}
            _zls_value=${_zls_trimmed#*=}
            _zls_key=$(printf '%s' "$_zls_key" | sed 's/[[:space:]]//g')
            _zls_value=$(printf '%s' "$_zls_value" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')

            case "$_zls_value" in
                \"*\") _zls_value=${_zls_value#\"}; _zls_value=${_zls_value%\"} ;;
                \'*\') _zls_value=${_zls_value#\'}; _zls_value=${_zls_value%\'} ;;
                \"*|\'*) die "незакрытая кавычка в $CONFIG_FILE:$_zls_line_number" ;;
                *[[:space:]]*) die "значение с пробелами нужно заключить в кавычки: $CONFIG_FILE:$_zls_line_number" ;;
            esac

            case "$_zls_key" in
                ZOOMKIT_API_TOKEN) ZOOMKIT_API_TOKEN=$_zls_value ;;
                ZOOMKIT_API_BASE_URL) ZOOMKIT_API_BASE_URL=$_zls_value ;;
                *) die "неизвестный параметр $_zls_key в $CONFIG_FILE:$_zls_line_number" ;;
            esac
        done < "$CONFIG_FILE"
    fi

    if [ "$_zls_env_token_set" = x ] && [ -n "$_zls_env_token" ]; then
        ZOOMKIT_API_TOKEN=$_zls_env_token
    fi
    if [ "$_zls_env_base_set" = x ]; then
        ZOOMKIT_API_BASE_URL=$_zls_env_base
    fi

    ZOOMKIT_API_BASE_URL=${ZOOMKIT_API_BASE_URL:-$DEFAULT_API_BASE_URL}
    ZOOMKIT_API_BASE_URL=${ZOOMKIT_API_BASE_URL%/}

    _zls_authority=${ZOOMKIT_API_BASE_URL#*://}
    _zls_authority=${_zls_authority%%/*}
    case "$_zls_authority" in
        ''|*@*) die "адрес API не должен содержать пустой хост или userinfo" ;;
    esac

    if [ "$ZOOMKIT_API_BASE_URL" != "$DEFAULT_API_BASE_URL" ] && [ "${ZOOMKIT_ALLOW_CUSTOM_API_BASE:-0}" != 1 ]; then
        die "нестандартный адрес API требует ZOOMKIT_ALLOW_CUSTOM_API_BASE=1 в окружении"
    fi

    case "$ZOOMKIT_API_BASE_URL" in
        https://*) ;;
        http://*)
            _zls_local_host=${_zls_authority%%:*}
            if [ "$_zls_authority" = "$_zls_local_host" ]; then
                _zls_local_port=""
            else
                _zls_local_port=${_zls_authority#*:}
                case "$_zls_local_port" in
                    ''|*[!0-9]*) die "локальный HTTP-адрес содержит недопустимый порт" ;;
                esac
            fi
            case "$_zls_local_host" in
                localhost|127.0.0.1) ;;
                *) die "ключ ZoomKit нельзя отправлять по незащищённому HTTP" ;;
            esac
            [ "${ZOOMKIT_ALLOW_INSECURE_HTTP:-0}" = 1 ] || die "HTTP разрешён только для локальной проверки с ZOOMKIT_ALLOW_INSECURE_HTTP=1"
            ;;
        *) die "ZOOMKIT_API_BASE_URL должен начинаться с https://" ;;
    esac
}

require_token() {
    case "${ZOOMKIT_API_TOKEN-}" in
        ''|your_api_token_here|replace_me|paste_token_here|*your_api_token_here*)
            show_missing_token_help
            exit 2
            ;;
    esac

    _zrt_single_line=$(printf '%s' "$ZOOMKIT_API_TOKEN" | tr -d '\r\n')
    [ "$_zrt_single_line" = "$ZOOMKIT_API_TOKEN" ] || die "ZOOMKIT_API_TOKEN не должен содержать перевод строки"
}

validate_body_file() {
    [ -n "$BODY_FILE" ] || die "для команды $COMMAND требуется --body ФАЙЛ"
    [ -f "$BODY_FILE" ] || die "файл тела запроса не найден: $BODY_FILE"
    [ -r "$BODY_FILE" ] || die "файл тела запроса нельзя прочитать: $BODY_FILE"
    [ -s "$BODY_FILE" ] || die "файл тела запроса пуст: $BODY_FILE"

    command -v jq >/dev/null 2>&1 || die "для проверки тела запроса нужен jq"
    jq -e 'type == "object"' "$BODY_FILE" >/dev/null 2>&1 || die "в $BODY_FILE должен находиться корректный JSON-объект"
}

validate_command_body() {
    case "$COMMAND" in
        report-create)
            jq -e '
                def allowed: ["type", "date_start", "date_end", "clients", "callback"];
                def full_date:
                    (type == "string") and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$");
                def client_id:
                    (type == "string") and
                    test("^(yandex\\.direct|google\\.ads|facebook\\.ads|vk\\.ads|mytarget):[0-9]+$");
                def callback_url:
                    (type == "string") and
                    test("^https?://[^/?#[:space:]]+(?:[/?#][^[:space:]]*)?$"; "i");
                ((keys - allowed) | length == 0) and
                has("type") and has("date_start") and has("date_end") and has("clients") and
                (.type == "CAMPAIGN_ADSET_REPORT" or
                 .type == "CAMPAIGN_PHRASES_REPORT" or
                 .type == "CAMPAIGN_QUERIES_REPORT") and
                (.date_start | full_date) and (.date_end | full_date) and
                (.date_start <= .date_end) and
                ((.clients | type) == "array") and
                ((.clients | length) >= 1 and (.clients | length) <= 20) and
                (all(.clients[]; client_id)) and
                ((.clients | unique | length) == (.clients | length)) and
                ((has("callback") | not) or
                    (.callback | callback_url)) and
                (if .type == "CAMPAIGN_PHRASES_REPORT" then
                    all(.clients[]; test("^(yandex\\.direct|google\\.ads):"))
                 elif .type == "CAMPAIGN_QUERIES_REPORT" then
                    all(.clients[]; test("^yandex\\.direct:"))
                 else true end)
            ' "$BODY_FILE" >/dev/null 2>&1 || \
                die "некорректное тело report-create: проверьте тип отчёта, даты YYYY-MM-DD, 1–20 уникальных клиентов type:id, совместимость интеграций и абсолютный http(s)-адрес callback"
            ;;
        url-check-settings-update)
            jq -e '
                def allowed: [
                    "check_urls_enabled",
                    "suspend_urls_enabled",
                    "suspend_urls_if_301",
                    "suspend_urls_if_302",
                    "suspend_urls_if_303",
                    "suspend_urls_if_307",
                    "suspend_urls_if_400_499",
                    "suspend_urls_if_500_599",
                    "suspend_urls_if_other",
                    "suspend_urls_if_substring",
                    "suspend_urls_if_numeral",
                    "check_urls_ignore_substring_in_sitelinks",
                    "check_urls_check_numerals",
                    "check_urls_strip_tags",
                    "check_urls_metrika_ids",
                    "check_urls_ajax",
                    "check_urls_ignore_autotargeting",
                    "check_urls_title_over_54",
                    "check_urls_max_time"
                ];
                (length > 0) and
                ((keys - allowed) | length == 0) and
                all(to_entries[];
                    .key as $key | .value as $value |
                    if $key == "check_urls_max_time" then
                        (($value | type) == "number") and
                        (($value | floor) == $value) and
                        ($value >= 0 and $value <= 600)
                    else
                        (($value | type) == "boolean")
                    end
                )
            ' "$BODY_FILE" >/dev/null 2>&1 || \
                die "url-check-settings-update принимает только документированные переключатели и целое check_urls_max_time от 0 до 600; текстовые поля меняются вручную по settings_url"
            ;;
    esac
}

header_value() {
    _zhv_name=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    awk -v wanted="$_zhv_name" '
        {
            name = tolower($1)
            sub(/:$/, "", name)
            if (name == wanted) {
                $1 = ""
                sub(/^[ \t]+/, "")
                sub(/\r$/, "")
                value = $0
            }
        }
        END { print value }
    ' "$HEADERS_FILE"
}

body_checksum() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{ print $1 }'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{ print $1 }'
    else
        cksum "$1" | awk '{ print "cksum:" $1 ":" $2 }'
    fi
}

print_error_excerpt() {
    [ -s "$BODY_TMP" ] || return 0
    printf '%s\n' "Ответ сервиса (не более 2000 байт):" >&2
    dd if="$BODY_TMP" bs=2000 count=1 2>/dev/null >&2 || true
    printf '\n' >&2
}

handle_http_error() {
    case "$HTTP_STATUS" in
        401)
            show_auth_help
            ;;
        403)
            printf '%s\n' "Ошибка: HTTP 403 — у ключа нет доступа к объекту или платной возможности." >&2
            print_error_excerpt
            ;;
        404)
            printf '%s\n' "Ошибка: HTTP 404 — объект не найден или принадлежит другому владельцу." >&2
            print_error_excerpt
            ;;
        409)
            printf '%s\n' "Ошибка: HTTP 409 — операция конфликтует с текущим состоянием объекта." >&2
            if [ "$COMMAND" = report-create ] && command -v jq >/dev/null 2>&1; then
                _zhe_conflict_report_id=$(jq -r '.conflict_report_id // empty' "$BODY_TMP" 2>/dev/null || true)
                case "$_zhe_conflict_report_id" in
                    ''|*[!0-9]*) ;;
                    *)
                        printf 'Уже создаётся пересекающийся отчёт %s; дождитесь его:\n' "$_zhe_conflict_report_id" >&2
                        printf '  sh scripts/zoomkit.sh report-wait --id %s\n' "$_zhe_conflict_report_id" >&2
                        ;;
                esac
            fi
            print_error_excerpt
            ;;
        429)
            _zhe_reset=$(header_value X-RateLimit-Reset)
            printf '%s\n' "Ошибка: HTTP 429 — превышен предел запросов ZoomKit." >&2
            if [ -n "$_zhe_reset" ]; then
                printf 'Повторите после метки времени: %s\n' "$_zhe_reset" >&2
            fi
            ;;
        *)
            printf 'Ошибка: ZoomKit API вернул HTTP %s.\n' "$HTTP_STATUS" >&2
            print_error_excerpt
            ;;
    esac
    exit 1
}

render_response() {
    _zrr_file=$1

    if ! command -v jq >/dev/null 2>&1; then
        printf '%s\n' "Получен JSON-подобный ответ. Для полной проверки и краткого разбора нужен jq."
        return 0
    fi

    case "$COMMAND" in
        token)
            jq -r '
                "Ключ: \(.name // "(без названия)")",
                "Идентификатор: \(.id // "—")",
                "Истекает: \(.expires_at // "срок не указан")",
                "Осталось полных дней: \(.expires_in_days // "—")",
                "Скоро истекает: \(.expires_soon // false)"
            ' "$_zrr_file"
            ;;
        balance)
            jq -r '
                "Баланс: \(.balance // "—") руб.",
                "Суточный тариф: \(.daily_tariff // "—") руб.",
                "Запас: \(.days_left // "без списаний") дн."
            ' "$_zrr_file"
            ;;
        invoices)
            _zrr_count=$(jq --arg status "$STATUS_FILTER" '[.[] | select($status == "" or .status == $status)] | length' "$_zrr_file")
            printf 'Счетов после отбора: %s\n' "$_zrr_count"
            jq -r --arg status "$STATUS_FILTER" --argjson limit "$LIMIT" '
                def state:
                    if . == "wait" then "ожидает оплаты"
                    elif . == "paid" then "оплачен"
                    elif . == "cancelled" then "отменён"
                    else . end;
                ["ID", "Номер", "Дата", "Сумма", "К оплате", "Состояние"],
                ([.[] | select($status == "" or .status == $status)][: $limit][] |
                    [(.id // "—"), (.number // "—"), (.created_at // "—"),
                     (.amount // "—"), (.pay_amount // "—"), ((.status // "—") | state)])
                | @tsv
            ' "$_zrr_file"
            if [ "$_zrr_count" -gt "$LIMIT" ] 2>/dev/null; then
                printf 'Показаны первые %s; полный список находится в файле.\n' "$LIMIT"
            fi
            ;;
        clients)
            jq -r --argjson limit "$LIMIT" '
                def clean: tostring | gsub("[\\r\\n\\t]"; " ");
                [to_entries[] | .value[]] as $integrations |
                [$integrations[] | (.clients // [])[]] as $projects |
                [to_entries[] | .key as $type | .value[] | . as $integration |
                    if (($integration.clients // []) | length) == 0 then
                        [$type, ($integration.id // "—"), ($integration.name // "—"),
                         ($integration.errors // "—" | clean), "—", "—", "—"]
                    else
                        $integration.clients[] |
                        [$type, ($integration.id // "—"), ($integration.name // "—"),
                         ($integration.errors // "—" | clean), (.id // "—"),
                         (.name // "—"), (.comment // "—" | clean)]
                    end] as $rows |
                "Интеграций: \($integrations | length)",
                "Проектов/аккаунтов: \($projects | length)",
                "Строк таблицы: \($rows | length)",
                (["Тип", "ID интеграции", "Логин", "Ошибка", "ID проекта/аккаунта", "Проект/аккаунт", "Комментарий"] | @tsv),
                ($rows[: $limit][] | @tsv),
                (if ($rows | length) > $limit then
                    "Показаны первые \($limit); полный список находится в файле."
                 else empty end)
            ' "$_zrr_file"
            printf '%s\n' "API не показывает, какой кабинет включён в списания, и не умеет его отключать."
            ;;
        reports)
            jq -r --argjson limit "$LIMIT" '
                "Отчётов: \(length)",
                (["ID", "Состояние", "Тип", "Начало", "Конец"] | @tsv),
                (.[: $limit][] | [(.id // "—"), (.status // "—"), (.type // "—"),
                    (.date_start // "—"), (.date_end // "—")] | @tsv)
            ' "$_zrr_file"
            ;;
        report-create)
            jq -r '
                "Отчёт поставлен в очередь: \(.id // "—")",
                "Состояние: \(.status // "—")",
                "Тип: \(.type // "—")",
                "Следующий шаг: sh scripts/zoomkit.sh report-wait --id \(.id // "ID")"
            ' "$_zrr_file"
            ;;
        report)
            jq -r '
                [(.reports // {}) | to_entries[] |
                    select((.value | type) == "object" and (.value.error? != null)) |
                    "\(.key): \(.value.error)"] as $errors |
                "Отчёт: \(.id // "—")",
                "Состояние: \(.status // "—")",
                "Тип: \(.type // "—")",
                "Период: \(.date_start // "—") — \(.date_end // "—")",
                "Клиентов: \((.clients // []) | length)",
                "Результатов: \((.reports // {}) | length)",
                "Клиентских ошибок: \($errors | length)",
                ($errors[:10][] | "- " + .)
            ' "$_zrr_file"
            ;;
        bidrules)
            jq -r --argjson limit "$LIMIT" '
                def shown: if . == null then "—" else tostring end;
                def clean: shown | gsub("[\\r\\n\\t]"; " ");
                def common_id:
                    if (.common | type) != "object" then "неизвестно"
                    elif ((.common | has("id")) | not) then "неизвестно"
                    elif .common.id == null then "не сохранено"
                    else (.common.id | tostring) end;
                "Кампания: \(.campaign_id // "—")",
                "Стратегия на поиске: \(.search_strategy // "—")",
                "Стратегия в сетях: \(.network_strategy // "—")",
                "Предупреждения: \((.warnings // []) | length)",
                ((.warnings // [])[:10][] | "- " + .),
                "Общее правило:",
                (["ID", "Поиск", "Трафик", "Надбавка, %", "Макс. ставка", "От цены"] | @tsv),
                ([common_id, ((.common // {}).search_enabled | shown),
                  ((.common // {}).search_traffic_volume | shown),
                  ((.common // {}).search_increase_percent | shown),
                  ((.common // {}).search_max_bid | shown),
                  ((.common // {}).search_max_bid_from_price | shown)] | @tsv),
                "Правил для фраз: \((.keywords // []) | length)",
                (["ID", "Условие", "Поиск", "Трафик", "Надбавка, %", "Макс. ставка", "От цены"] | @tsv),
                ((.keywords // [])[: $limit][] |
                    [(.id | shown), (.match | clean), (.search_enabled | shown),
                     (.search_traffic_volume | shown), (.search_increase_percent | shown),
                     (.search_max_bid | shown), (.search_max_bid_from_price | shown)] | @tsv),
                "API показывает параметры правил, но не фактическую ставку каждой фразы.",
                "Через публичный API проверена только эта кампания. Полный список кампаний есть в интерфейсе управления ставками: https://zoomkit.ru/yandex/direct/campaigns"
            ' "$_zrr_file"
            ;;
        url-check-settings)
            jq -r '
                def shown: if . == null then "—" else tostring end;
                "Кампания: \(.campaign_id // "—")",
                "Проверка ссылок включена: \(.check_urls_enabled | shown)",
                "Автоприостановка включена: \(.suspend_urls_enabled | shown)",
                "Приостанавливать при HTTP 4xx: \(.suspend_urls_if_400_499 | shown)",
                "Приостанавливать при HTTP 5xx: \(.suspend_urls_if_500_599 | shown)",
                "Приостанавливать при HTTP 301: \(.suspend_urls_if_301 | shown)",
                "Приостанавливать при HTTP 302: \(.suspend_urls_if_302 | shown)",
                "Приостанавливать при HTTP 303: \(.suspend_urls_if_303 | shown)",
                "Приостанавливать при HTTP 307: \(.suspend_urls_if_307 | shown)",
                "Приостанавливать при прочих ошибках: \(.suspend_urls_if_other | shown)",
                "Приостанавливать по строковому правилу: \(.suspend_urls_if_substring | shown)",
                "Приостанавливать по числительным: \(.suspend_urls_if_numeral | shown)",
                "Искомая строка: \((.text_settings // {}).check_urls_substring | shown)",
                "Ошибка при отсутствии строки: \((.text_settings // {}).check_urls_substring_must_present | shown)",
                "Текстовые настройки меняются вручную: \(.settings_url // "адрес не возвращён")",
                "Через публичный API проверена только эта кампания. Полный список кампаний есть в интерфейсе управления ставками: https://zoomkit.ru/yandex/direct/campaigns"
            ' "$_zrr_file"
            ;;
        url-check-tasks)
            jq -r --argjson limit "$LIMIT" '
                "Проверок: \(length)",
                (["ID", "Создана", "Обновлена", "Завершена", "Отправлена"] | @tsv),
                (.[: $limit][] | [(.id // "—"), (.created_at // "—"), (.updated_at // "—"),
                    (.complete // false), (.sent // false)] | @tsv),
                (if length == 0 then
                    "Пустая история не доказывает, что проверка выключена: завершённые записи удаляются через двое суток."
                 elif length > $limit then
                    "Показаны первые \($limit); полный список находится в файле."
                 else empty end)
            ' "$_zrr_file"
            ;;
        url-check-task)
            jq -r --argjson limit "$LIMIT" '
                def shown: if . == null then "—" else tostring end;
                def clean: shown | gsub("[\\r\\n\\t]"; " ");
                [(.groups // [])[] | .adgroup_id as $group | .problems[]? |
                    [$group, (.type | shown), (.url | clean), (.ad_id | shown),
                     (.sitelink | shown), (.message | clean)]] as $problems |
                [(.groups // [])[] | .adgroup_id as $group | .suspended[]? |
                    "группа \($group): \(.)"] as $suspended |
                [(.groups // [])[] | .adgroup_id as $group | .resumed[]? |
                    "группа \($group): \(.)"] as $resumed |
                "Проверка: \(.id // "—")",
                "Кампания: \(.campaign_id // "—")",
                "Состояние: \(.status // "—")",
                "Завершена: \(.complete // false)",
                "Групп с данными: \((.groups // []) | length)",
                "Найдено проблем: \($problems | length)",
                "Приостановлено объектов: \($suspended | length)",
                ($suspended[: $limit][] | "- " + .),
                "Возобновлено объектов: \($resumed | length)",
                ($resumed[: $limit][] | "- " + .),
                (["Группа", "Тип", "Ссылка", "Объявление", "Быстрая ссылка", "Описание"] | @tsv),
                ($problems[: $limit][] | @tsv),
                (if ($problems | length) > $limit then
                    "Показаны первые \($limit) проблем; полный список находится в файле."
                 else empty end)
            ' "$_zrr_file"
            ;;
        *)
            jq -r '
                if type == "object" then
                    to_entries[:20][] | .key as $key | (.value | tostring) as $value |
                    "\($key): \($value[0:300])"
                elif type == "array" then
                    .[:20][] | tostring | .[0:300]
                else
                    tostring
                end
            ' "$_zrr_file"
            ;;
    esac
}

validate_response_body() {
    if command -v jq >/dev/null 2>&1; then
        jq -e 'type == "object" or type == "array"' "$BODY_TMP" >/dev/null 2>&1 || \
            die "ZoomKit вернул успешный код HTTP, но не JSON-объект или массив; ответ сохранён в $DESTINATION"
        return 0
    fi

    _zvrb_first=$(awk '
        {
            sub(/^[[:space:]]*/, "")
            if (length($0) > 0) {
                print substr($0, 1, 1)
                exit
            }
        }
    ' "$BODY_TMP")
    case "$_zvrb_first" in
        \{|\[) ;;
        *) die "ZoomKit вернул успешный код HTTP, но ответ не похож на JSON; он сохранён в $DESTINATION" ;;
    esac
}

COMMAND=${1:-help}
if [ $# -gt 0 ]; then
    shift
fi

if [ "$COMMAND" = report-wait ]; then
    exec sh "$SCRIPT_DIR/report-wait.sh" "$@"
fi

case "$COMMAND" in
    invoice-create|invoices-create|payment-link|payment-link-create|invoice-download)
        printf '%s\n' "Ошибка: официальный ZoomKit API 1.8.0 не поддерживает эту операцию." >&2
        printf '%s\n' "Доступны только balance и invoices; выставление и оплата выполняются в $PROFILE_URL" >&2
        exit 4
        ;;
esac

RESOURCE_ID=""
CLIENT_ID=""
CAMPAIGN_ID=""
TOKEN_ID=""
BIDRULE_ID=""
TASK_ID=""
BODY_FILE=""
OUTPUT_FILE=""
STATUS_FILTER=""
LIMIT=20
RAW=0
CONFIRM=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --id)
            require_value "$@"
            RESOURCE_ID=$2
            shift 2
            ;;
        --client)
            require_value "$@"
            CLIENT_ID=$2
            shift 2
            ;;
        --campaign)
            require_value "$@"
            CAMPAIGN_ID=$2
            shift 2
            ;;
        --token-id)
            require_value "$@"
            TOKEN_ID=$2
            shift 2
            ;;
        --bidrule)
            require_value "$@"
            BIDRULE_ID=$2
            shift 2
            ;;
        --task)
            require_value "$@"
            TASK_ID=$2
            shift 2
            ;;
        --body)
            require_value "$@"
            BODY_FILE=$2
            shift 2
            ;;
        --output)
            require_value "$@"
            OUTPUT_FILE=$2
            shift 2
            ;;
        --status)
            require_value "$@"
            STATUS_FILTER=$2
            shift 2
            ;;
        --limit)
            require_value "$@"
            LIMIT=$2
            shift 2
            ;;
        --raw)
            RAW=1
            shift
            ;;
        --confirm)
            CONFIRM=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "неизвестный параметр: $1"
            ;;
    esac
done

case "$LIMIT" in
    ''|*[!0-9]*) die "--limit должен быть целым числом" ;;
esac
[ "$LIMIT" -ge 1 ] && [ "$LIMIT" -le 1000 ] || die "--limit должен быть от 1 до 1000"
case "$STATUS_FILTER" in
    ''|wait|paid|cancelled) ;;
    *) die "--status должен быть wait, paid или cancelled" ;;
esac

METHOD=""
API_PATH=""
MUTATING=0
BODY_REQUIRED=0

case "$COMMAND" in
    help)
        usage
        exit 0
        ;;
    billing-capabilities)
        show_billing_capabilities
        exit 0
        ;;
    getting-started)
        show_getting_started
        exit 0
        ;;
    token) METHOD=GET; API_PATH="/token" ;;
    balance) METHOD=GET; API_PATH="/billing/balance" ;;
    invoices) METHOD=GET; API_PATH="/billing/invoices" ;;
    clients) METHOD=GET; API_PATH="/stats/clients" ;;
    reports) METHOD=GET; API_PATH="/stats/reports" ;;
    report)
        validate_positive_integer "$RESOURCE_ID" "--id"
        METHOD=GET
        API_PATH="/stats/reports/$RESOURCE_ID"
        ;;
    report-create)
        METHOD=POST
        API_PATH="/stats/reports"
        MUTATING=1
        BODY_REQUIRED=1
        ;;
    report-delete)
        validate_positive_integer "$RESOURCE_ID" "--id"
        METHOD=DELETE
        API_PATH="/stats/reports/$RESOURCE_ID"
        MUTATING=1
        ;;
    client-update)
        validate_positive_integer "$CLIENT_ID" "--client"
        METHOD=POST
        API_PATH="/yandex/direct/clients/$CLIENT_ID/update"
        MUTATING=1
        ;;
    bidrules)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        METHOD=GET
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/bidrules"
        ;;
    autotargeting)
        validate_positive_integer "$TOKEN_ID" "--token-id"
        METHOD=POST
        API_PATH="/yandex/direct/tokens/$TOKEN_ID/autotargeting"
        MUTATING=1
        ;;
    bidrule-common-update)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        METHOD=PUT
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/bidrules/common"
        MUTATING=1
        BODY_REQUIRED=1
        ;;
    bidrule-keywords-create)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        METHOD=POST
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/bidrules/keywords"
        MUTATING=1
        BODY_REQUIRED=1
        ;;
    bidrule-keywords-update)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        validate_positive_integer "$BIDRULE_ID" "--bidrule"
        METHOD=PUT
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/bidrules/keywords/$BIDRULE_ID"
        MUTATING=1
        BODY_REQUIRED=1
        ;;
    bidrule-keywords-delete)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        validate_positive_integer "$BIDRULE_ID" "--bidrule"
        METHOD=DELETE
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/bidrules/keywords/$BIDRULE_ID"
        MUTATING=1
        ;;
    url-check-settings)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        METHOD=GET
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/url-check-settings"
        ;;
    url-check-settings-update)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        METHOD=PUT
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/url-check-settings"
        MUTATING=1
        BODY_REQUIRED=1
        ;;
    url-check-tasks)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        METHOD=GET
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/url-check-tasks"
        ;;
    url-check-task)
        validate_positive_integer "$CAMPAIGN_ID" "--campaign"
        validate_positive_integer "$TASK_ID" "--task"
        METHOD=GET
        API_PATH="/yandex/direct/campaigns/$CAMPAIGN_ID/url-check-tasks/$TASK_ID"
        ;;
    *)
        usage >&2
        die "неизвестная команда: $COMMAND"
        ;;
esac

if [ "$BODY_REQUIRED" -eq 1 ]; then
    validate_body_file
    validate_command_body
elif [ -n "$BODY_FILE" ]; then
    die "команда $COMMAND не принимает --body"
fi

load_settings
REQUEST_URL="${ZOOMKIT_API_BASE_URL}${API_PATH}"

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'Метод: %s\n' "$METHOD"
    printf 'Адрес: %s\n' "$REQUEST_URL"
    if [ "$BODY_REQUIRED" -eq 1 ]; then
        BODY_BYTES=$(wc -c < "$BODY_FILE" | tr -d ' ')
        printf 'Тело: %s\n' "$BODY_FILE"
        printf 'Размер тела: %s байт\n' "$BODY_BYTES"
        printf 'Контрольная сумма: %s\n' "$(body_checksum "$BODY_FILE")"
        if [ "$BODY_BYTES" -le 3000 ]; then
            printf '%s\n' "Тело JSON:"
            jq -c . "$BODY_FILE"
        else
            printf '%s\n' "Тело больше 3000 байт; проверьте точное содержимое в указанном файле."
        fi
    else
        printf '%s\n' "Тело: отсутствует"
    fi
    printf '%s\n' "Сетевой запрос не выполнен."
    exit 0
fi

if [ "$MUTATING" -eq 1 ] && [ "$CONFIRM" -ne 1 ]; then
    printf '%s\n' "Ошибка: команда $COMMAND изменяет данные ZoomKit." >&2
    printf '%s\n' "Сначала проверьте её с --dry-run, затем явно повторите с --confirm." >&2
    exit 3
fi

require_token

CURL_BIN=${ZOOMKIT_CURL_BIN:-curl}
command -v "$CURL_BIN" >/dev/null 2>&1 || die "не найден curl"
CURL_CONNECT_TIMEOUT=${ZOOMKIT_CURL_CONNECT_TIMEOUT:-10}
CURL_MAX_TIME=${ZOOMKIT_CURL_MAX_TIME:-120}
validate_positive_integer "$CURL_CONNECT_TIMEOUT" "ZOOMKIT_CURL_CONNECT_TIMEOUT"
validate_positive_integer "$CURL_MAX_TIME" "ZOOMKIT_CURL_MAX_TIME"
[ "$CURL_CONNECT_TIMEOUT" -le 60 ] || die "ZOOMKIT_CURL_CONNECT_TIMEOUT должен быть не больше 60"
[ "$CURL_MAX_TIME" -le 600 ] || die "ZOOMKIT_CURL_MAX_TIME должен быть не больше 600"
[ "$CURL_CONNECT_TIMEOUT" -le "$CURL_MAX_TIME" ] || \
    die "ZOOMKIT_CURL_CONNECT_TIMEOUT не должен превышать ZOOMKIT_CURL_MAX_TIME"

umask 077
ZOOMKIT_TMP_ROOT=${TMPDIR:-/tmp}
ZOOMKIT_REQUEST_DIR=$(mktemp -d "$ZOOMKIT_TMP_ROOT/zoomkit.XXXXXX") || die "не удалось создать временный каталог"
HEADERS_FILE="$ZOOMKIT_REQUEST_DIR/headers.txt"
BODY_TMP="$ZOOMKIT_REQUEST_DIR/body.json"
REQUEST_HEADERS_FILE="$ZOOMKIT_REQUEST_DIR/request-headers.txt"
OUTPUT_TMP=""

cleanup() {
    rm -f "$HEADERS_FILE" "$BODY_TMP" "$REQUEST_HEADERS_FILE"
    if [ -n "$OUTPUT_TMP" ]; then
        rm -f "$OUTPUT_TMP"
    fi
    rmdir "$ZOOMKIT_REQUEST_DIR" 2>/dev/null || true
}
trap cleanup 0
trap 'exit 130' 2
trap 'exit 143' 15

{
    printf '%s\n' "Accept: application/json"
    printf 'Authorization: Bearer %s\n' "$ZOOMKIT_API_TOKEN"
} > "$REQUEST_HEADERS_FILE"
chmod 600 "$REQUEST_HEADERS_FILE" 2>/dev/null || true

if [ "$BODY_REQUIRED" -eq 1 ]; then
    if ! "$CURL_BIN" -q --silent --show-error \
        --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
        --request "$METHOD" \
        --dump-header "$HEADERS_FILE" \
        --output "$BODY_TMP" \
        --header "@$REQUEST_HEADERS_FILE" \
        --header "Content-Type: application/json" \
        --data-binary "@$BODY_FILE" \
        "$REQUEST_URL"; then
        die "не удалось подключиться к ZoomKit API"
    fi
else
    if ! "$CURL_BIN" -q --silent --show-error \
        --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
        --request "$METHOD" \
        --dump-header "$HEADERS_FILE" \
        --output "$BODY_TMP" \
        --header "@$REQUEST_HEADERS_FILE" \
        "$REQUEST_URL"; then
        die "не удалось подключиться к ZoomKit API"
    fi
fi

HTTP_STATUS=$(awk '/^HTTP\// { status = $2 } END { print status }' "$HEADERS_FILE")
case "$HTTP_STATUS" in
    2??) ;;
    '') die "ZoomKit API не вернул код HTTP" ;;
    *) handle_http_error ;;
esac

if [ -n "$OUTPUT_FILE" ]; then
    DESTINATION=$OUTPUT_FILE
else
    DESTINATION="$CACHE_DIR/latest-$COMMAND.json"
fi

DESTINATION_DIR=$(dirname -- "$DESTINATION")
mkdir -p "$DESTINATION_DIR"
OUTPUT_TMP=$(mktemp "$DESTINATION_DIR/.zoomkit-response.XXXXXX") || die "не удалось создать файл ответа"
cp "$BODY_TMP" "$OUTPUT_TMP"
chmod 600 "$OUTPUT_TMP" 2>/dev/null || true
mv -f "$OUTPUT_TMP" "$DESTINATION"
OUTPUT_TMP=""

validate_response_body

if [ "$RAW" -eq 1 ]; then
    cat "$BODY_TMP"
    printf '\n'
    printf 'Полный ответ сохранён: %s\n' "$DESTINATION" >&2
else
    render_response "$BODY_TMP"
    printf 'Полный ответ: %s\n' "$DESTINATION"
fi
