#!/bin/sh
# Прямые доступы к счётчику: список, проверка, выдача и изменение.
# Usage: grants.sh --counter ID --action list|get|add|update [options]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

usage() {
    echo "Использование: grants.sh --counter ID --action list|get|add|update" >&2
    echo "       [--login LOGIN] [--permission view|analyst|edit]" >&2
    echo "       [--comment TEXT] [--csv FILE] [--apply]" >&2
}

ACTION="list"
COUNTER=""
LOGIN=""
PERMISSION=""
COMMENT=""
PARTNER_DATA_ACCESS="false"
COMMENT_SET=""
CSV_OUT=""
APPLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --action)              ACTION="$2"; shift 2 ;;
        --counter)             COUNTER="$2"; shift 2 ;;
        --login)               LOGIN="$2"; shift 2 ;;
        --permission)          PERMISSION="$2"; shift 2 ;;
        --comment)             COMMENT="$2"; COMMENT_SET="1"; shift 2 ;;
        --csv)                 CSV_OUT="$2"; shift 2 ;;
        --apply)               APPLY="1"; shift ;;
        --help|-h)             usage; exit 0 ;;
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
TMP_BODY=$(mktemp "${METRIKA_TMPDIR}/metrika_grant_body.XXXXXX")
TMP_RESPONSE=$(mktemp "${METRIKA_TMPDIR}/metrika_grant_response.XXXXXX")
TMP_TSV=$(mktemp "${METRIKA_TMPDIR}/metrika_grants.XXXXXX")
trap 'rm -f "$TMP_BODY" "$TMP_RESPONSE" "$TMP_TSV"' EXIT

require_login() {
    require_single_line "$LOGIN" "логин Яндекса"
    require_max_characters "$LOGIN" 255 "логин Яндекса"
}

require_permission() {
    case "$PERMISSION" in
        view|analyst|edit) ;;
        *)
            echo "Ошибка: --permission должен быть view, analyst или edit." >&2
            exit 1
            ;;
    esac
    require_max_characters "$COMMENT" 255 "комментарий"
    if [ -n "$COMMENT" ]; then
        require_single_line "$COMMENT" "комментарий"
    fi
}

grant_summary() {
    management_json grant-summary "$1"
}

render_grants() {
    _rg_json_file="$1"
    management_json grants-tsv "$_rg_json_file" "$METRIKA_COUNTER_OWNER" > "$TMP_TSV"
    print_tsv_head "$TMP_TSV" 31
    _rg_total=$(( $(wc -l < "$TMP_TSV" | tr -d ' ') - 1 ))
    echo "Известных записей доступа вместе с владельцем: $_rg_total"
    echo "Примечание: представители аккаунта не входят в прямые доступы счётчика."
    if [ -n "$CSV_OUT" ]; then
        cp "$TMP_TSV" "$CSV_OUT"
        echo "Полный список: $CSV_OUT"
    fi
}

write_grant_body() {
    _wgb_login=$(json_escape "$LOGIN")
    _wgb_comment=$(json_escape "$COMMENT")
    printf '{"grant":{"user_login":"%s","perm":"%s","comment":"%s","partner_data_access":%s}}\n' \
        "$_wgb_login" "$PERMISSION" "$_wgb_comment" "$PARTNER_DATA_ACCESS" > "$TMP_BODY"
}

fetch_grant() {
    METRIKA_ALLOW_NOT_FOUND=1
    METRIKA_CAPTURE_HTTP_ERROR=1
    if metrika_mgmt_request GET "/management/v1/counter/$COUNTER/grant" "" \
        --data-urlencode "user_login=$LOGIN" > "$TMP_RESPONSE"; then
        unset METRIKA_ALLOW_NOT_FOUND METRIKA_CAPTURE_HTTP_ERROR
        return 0
    else
        _fg_status=$?
        _fg_http_status="$METRIKA_HTTP_STATUS"
        unset METRIKA_ALLOW_NOT_FOUND METRIKA_CAPTURE_HTTP_ERROR
        if [ "$_fg_status" -eq 4 ]; then
            return 4
        fi
        if [ "$_fg_http_status" = "400" ] && \
           management_json missing-grant-error "$TMP_RESPONSE"; then
            return 4
        fi
        echo "Ошибка HTTP ${_fg_http_status:-неизвестно}: не удалось проверить прямой доступ $LOGIN." >&2
        cat "$TMP_RESPONSE" >&2
        return "$_fg_status"
    fi
}

verify_grant_state() {
    if fetch_grant; then
        :
    else
        _vgs_status=$?
        echo "Ошибка: не удалось перечитать прямой доступ." >&2
        return "$_vgs_status"
    fi
    _vgs_login=$(management_json value "$TMP_RESPONSE" grant.user_login)
    _vgs_permission=$(management_json value "$TMP_RESPONSE" grant.perm)
    _vgs_comment=$(management_json value "$TMP_RESPONSE" grant.comment)
    _vgs_partner=$(management_json value "$TMP_RESPONSE" grant.partner_data_access)
    _vgs_partner="${_vgs_partner:-false}"
    _vgs_filters=$(management_json value "$TMP_RESPONSE" grant.access_filters)
    if [ "$_vgs_login" != "$LOGIN" ] || \
       [ "$_vgs_permission" != "$PERMISSION" ] || \
       [ "$_vgs_comment" != "$COMMENT" ] || \
       [ "$_vgs_partner" != "$PARTNER_DATA_ACCESS" ] || \
       { [ -n "$_vgs_filters" ] && [ "$_vgs_filters" != "[]" ]; }; then
        echo "Ошибка: фактические настройки прямого доступа отличаются от запрошенных." >&2
        grant_summary "$TMP_RESPONSE" >&2
        return 1
    fi
}

show_write_plan() {
    echo "Счётчик: $COUNTER"
    echo "Текущая роль: $METRIKA_COUNTER_PERMISSION"
    echo "Действие: $1"
    echo "Логин: $LOGIN"
    [ -n "$PERMISSION" ] && echo "Новая роль: $PERMISSION"
    [ -n "$COMMENT" ] && echo "Комментарий: $COMMENT"
    if [ -z "$APPLY" ]; then
        echo "Только проверка. Добавьте --apply, чтобы выполнить изменение."
    fi
}

case "$ACTION" in
    list)
        load_counter_access "$COUNTER"
        metrika_mgmt_get "/management/v1/counter/$COUNTER/grants" > "$TMP_RESPONSE"
        echo "Ваша роль: $METRIKA_COUNTER_PERMISSION"
        render_grants "$TMP_RESPONSE"
        ;;

    get)
        require_login
        load_counter_access "$COUNTER"
        if [ "$LOGIN" = "$METRIKA_COUNTER_OWNER" ]; then
            echo "Логин: $LOGIN"
            echo "Роль: own"
            echo "Источник: владелец счётчика"
            exit 0
        fi
        if fetch_grant; then
            echo "Источник: прямой доступ"
            grant_summary "$TMP_RESPONSE"
        else
            _get_status=$?
            if [ "$_get_status" -eq 4 ]; then
                echo "Логин: $LOGIN"
                echo "Прямой доступ: не найден"
                echo "Примечание: представительский доступ при этом возможен."
                exit 3
            fi
            exit "$_get_status"
        fi
        ;;

    add)
        require_login
        [ -z "$PERMISSION" ] && PERMISSION="view"
        require_permission
        require_counter_permission "$COUNTER" "own edit" "выдавать доступ к счётчику"
        if [ "$LOGIN" = "$METRIKA_COUNTER_OWNER" ]; then
            echo "Ошибка: владелец счётчика уже имеет полный доступ." >&2
            exit 1
        fi
        if fetch_grant; then
            _existing_permission=$(management_json value "$TMP_RESPONSE" grant.perm)
            _existing_comment=$(management_json value "$TMP_RESPONSE" grant.comment)
            _existing_filters=$(management_json value "$TMP_RESPONSE" grant.access_filters)
            if [ -n "$_existing_filters" ] && [ "$_existing_filters" != "[]" ]; then
                echo "Ошибка: у логина $LOGIN уже есть доступ с фильтром данных." >&2
                echo "Команда add не заменяет и не расширяет такой доступ." >&2
                grant_summary "$TMP_RESPONSE" >&2
                exit 1
            fi
            [ -z "$COMMENT_SET" ] && COMMENT="$_existing_comment"
            if [ "$_existing_permission" = "$PERMISSION" ] && \
               [ "$_existing_comment" = "$COMMENT" ]; then
                echo "У логина $LOGIN уже есть запрошенная роль; настройки не изменены:"
                grant_summary "$TMP_RESPONSE"
                exit 0
            fi
            echo "Ошибка: у логина $LOGIN уже есть прямая роль $_existing_permission; используйте --action update." >&2
            exit 1
        else
            _add_lookup=$?
            [ "$_add_lookup" -eq 4 ] || exit "$_add_lookup"
        fi
        write_grant_body
        show_write_plan "выдать прямой доступ к счётчику"
        [ -z "$APPLY" ] && exit 0

        metrika_mgmt_request POST "/management/v1/counter/$COUNTER/grants" "$TMP_BODY" > "$TMP_RESPONSE"
        verify_grant_state
        echo "Выдано и проверено:"
        grant_summary "$TMP_RESPONSE"
        ;;

    update)
        require_login
        if [ -z "$PERMISSION" ]; then
            echo "Ошибка: для update требуется --permission view|analyst|edit." >&2
            exit 1
        fi
        require_counter_permission "$COUNTER" "own edit" "изменять доступ к счётчику"
        if fetch_grant; then
            :
        else
            _update_lookup=$?
            if [ "$_update_lookup" -eq 4 ]; then
                echo "Ошибка: у $LOGIN нет прямого доступа; используйте --action add." >&2
            fi
            exit "$_update_lookup"
        fi
        _update_comment=$(management_json value "$TMP_RESPONSE" grant.comment)
        _update_partner=$(management_json value "$TMP_RESPONSE" grant.partner_data_access)
        _update_filters=$(management_json value "$TMP_RESPONSE" grant.access_filters)
        if [ -n "$_update_filters" ] && [ "$_update_filters" != "[]" ]; then
            echo "Ошибка: изменение доступа с фильтром этим сценарием не поддерживается." >&2
            echo "Это защищает существующее ограничение данных от случайного удаления." >&2
            exit 1
        fi
        [ -z "$COMMENT_SET" ] && COMMENT="$_update_comment"
        PARTNER_DATA_ACCESS="${_update_partner:-false}"
        require_permission
        echo "Текущий доступ:"
        grant_summary "$TMP_RESPONSE"
        write_grant_body
        echo "Новые значения:"
        show_write_plan "изменить прямой доступ к счётчику"
        [ -z "$APPLY" ] && exit 0

        metrika_mgmt_request PUT "/management/v1/counter/$COUNTER/grant" "$TMP_BODY" > "$TMP_RESPONSE"
        verify_grant_state
        echo "Изменено и проверено:"
        grant_summary "$TMP_RESPONSE"
        ;;

    *)
        echo "Ошибка: неизвестное действие '$ACTION'." >&2
        usage
        exit 1
        ;;
esac
