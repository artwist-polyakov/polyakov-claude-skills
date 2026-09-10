#!/bin/sh
# Common functions for Yandex Metrika API skill
# POSIX sh compatible — no bashisms

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/.env"
CACHE_DIR="$SCRIPT_DIR/../cache"

METRIKA_API="https://api-metrika.yandex.net"

# Ensure tmp directory exists
METRIKA_TMPDIR="${TMPDIR:-/tmp}"
mkdir -p "$METRIKA_TMPDIR"

# --------------- Config ---------------

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        _lc_mode=""
        if stat -f '%Lp' "$CONFIG_FILE" >/dev/null 2>&1; then
            _lc_mode=$(stat -f '%Lp' "$CONFIG_FILE")
        elif stat -c '%a' "$CONFIG_FILE" >/dev/null 2>&1; then
            _lc_mode=$(stat -c '%a' "$CONFIG_FILE")
        fi
        case "$_lc_mode" in
            *[1-7][0-7]|*[0-7][1-7])
                echo "Ошибка: config/.env доступен группе или другим пользователям." >&2
                echo "Исправьте права: chmod 600 config/.env" >&2
                exit 1
                ;;
        esac
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi

    if [ -z "${YANDEX_METRIKA_TOKEN:-}" ]; then
        echo "Error: YANDEX_METRIKA_TOKEN not found." >&2
        echo "Set in config/.env or environment. See config/README.md." >&2
        exit 1
    fi
    require_single_line "$YANDEX_METRIKA_TOKEN" "OAuth-токен"
}

# write_metrika_headers <file> [json]
# The OAuth token must not appear in curl argv. The caller creates a private
# temporary file and removes it immediately after the request.
write_metrika_headers() {
    _wmh_file="$1"
    _wmh_format="${2:-}"
    {
        printf 'Authorization: OAuth %s\n' "$YANDEX_METRIKA_TOKEN"
        printf 'Accept-Charset: utf-8\n'
        printf 'Accept-Language: ru\n'
        if [ "$_wmh_format" = "json" ]; then
            printf 'Content-Type: application/json; charset=UTF-8\n'
        fi
    } > "$_wmh_file"
}

# management_json <action> <file> [argument]
# Robust JSON handling is needed only for mutable management objects.
management_json() {
    if command -v python3 >/dev/null 2>&1; then
        python3 "$SCRIPT_DIR/management_json.py" "$@"
    elif command -v uv >/dev/null 2>&1; then
        uv run --script "$SCRIPT_DIR/management_json.py" "$@"
    else
        echo "Ошибка: для управления сегментами и доступами нужен Python 3.10+ или uv." >&2
        return 1
    fi
}

# --------------- Cache helpers ---------------

# cache_dir_for_counter <counter_id>
cache_dir_for_counter() {
    _cdc_dir="$CACHE_DIR/counter_$1"
    mkdir -p "$_cdc_dir/reports"
    echo "$_cdc_dir"
}

# cache_key <params_string> — deterministic hash via cksum
cache_key() {
    printf '%s' "$1" | cksum | awk '{print $1}'
}

# cache_get <file_path> — prints cached file if exists and not empty
# Returns 0 if cache hit, 1 if miss
cache_get() {
    if [ -f "$1" ] && [ -s "$1" ]; then
        cat "$1"
        return 0
    fi
    return 1
}

# cache_put <file_path> — reads stdin, writes to file
cache_put() {
    mkdir -p "$(dirname "$1")"
    cat > "$1"
}

# --------------- API helpers ---------------

# metrika_get <path> [extra_curl_args...]
# Makes authenticated GET request, returns body. Headers saved to temp file.
metrika_get() {
    _mg_path="$1"
    shift
    _mg_url="${METRIKA_API}${_mg_path}"
    _mg_headers=$(mktemp "${METRIKA_TMPDIR}/metrika_headers.XXXXXX")
    _mg_auth_headers=$(mktemp "${METRIKA_TMPDIR}/metrika_auth.XXXXXX")
    write_metrika_headers "$_mg_auth_headers"

    _mg_body=$(YANDEX_METRIKA_TOKEN= curl -q --silent --show-error -G -D "$_mg_headers" \
        -H "@$_mg_auth_headers" \
        "$@" \
        "$_mg_url") || {
        rm -f "$_mg_headers" "$_mg_auth_headers"
        echo "Error: curl failed for $_mg_url" >&2
        return 1
    }

    # Check for 429
    _mg_status=$(awk '/^HTTP\// {status=$2} END {print status}' "$_mg_headers")
    if [ "$_mg_status" = "429" ]; then
        _mg_retry=$(grep -i 'Retry-After' "$_mg_headers" | sed 's/[^0-9]//g' | head -1)
        rm -f "$_mg_headers" "$_mg_auth_headers"
        # Only retry once (guard via env var)
        if [ -z "${_METRIKA_RETRY_DONE:-}" ] && [ -n "$_mg_retry" ] && [ "$_mg_retry" -le 60 ] 2>/dev/null; then
            _mg_jitter=$(awk 'BEGIN{srand(); printf "%d", rand()*3}')
            _mg_wait=$(( _mg_retry + _mg_jitter ))
            echo "Rate limited. Waiting ${_mg_wait}s (Retry-After: ${_mg_retry}s)..." >&2
            sleep "$_mg_wait"
            _METRIKA_RETRY_DONE=1 metrika_get "$_mg_path" "$@"
            return $?
        else
            echo "Error: Rate limit exceeded (429). Metrika quota: ~200 req/5min." >&2
            echo "Wait ~5 minutes and retry." >&2
            return 1
        fi
    fi

    # Check for HTTP errors
    if [ -n "$_mg_status" ] && [ "$_mg_status" -ge 400 ] 2>/dev/null; then
        rm -f "$_mg_headers" "$_mg_auth_headers"
        echo "Error: HTTP $_mg_status from $_mg_url" >&2
        echo "$_mg_body" >&2
        return 1
    fi

    rm -f "$_mg_headers" "$_mg_auth_headers"
    printf '%s' "$_mg_body"
}

# metrika_get_csv <path> <output_file> [extra_curl_args...]
# Downloads CSV report to file. Returns 0 on success.
metrika_get_csv() {
    _mgc_path="$1"
    _mgc_output="$2"
    shift 2
    _mgc_url="${METRIKA_API}${_mgc_path}"
    _mgc_headers=$(mktemp "${METRIKA_TMPDIR}/metrika_headers.XXXXXX")
    _mgc_auth_headers=$(mktemp "${METRIKA_TMPDIR}/metrika_auth.XXXXXX")
    write_metrika_headers "$_mgc_auth_headers"

    YANDEX_METRIKA_TOKEN= curl -q --silent --show-error -G -D "$_mgc_headers" \
        -H "@$_mgc_auth_headers" \
        -o "$_mgc_output" \
        "$@" \
        "$_mgc_url" || {
        rm -f "$_mgc_headers" "$_mgc_auth_headers"
        echo "Error: curl failed for $_mgc_url" >&2
        return 1
    }

    _mgc_status=$(awk '/^HTTP\// {status=$2} END {print status}' "$_mgc_headers")
    if [ "$_mgc_status" = "429" ]; then
        _mgc_retry=$(grep -i 'Retry-After' "$_mgc_headers" | sed 's/[^0-9]//g' | head -1)
        rm -f "$_mgc_headers" "$_mgc_auth_headers"
        if [ -z "${_METRIKA_RETRY_DONE:-}" ] && [ -n "$_mgc_retry" ] && [ "$_mgc_retry" -le 60 ] 2>/dev/null; then
            _mgc_jitter=$(awk 'BEGIN{srand(); printf "%d", rand()*3}')
            _mgc_wait=$(( _mgc_retry + _mgc_jitter ))
            echo "Rate limited. Waiting ${_mgc_wait}s..." >&2
            sleep "$_mgc_wait"
            _METRIKA_RETRY_DONE=1 metrika_get_csv "$_mgc_path" "$_mgc_output" "$@"
            return $?
        else
            echo "Error: Rate limit exceeded (429). Wait ~5 minutes." >&2
            return 1
        fi
    fi

    if [ -n "$_mgc_status" ] && [ "$_mgc_status" -ge 400 ] 2>/dev/null; then
        rm -f "$_mgc_headers" "$_mgc_auth_headers"
        echo "Error: HTTP $_mgc_status" >&2
        cat "$_mgc_output" >&2
        return 1
    fi

    rm -f "$_mgc_headers" "$_mgc_auth_headers"
    return 0
}

# metrika_mgmt_get <path> [extra_curl_args...]
# Management API with simple backoff (2/4s) only for 429.
metrika_mgmt_get() {
    _mmg_path="$1"
    shift
    _mmg_attempt=0
    _mmg_max=3
    _mmg_delay=2
    _mmg_response=$(mktemp "${METRIKA_TMPDIR}/metrika_mgmt_get.XXXXXX")

    while [ "$_mmg_attempt" -lt "$_mmg_max" ]; do
        if metrika_mgmt_request GET "$_mmg_path" "" "$@" > "$_mmg_response"; then
            cat "$_mmg_response"
            rm -f "$_mmg_response"
            return 0
        else
            _mmg_result=$?
            _mmg_http_status="$METRIKA_HTTP_STATUS"
        fi
        _mmg_attempt=$(( _mmg_attempt + 1 ))
        if [ "$_mmg_http_status" = "429" ] && [ "$_mmg_attempt" -lt "$_mmg_max" ]; then
            echo "Повтор API управления ${_mmg_attempt}/${_mmg_max} через ${_mmg_delay} с из-за HTTP 429..." >&2
            sleep "$_mmg_delay"
            _mmg_delay=$(( _mmg_delay * 2 ))
        else
            rm -f "$_mmg_response"
            return "$_mmg_result"
        fi
    done
    rm -f "$_mmg_response"
    return 1
}

# metrika_mgmt_request <METHOD> <path> <body_file_or_empty> [extra_curl_args...]
# One-shot Management API request. Mutating requests are deliberately never
# retried: after a broken connection their outcome may be unknown.
metrika_mgmt_request() {
    _mmr_method="$1"
    _mmr_path="$2"
    _mmr_body_file="$3"
    shift 3

    case "$_mmr_method" in
        GET|POST|PUT|DELETE) ;;
        *)
            echo "Ошибка: неподдерживаемый метод HTTP: $_mmr_method" >&2
            return 1
            ;;
    esac

    if [ -n "$_mmr_body_file" ] && [ ! -f "$_mmr_body_file" ]; then
        echo "Ошибка: файл с телом JSON не найден: $_mmr_body_file" >&2
        return 1
    fi

    _mmr_url="${METRIKA_API}${_mmr_path}"
    _mmr_headers=$(mktemp "${METRIKA_TMPDIR}/metrika_headers.XXXXXX")
    _mmr_auth_headers=$(mktemp "${METRIKA_TMPDIR}/metrika_auth.XXXXXX")
    if [ -n "$_mmr_body_file" ]; then
        write_metrika_headers "$_mmr_auth_headers" json
    else
        write_metrika_headers "$_mmr_auth_headers"
    fi
    METRIKA_HTTP_STATUS=""

    if [ -n "$_mmr_body_file" ]; then
        _mmr_body=$(YANDEX_METRIKA_TOKEN= curl -q --silent --show-error -X "$_mmr_method" -D "$_mmr_headers" \
            -H "@$_mmr_auth_headers" \
            --data-binary "@$_mmr_body_file" \
            "$@" \
            "$_mmr_url") || {
            _mmr_curl_status=$?
            rm -f "$_mmr_headers" "$_mmr_auth_headers"
            echo "Ошибка curl при запросе $_mmr_method $_mmr_url" >&2
            if [ "$_mmr_method" != "GET" ]; then
                echo "Результат может быть неизвестен. Перед повтором перечитайте объект." >&2
            fi
            return "$_mmr_curl_status"
        }
    else
        _mmr_body=$(YANDEX_METRIKA_TOKEN= curl -q --silent --show-error -G -X "$_mmr_method" -D "$_mmr_headers" \
            -H "@$_mmr_auth_headers" \
            "$@" \
            "$_mmr_url") || {
            _mmr_curl_status=$?
            rm -f "$_mmr_headers" "$_mmr_auth_headers"
            echo "Ошибка curl при запросе $_mmr_method $_mmr_url" >&2
            if [ "$_mmr_method" != "GET" ]; then
                echo "Результат может быть неизвестен. Перед повтором перечитайте объект." >&2
            fi
            return "$_mmr_curl_status"
        }
    fi

    METRIKA_HTTP_STATUS=$(awk '/^HTTP\// {status=$2} END {print status}' "$_mmr_headers")
    rm -f "$_mmr_headers" "$_mmr_auth_headers"

    if [ -z "$METRIKA_HTTP_STATUS" ]; then
        echo "Ошибка: в ответе API нет состояния HTTP." >&2
        return 1
    fi

    if [ "$METRIKA_HTTP_STATUS" = "404" ] && [ "${METRIKA_ALLOW_NOT_FOUND:-}" = "1" ]; then
        printf '%s' "$_mmr_body"
        return 4
    fi

    if [ -n "$METRIKA_HTTP_STATUS" ] && [ "$METRIKA_HTTP_STATUS" -ge 400 ] 2>/dev/null; then
        if [ "${METRIKA_CAPTURE_HTTP_ERROR:-}" = "1" ]; then
            printf '%s' "$_mmr_body"
            return 1
        fi
        echo "Ошибка HTTP $METRIKA_HTTP_STATUS: $_mmr_method $_mmr_url" >&2
        printf '%s\n' "$_mmr_body" >&2
        if [ "$METRIKA_HTTP_STATUS" = "403" ]; then
            case "$_mmr_body" in
                *counter_in_connect*)
                    echo "Счётчик связан с паспортной организацией: измените доступ в её веб-интерфейсе." >&2
                    ;;
            esac
        fi
        if [ "$_mmr_method" != "GET" ]; then
            echo "Запрос не повторялся автоматически. Перед повтором перечитайте объект." >&2
        fi
        return 1
    fi

    printf '%s' "$_mmr_body"
}

# --------------- Filter/param builders ---------------

# build_filters <base_filter> [device] [source] [attribution]
# Combines filters with AND
build_filters() {
    _bf_result="${1:-ym:s:isRobot=='No'}"
    _bf_device="$2"
    _bf_source="$3"
    _bf_attr="${4:-lastsign}"

    if [ -n "$_bf_device" ] && [ "$_bf_device" != "all" ]; then
        case "$_bf_device" in
            desktop) _bf_result="${_bf_result} AND ym:s:deviceCategory=='desktop'" ;;
            mobile)  _bf_result="${_bf_result} AND ym:s:deviceCategory=='mobile'" ;;
            tablet)  _bf_result="${_bf_result} AND ym:s:deviceCategory=='tablet'" ;;
        esac
    fi

    if [ -n "$_bf_source" ] && [ "$_bf_source" != "all" ]; then
        case "$_bf_source" in
            organic)  _bf_result="${_bf_result} AND ym:s:${_bf_attr}TrafficSource=='organic'" ;;
            ad)       _bf_result="${_bf_result} AND ym:s:${_bf_attr}TrafficSource=='ad'" ;;
            referral) _bf_result="${_bf_result} AND ym:s:${_bf_attr}TrafficSource=='referral'" ;;
            direct)   _bf_result="${_bf_result} AND ym:s:${_bf_attr}TrafficSource=='direct'" ;;
            social)   _bf_result="${_bf_result} AND ym:s:${_bf_attr}TrafficSource=='social'" ;;
        esac
    fi

    echo "$_bf_result"
}

# --------------- Output helpers ---------------

# print_csv_head <file> [n_lines]
# Prints first N lines of CSV (default 30) with line numbers
print_csv_head() {
    _pch_file="$1"
    _pch_n="${2:-30}"
    if [ -f "$_pch_file" ]; then
        head -n "$_pch_n" "$_pch_file"
        _pch_total=$(wc -l < "$_pch_file" | tr -d ' ')
        if [ "$_pch_total" -gt "$_pch_n" ]; then
            echo "... ($(( _pch_total - _pch_n )) more rows, full data in: $_pch_file)"
        fi
    fi
}

# --------------- JSON minimal helpers (management API only) ---------------

# json_escape <string> — escapes a single-line value for a JSON string.
json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g; s//\\r/g'
}

# json_extract_field <json_string> <field_name>
# Extracts value of a simple key:value pair (not nested)
json_extract_field() {
    echo "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//'
}

# json_extract_number <json_string> <field_name>
json_extract_number() {
    echo "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*[0-9]*" | head -1 | sed 's/.*:[[:space:]]*//'
}

# require_positive_integer <value> <label>
require_positive_integer() {
    case "$1" in
        ''|*[!0-9]*|0)
            echo "Ошибка: $2 должен быть положительным целым числом." >&2
            exit 1
            ;;
    esac
}

# require_single_line <value> <label>
require_single_line() {
    if [ -z "$1" ]; then
        echo "Ошибка: $2 не должен быть пустым." >&2
        exit 1
    fi
    _msl_newline='
'
    _msl_return=$(printf '\r')
    case "$1" in
        *"$_msl_newline"*|*"$_msl_return"*)
            echo "Ошибка: $2 должен состоять из одной строки без управляющих символов." >&2
            exit 1
            ;;
    esac
    if printf '%s' "$1" | LC_ALL=C grep -q '[[:cntrl:]]'; then
        echo "Ошибка: $2 содержит управляющий символ." >&2
        exit 1
    fi
}

# require_max_characters <value> <max> <label>
require_max_characters() {
    _rmc_size=$(printf '%s' "$1" | management_json text-length -)
    if [ "$_rmc_size" -gt "$2" ]; then
        echo "Ошибка: $3 длиннее $2 символов." >&2
        exit 1
    fi
}

# load_counter_access <counter_id>
# Sets METRIKA_COUNTER_PERMISSION and METRIKA_COUNTER_OWNER from fresh data.
load_counter_access() {
    _mca_counter="$1"
    _mca_response=$(metrika_mgmt_request GET "/management/v1/counter/$_mca_counter" "")
    METRIKA_COUNTER_PERMISSION=$(json_extract_field "$_mca_response" "permission" || true)
    METRIKA_COUNTER_OWNER=$(json_extract_field "$_mca_response" "owner_login" || true)
    if [ -z "$METRIKA_COUNTER_PERMISSION" ]; then
        echo "Ошибка: в ответе API нет роли пользователя на счётчике." >&2
        return 1
    fi
}

# require_counter_permission <counter_id> <space-separated permissions> <action>
require_counter_permission() {
    _mcp_counter="$1"
    _mcp_allowed="$2"
    _mcp_action="$3"
    load_counter_access "$_mcp_counter"
    case " $_mcp_allowed " in
        *" $METRIKA_COUNTER_PERMISSION "*) return 0 ;;
        *)
            echo "Ошибка: роль '$METRIKA_COUNTER_PERMISSION' не позволяет $_mcp_action." >&2
            echo "Нужна одна из ролей: $_mcp_allowed." >&2
            return 1
            ;;
    esac
}

# print_tsv_head <file> [n_lines]
print_tsv_head() {
    _pth_file="$1"
    _pth_n="${2:-30}"
    head -n "$_pth_n" "$_pth_file"
    _pth_total=$(wc -l < "$_pth_file" | tr -d ' ')
    if [ "$_pth_total" -gt "$_pth_n" ]; then
        echo "... и ещё $(( _pth_total - _pth_n )) строк"
    fi
}

# --------------- Date helpers ---------------

# date_is_today <YYYY-MM-DD> — returns 0 if date equals today
date_is_today() {
    [ "$1" = "$(date +%Y-%m-%d)" ]
}

# --------------- Common param parsing ---------------

# parse_common_params "$@"
# Sets variables: COUNTER, DATE1, DATE2, GROUP, DEVICE, SOURCE, ATTRIBUTION, FILTERS, LIMIT, CSV_OUT, NO_CACHE
parse_common_params() {
    COUNTER=""
    DATE1=""
    DATE2=""
    GROUP=""
    DEVICE=""
    SOURCE=""
    ATTRIBUTION="lastsign"
    FILTERS=""
    LIMIT=""
    CSV_OUT=""
    NO_CACHE=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --counter)     COUNTER="$2"; shift 2 ;;
            --date1)       DATE1="$2"; shift 2 ;;
            --date2)       DATE2="$2"; shift 2 ;;
            --group)       GROUP="$2"; shift 2 ;;
            --device)      DEVICE="$2"; shift 2 ;;
            --source)      SOURCE="$2"; shift 2 ;;
            --attribution) ATTRIBUTION="$2"; shift 2 ;;
            --filters)     FILTERS="$2"; shift 2 ;;
            --limit)       LIMIT="$2"; shift 2 ;;
            --csv)         CSV_OUT="$2"; shift 2 ;;
            --no-cache)    NO_CACHE="1"; shift ;;
            *)             shift ;;
        esac
    done

    # Default date2 to today
    if [ -z "$DATE2" ]; then
        DATE2=$(date +%Y-%m-%d)
    fi

    # Build combined filters
    FILTERS=$(build_filters "${FILTERS:-ym:s:isRobot=='No'}" "$DEVICE" "$SOURCE" "$ATTRIBUTION")
}

# require_counter — exits if COUNTER not set
require_counter() {
    if [ -z "$COUNTER" ]; then
        echo "Error: --counter <ID> is required." >&2
        exit 1
    fi
}

# require_dates — exits if DATE1 not set
require_dates() {
    if [ -z "$DATE1" ]; then
        echo "Error: --date1 YYYY-MM-DD is required." >&2
        exit 1
    fi
}
