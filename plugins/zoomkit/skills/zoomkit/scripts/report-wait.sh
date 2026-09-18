#!/bin/sh
# Ограниченное ожидание уже созданного отчёта ZoomKit.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SKILL_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ZOOMKIT_SCRIPT="$SCRIPT_DIR/zoomkit.sh"
REPORT_ID=""
INTERVAL=5
TIMEOUT=55
OUTPUT_FILE=""

usage() {
    cat <<'EOF'
Ожидание отчёта ZoomKit

Использование:
  sh scripts/zoomkit.sh report-wait --id ID [--interval СЕК] [--timeout СЕК] [--output ФАЙЛ]

Параметры:
  --id ID          Идентификатор уже созданного отчёта
  --interval СЕК   Пауза между проверками, от 1 до 30; по умолчанию 5
  --timeout СЕК    Общее ожидание, от 0 до 55; по умолчанию 55
  --output ФАЙЛ    Куда сохранять последнее полное состояние отчёта
EOF
}

die() {
    printf 'Ошибка: %s\n' "$1" >&2
    exit "${2:-1}"
}

require_value() {
    [ $# -ge 2 ] || die "после $1 требуется значение"
}

validate_positive_integer() {
    _zrw_value=$1
    _zrw_name=$2
    case "$_zrw_value" in
        ''|*[!0-9]*) die "$_zrw_name должен быть целым числом" ;;
    esac
    [ "$_zrw_value" -gt 0 ] 2>/dev/null || die "$_zrw_name должен быть больше нуля"
}

validate_range() {
    _zrw_value=$1
    _zrw_name=$2
    _zrw_min=$3
    _zrw_max=$4
    case "$_zrw_value" in
        ''|*[!0-9]*) die "$_zrw_name должен быть целым числом" ;;
    esac
    [ "$_zrw_value" -ge "$_zrw_min" ] && [ "$_zrw_value" -le "$_zrw_max" ] || \
        die "$_zrw_name должен быть от $_zrw_min до $_zrw_max"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --id)
            require_value "$@"
            REPORT_ID=$2
            shift 2
            ;;
        --interval)
            require_value "$@"
            INTERVAL=$2
            shift 2
            ;;
        --timeout)
            require_value "$@"
            TIMEOUT=$2
            shift 2
            ;;
        --output)
            require_value "$@"
            OUTPUT_FILE=$2
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *) die "неизвестный параметр: $1" ;;
    esac
done

validate_positive_integer "$REPORT_ID" "--id"
validate_range "$INTERVAL" "--interval" 1 30
validate_range "$TIMEOUT" "--timeout" 0 55
command -v jq >/dev/null 2>&1 || die "для ожидания отчёта нужен jq"

if [ -z "$OUTPUT_FILE" ]; then
    OUTPUT_FILE="${ZOOMKIT_CACHE_DIR:-$SKILL_DIR/cache}/report-$REPORT_ID.json"
fi

umask 077
OUTPUT_DIR=$(dirname -- "$OUTPUT_FILE")
mkdir -p "$OUTPUT_DIR"
STATE_FILE=$(mktemp "$OUTPUT_DIR/.zoomkit-report-wait.$REPORT_ID.XXXXXX") || \
    die "не удалось создать временный файл ожидания"
PUBLISH_TMP=""

cleanup() {
    rm -f "$STATE_FILE"
    if [ -n "$PUBLISH_TMP" ]; then
        rm -f "$PUBLISH_TMP"
    fi
}
trap cleanup 0
trap 'exit 130' 2
trap 'exit 143' 15

publish_state() {
    PUBLISH_TMP=$(mktemp "$OUTPUT_DIR/.zoomkit-report-state.XXXXXX") || \
        die "не удалось подготовить файл состояния"
    cp "$STATE_FILE" "$PUBLISH_TMP"
    chmod 600 "$PUBLISH_TMP" 2>/dev/null || true
    mv -f "$PUBLISH_TMP" "$OUTPUT_FILE"
    PUBLISH_TMP=""
}

STARTED_AT=$(date +%s)
LAST_STATUS=""

while :; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - STARTED_AT))

    if [ -n "$LAST_STATUS" ] && [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        printf 'Ожидание остановлено через %s сек.; текущий статус: %s.\n' "$ELAPSED" "$LAST_STATUS" >&2
        printf 'Повторите: sh scripts/zoomkit.sh report-wait --id %s\n' "$REPORT_ID" >&2
        exit 5
    fi

    REQUEST_TIMEOUT=$((TIMEOUT - ELAPSED))
    if [ "$REQUEST_TIMEOUT" -le 0 ]; then
        REQUEST_TIMEOUT=1
    elif [ "$REQUEST_TIMEOUT" -gt 15 ]; then
        REQUEST_TIMEOUT=15
    fi

    ZOOMKIT_CURL_CONNECT_TIMEOUT="$REQUEST_TIMEOUT" \
    ZOOMKIT_CURL_MAX_TIME="$REQUEST_TIMEOUT" \
        sh "$ZOOMKIT_SCRIPT" report --id "$REPORT_ID" --output "$STATE_FILE" >/dev/null

    RESPONSE_ID=$(jq -r '
        if type == "object" and (.id | type == "number") then (.id | tostring)
        else "" end
    ' "$STATE_FILE")
    [ "$RESPONSE_ID" = "$REPORT_ID" ] || \
        die "ZoomKit вернул отчёт $RESPONSE_ID вместо $REPORT_ID; ответ не опубликован"

    STATUS=$(jq -r 'if (.status | type) == "string" then .status else "" end' "$STATE_FILE")
    publish_state
    LAST_STATUS=$STATUS
    NOW=$(date +%s)
    ELAPSED=$((NOW - STARTED_AT))

    case "$STATUS" in
        READY)
            printf 'Отчёт %s готов за %s сек.\n' "$REPORT_ID" "$ELAPSED"
            jq -r '
                [(.reports // {}) | to_entries[] |
                    select((.value | type) == "object" and (.value.error? != null)) |
                    "\(.key): \(.value.error)"] as $errors |
                "Тип: \(.type // "—")",
                "Период: \(.date_start // "—") — \(.date_end // "—")",
                "Клиентов: \((.clients // []) | length)",
                "Результатов: \((.reports // {}) | length)",
                "Клиентских ошибок: \($errors | length)",
                ($errors[:10][] | "- " + .)
            ' "$STATE_FILE"
            printf 'Полный ответ: %s\n' "$OUTPUT_FILE"
            exit 0
            ;;
        FAILED)
            printf 'Ошибка: отчёт %s завершился со статусом FAILED.\n' "$REPORT_ID" >&2
            printf 'Полный ответ: %s\n' "$OUTPUT_FILE" >&2
            exit 6
            ;;
        CREATED|PROCESSED)
            printf 'Отчёт %s: %s, прошло %s сек.\n' "$REPORT_ID" "$STATUS" "$ELAPSED"
            ;;
        *)
            die "в ответе отчёта $REPORT_ID нет известного статуса; полный ответ: $OUTPUT_FILE"
            ;;
    esac

    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        printf 'Ожидание остановлено через %s сек.; текущий статус: %s.\n' "$ELAPSED" "$STATUS" >&2
        printf 'Повторите: sh scripts/zoomkit.sh report-wait --id %s\n' "$REPORT_ID" >&2
        exit 5
    fi

    REMAINING=$((TIMEOUT - ELAPSED))
    SLEEP_FOR=$INTERVAL
    if [ "$SLEEP_FOR" -gt "$REMAINING" ]; then
        SLEEP_FOR=$REMAINING
    fi
    sleep "$SLEEP_FOR"
done
