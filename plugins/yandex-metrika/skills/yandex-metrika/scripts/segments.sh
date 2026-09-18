#!/bin/sh
# API-сегменты: список, просмотр, создание и удаление.
# Usage: segments.sh --counter ID --action list|get|create|delete [options]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

usage() {
    echo "Использование: segments.sh --counter ID --action list|get|create|delete" >&2
    echo "       [--segment-id ID] [--name NAME] [--expression FILTER] [--csv FILE] [--apply]" >&2
}

ACTION="list"
COUNTER=""
SEGMENT_ID=""
NAME=""
EXPRESSION=""
CSV_OUT=""
APPLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --action)     ACTION="$2"; shift 2 ;;
        --counter)    COUNTER="$2"; shift 2 ;;
        --segment-id) SEGMENT_ID="$2"; shift 2 ;;
        --name)       NAME="$2"; shift 2 ;;
        --expression) EXPRESSION="$2"; shift 2 ;;
        --csv)        CSV_OUT="$2"; shift 2 ;;
        --apply)      APPLY="1"; shift ;;
        --help|-h)    usage; exit 0 ;;
        *)
            echo "Ошибка: неизвестный аргумент: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [ -z "$COUNTER" ]; then
    echo "Ошибка: требуется --counter ID." >&2
    usage
    exit 1
fi
require_positive_integer "$COUNTER" "ID счётчика"
load_config

umask 077
TMP_BODY=$(mktemp "${METRIKA_TMPDIR}/metrika_segment_body.XXXXXX")
TMP_RESPONSE=$(mktemp "${METRIKA_TMPDIR}/metrika_segment_response.XXXXXX")
TMP_TSV=$(mktemp "${METRIKA_TMPDIR}/metrika_segments.XXXXXX")
trap 'rm -f "$TMP_BODY" "$TMP_RESPONSE" "$TMP_TSV"' EXIT

render_segments() {
    _rs_json_file="$1"
    management_json segments-tsv "$_rs_json_file" > "$TMP_TSV"
    print_tsv_head "$TMP_TSV" 31
    _rs_total=$(( $(wc -l < "$TMP_TSV" | tr -d ' ') - 1 ))
    echo "Всего API-сегментов: $_rs_total"
    if [ -n "$CSV_OUT" ]; then
        cp "$TMP_TSV" "$CSV_OUT"
        echo "Полный список: $CSV_OUT"
    fi
}

write_segment_body() {
    _wsb_name=$(json_escape "$NAME")
    _wsb_expression=$(json_escape "$EXPRESSION")
    printf '{"segment":{"name":"%s","expression":"%s"}}\n' \
        "$_wsb_name" "$_wsb_expression" > "$TMP_BODY"
}

verify_segment_state() {
    _vss_segment_id="$1"
    if metrika_mgmt_get "/management/v1/counter/$COUNTER/apisegment/segment/$_vss_segment_id" > "$TMP_RESPONSE"; then
        :
    else
        _vss_status=$?
        echo "Ошибка: не удалось перечитать API-сегмент." >&2
        return "$_vss_status"
    fi
    _vss_actual_id=$(management_json value "$TMP_RESPONSE" segment.segment_id)
    _vss_counter_id=$(management_json value "$TMP_RESPONSE" segment.counter_id)
    _vss_name=$(management_json value "$TMP_RESPONSE" segment.name)
    _vss_expression=$(management_json value "$TMP_RESPONSE" segment.expression)
    _vss_status=$(management_json value "$TMP_RESPONSE" segment.status)
    _vss_source=$(management_json value "$TMP_RESPONSE" segment.segment_source)
    if [ "$_vss_actual_id" != "$_vss_segment_id" ] || \
       [ "$_vss_counter_id" != "$COUNTER" ] || \
       [ "$_vss_name" != "$NAME" ] || \
       [ "$_vss_expression" != "$EXPRESSION" ] || \
       [ "$_vss_status" != "active" ] || \
       [ "$_vss_source" != "api" ]; then
        echo "Ошибка: фактическое состояние API-сегмента отличается от запрошенного." >&2
        management_json segment-summary "$TMP_RESPONSE" >&2
        return 1
    fi
}

require_api_segment() {
    _ras_expected_id="$1"
    _ras_actual_id=$(management_json value "$TMP_RESPONSE" segment.segment_id)
    _ras_counter_id=$(management_json value "$TMP_RESPONSE" segment.counter_id)
    _ras_source=$(management_json value "$TMP_RESPONSE" segment.segment_source)
    if [ "$_ras_actual_id" != "$_ras_expected_id" ] || \
       [ "$_ras_counter_id" != "$COUNTER" ]; then
        echo "Ошибка: ответ относится не к запрошенному сегменту или счётчику." >&2
        exit 1
    fi
    if [ "$_ras_source" != "api" ]; then
        echo "Ошибка: изменять или удалять можно только сегмент с источником api." >&2
        echo "Сегмент из интерфейса Метрики оставлен без изменений." >&2
        exit 1
    fi
}

fetch_segment_from_list() {
    _fsfl_segment_id="$1"
    metrika_mgmt_get "/management/v1/counter/$COUNTER/apisegment/segments" > "$TMP_TSV"
    if management_json find-segment-id "$TMP_TSV" "$_fsfl_segment_id" > "$TMP_RESPONSE"; then
        return 0
    else
        return $?
    fi
}

show_write_plan() {
    echo "Счётчик: $COUNTER"
    echo "Текущая роль: $METRIKA_COUNTER_PERMISSION"
    echo "Действие: $1"
    echo "Название: $NAME"
    echo "Выражение: $EXPRESSION"
    if [ -z "$APPLY" ]; then
        echo "Только проверка. Добавьте --apply, чтобы выполнить изменение."
    fi
}

case "$ACTION" in
    list)
        metrika_mgmt_get "/management/v1/counter/$COUNTER/apisegment/segments" > "$TMP_RESPONSE"
        render_segments "$TMP_RESPONSE"
        ;;

    get)
        require_positive_integer "$SEGMENT_ID" "ID сегмента"
        metrika_mgmt_get "/management/v1/counter/$COUNTER/apisegment/segment/$SEGMENT_ID" > "$TMP_RESPONSE"
        management_json segment-summary "$TMP_RESPONSE"
        ;;

    create)
        require_single_line "$NAME" "название сегмента"
        require_single_line "$EXPRESSION" "выражение сегмента"
        require_max_characters "$NAME" 255 "название сегмента"
        require_max_characters "$EXPRESSION" 65535 "выражение сегмента"
        require_counter_permission "$COUNTER" "own edit analyst" "создавать API-сегменты"
        metrika_mgmt_get "/management/v1/counter/$COUNTER/apisegment/segments" > "$TMP_TSV"
        if management_json find-segment "$TMP_TSV" -- "$NAME" "$EXPRESSION" > "$TMP_RESPONSE"; then
            echo "Точно такой активный API-сегмент уже существует. Новая запись не создана:"
            management_json segment-summary "$TMP_RESPONSE"
            exit 0
        else
            _create_lookup=$?
            case "$_create_lookup" in
                4) ;;
                5)
                    echo "Ошибка: активный API-сегмент с названием '$NAME' уже существует, но его выражение отличается." >&2
                    echo "Укажите другое название или измените существующий сегмент." >&2
                    exit 1
                    ;;
                *) exit "$_create_lookup" ;;
            esac
        fi
        write_segment_body
        show_write_plan "создать API-сегмент"
        [ -z "$APPLY" ] && exit 0

        metrika_mgmt_request POST "/management/v1/counter/$COUNTER/apisegment/segments" "$TMP_BODY" > "$TMP_RESPONSE"
        SEGMENT_ID=$(management_json value "$TMP_RESPONSE" segment.segment_id)
        if [ -z "$SEGMENT_ID" ]; then
            echo "Ошибка: API не вернул segment_id." >&2
            exit 1
        fi
        verify_segment_state "$SEGMENT_ID"
        echo "Создано и проверено:"
        management_json segment-summary "$TMP_RESPONSE"
        ;;

    delete)
        require_positive_integer "$SEGMENT_ID" "ID сегмента"
        require_counter_permission "$COUNTER" "own edit" "удалять API-сегменты"
        if fetch_segment_from_list "$SEGMENT_ID"; then
            :
        else
            _delete_lookup=$?
            if [ "$_delete_lookup" -eq 4 ]; then
                echo "API-сегмент $SEGMENT_ID уже отсутствует. Удалять нечего."
                exit 0
            fi
            exit "$_delete_lookup"
        fi
        require_api_segment "$SEGMENT_ID"
        _delete_status=$(management_json value "$TMP_RESPONSE" segment.status)
        if [ "$_delete_status" = "deleted" ]; then
            echo "API-сегмент $SEGMENT_ID уже удалён."
            exit 0
        fi
        if [ "$_delete_status" != "active" ]; then
            echo "Ошибка: неизвестное состояние сегмента: ${_delete_status:-пусто}." >&2
            exit 1
        fi
        NAME=$(management_json value "$TMP_RESPONSE" segment.name)
        EXPRESSION=$(management_json value "$TMP_RESPONSE" segment.expression)
        echo "Предупреждение: API не сообщает, используется ли сегмент во внешних системах."
        show_write_plan "удалить API-сегмент $SEGMENT_ID"
        [ -z "$APPLY" ] && exit 0

        metrika_mgmt_request DELETE "/management/v1/counter/$COUNTER/apisegment/segment/$SEGMENT_ID" "" > "$TMP_RESPONSE"
        if fetch_segment_from_list "$SEGMENT_ID"; then
            _deleted_status=$(management_json value "$TMP_RESPONSE" segment.status)
            if [ "$_deleted_status" != "deleted" ]; then
                echo "Ошибка: у сегмента осталось состояние '${_deleted_status:-неизвестно}'." >&2
                exit 1
            fi
            echo "Удалено и проверено: у сегмента $SEGMENT_ID состояние deleted."
        else
            _verify_result=$?
            if [ "$_verify_result" -eq 4 ]; then
                echo "Удалено и проверено: сегмент $SEGMENT_ID больше не возвращается."
            else
                exit "$_verify_result"
            fi
        fi
        ;;

    *)
        echo "Ошибка: неизвестное действие '$ACTION'." >&2
        usage
        exit 1
        ;;
esac
