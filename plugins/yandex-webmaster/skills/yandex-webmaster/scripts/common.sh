#!/bin/sh
# Common functions for Yandex Webmaster API skill
# POSIX sh compatible — no bashisms

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/.env"
CACHE_DIR="$SCRIPT_DIR/../cache"

WEBMASTER_API="https://api.webmaster.yandex.net"

WM_TMPDIR="${TMPDIR:-/tmp}"
mkdir -p "$WM_TMPDIR"

# --------------- Config ---------------

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi

    if [ -z "$YANDEX_WEBMASTER_TOKEN" ]; then
        echo "Error: YANDEX_WEBMASTER_TOKEN not found." >&2
        echo "Set in config/.env or environment. See config/README.md." >&2
        exit 1
    fi
}

# --------------- User ID ---------------

# ensure_user_id — fetches and caches user_id
ensure_user_id() {
    _eui_cache="$CACHE_DIR/user_id.txt"
    if [ -f "$_eui_cache" ] && [ -s "$_eui_cache" ]; then
        USER_ID=$(cat "$_eui_cache")
        return 0
    fi

    _eui_tmp="${WM_TMPDIR}/wm_user_$$.json"
    trap 'rm -f "$_eui_tmp"' EXIT

    webmaster_raw_get "/v4/user" > "$_eui_tmp"
    USER_ID=$(json_extract_number "$(cat "$_eui_tmp")" "user_id")

    if [ -z "$USER_ID" ]; then
        echo "Error: could not get user_id from API." >&2
        cat "$_eui_tmp" >&2
        rm -f "$_eui_tmp"
        exit 1
    fi

    mkdir -p "$CACHE_DIR"
    printf '%s' "$USER_ID" > "$_eui_cache"
    rm -f "$_eui_tmp"
}

# --------------- Host resolution ---------------

# normalize_host_search <domain_or_url> — IDN ввод в формате ASCII-кеша.
normalize_host_search() {
    if ! printf '%s' "$1" | LC_ALL=C grep -q '[^ -~]'; then
        printf '%s' "$1"
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: для поиска кириллического домена нужен python3; можно указать домен в xn--… или --host-id." >&2
        return 1
    fi
    python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit

try:
    value = sys.argv[1].strip()
    prefix = "" if "://" in value or value.startswith("//") else "//"
    url = urlsplit(prefix + value)
    if not url.hostname or url.username is not None:
        raise ValueError("ожидается домен или URL сайта без учётных данных")
    if url.hostname.isascii():
        print(value)
    else:
        host = url.hostname.encode("idna").decode("ascii")
        netloc = host if url.port is None else f"{host}:{url.port}"
        print(url._replace(netloc=netloc).geturl()[len(prefix):])
except (UnicodeError, ValueError) as error:
    print(f"Error: не удалось преобразовать домен в IDNA: {error}", file=sys.stderr)
    sys.exit(1)
PY
}

# resolve_host — sets HOST_ID from --host (domain search) or --host-id (direct)
# Requires USER_ID to be set (call ensure_user_id first)
resolve_host() {
    if [ -n "$HOST_ID" ]; then
        return 0
    fi

    if [ -n "$HOST_SEARCH" ]; then
        _rh_search=$(normalize_host_search "$HOST_SEARCH") || return 1
        _rh_tsv="$CACHE_DIR/hosts.tsv"
        if [ ! -f "$_rh_tsv" ] || [ ! -s "$_rh_tsv" ]; then
            _refresh_hosts_cache
        fi

        _rh_match=$(grep -i -- "$_rh_search" "$_rh_tsv" | head -1)
        if [ -z "$_rh_match" ]; then
            echo "Error: no host matching '$HOST_SEARCH' in cache." >&2
            echo "Run: bash scripts/hosts.sh --no-cache" >&2
            exit 1
        fi
        HOST_ID=$(printf '%s' "$_rh_match" | cut -f1)
    fi

    if [ -z "$HOST_ID" ]; then
        echo "Error: --host <domain> or --host-id <id> is required." >&2
        exit 1
    fi
}

_refresh_hosts_cache() {
    _rhc_tmp="${WM_TMPDIR}/wm_hosts_$$.json"
    webmaster_raw_get "/v4/user/${USER_ID}/hosts" > "$_rhc_tmp"

    mkdir -p "$CACHE_DIR"
    cp "$_rhc_tmp" "$CACHE_DIR/hosts.json"

    # Generate TSV: host_id<TAB>url<TAB>verified<TAB>main_mirror
    {
        sed 's/},{/}\n{/g' "$_rhc_tmp" | while IFS= read -r _line || [ -n "$_line" ]; do
            _hid=$(json_extract_field_raw "$_line" "host_id")
            _url=$(json_extract_field_raw "$_line" "ascii_host_url")
            _ver=$(json_extract_field_raw "$_line" "verified")
            [ -z "$_hid" ] && continue
            printf '%s\t%s\t%s\n' "$_hid" "$_url" "$_ver"
        done
    } > "$CACHE_DIR/hosts.tsv"

    rm -f "$_rhc_tmp"
}

# --------------- API helpers ---------------

# webmaster_raw_get <full_path> [extra_curl_args...]
# Low-level GET with OAuth, 429 retry, error handling
webmaster_raw_get() {
    _wrg_path="$1"
    shift
    _wrg_url="${WEBMASTER_API}${_wrg_path}"
    _wrg_headers="${WM_TMPDIR}/wm_headers_$$.txt"

    _wrg_body=$(curl -s -G -D "$_wrg_headers" \
        -H "Authorization: OAuth $YANDEX_WEBMASTER_TOKEN" \
        "$@" \
        "$_wrg_url") || {
        rm -f "$_wrg_headers"
        echo "Error: curl failed for $_wrg_url" >&2
        return 1
    }

    _wrg_status=$(head -1 "$_wrg_headers" | grep -o '[0-9][0-9][0-9]' | head -1)

    # 429 retry
    if [ "$_wrg_status" = "429" ]; then
        _wrg_retry=$(grep -i 'Retry-After' "$_wrg_headers" | sed 's/[^0-9]//g' | head -1)
        rm -f "$_wrg_headers"
        if [ -z "${_WM_RETRY_DONE:-}" ] && [ -n "$_wrg_retry" ] && [ "$_wrg_retry" -le 60 ] 2>/dev/null; then
            _wrg_jitter=$(awk 'BEGIN{srand(); printf "%d", rand()*3}')
            _wrg_wait=$(( _wrg_retry + _wrg_jitter ))
            echo "Rate limited. Waiting ${_wrg_wait}s..." >&2
            sleep "$_wrg_wait"
            _WM_RETRY_DONE=1 webmaster_raw_get "$_wrg_path" "$@"
            return $?
        else
            echo "Error: Rate limit exceeded (429)." >&2
            return 1
        fi
    fi

    # HTTP errors
    if [ -n "$_wrg_status" ] && [ "$_wrg_status" -ge 400 ] 2>/dev/null; then
        rm -f "$_wrg_headers"
        _wrg_err_code=$(json_extract_field_raw "$_wrg_body" "error_code")
        _wrg_err_msg=$(json_extract_field_raw "$_wrg_body" "error_message")
        if [ -n "$_wrg_err_code" ]; then
            echo "Error: HTTP $_wrg_status — $_wrg_err_code: $_wrg_err_msg" >&2
        else
            echo "Error: HTTP $_wrg_status from $_wrg_url" >&2
            echo "$_wrg_body" >&2
        fi
        return 1
    fi

    rm -f "$_wrg_headers"
    printf '%s' "$_wrg_body"
}

# webmaster_raw_post <full_path> <json_body>
# Low-level POST with OAuth
webmaster_raw_post() {
    _wrp_path="$1"
    _wrp_body_data="$2"
    _wrp_url="${WEBMASTER_API}${_wrp_path}"
    _wrp_headers="${WM_TMPDIR}/wm_headers_$$.txt"

    _wrp_resp=$(curl -s -X POST -D "$_wrp_headers" \
        -H "Authorization: OAuth $YANDEX_WEBMASTER_TOKEN" \
        -H "Content-Type: application/json; charset=UTF-8" \
        -d "$_wrp_body_data" \
        "$_wrp_url") || {
        rm -f "$_wrp_headers"
        echo "Error: curl failed for $_wrp_url" >&2
        return 1
    }

    _wrp_status=$(head -1 "$_wrp_headers" | grep -o '[0-9][0-9][0-9]' | head -1)

    if [ "$_wrp_status" = "429" ]; then
        _wrp_retry=$(grep -i 'Retry-After' "$_wrp_headers" | sed 's/[^0-9]//g' | head -1)
        rm -f "$_wrp_headers"
        if [ -z "${_WM_RETRY_DONE:-}" ] && [ -n "$_wrp_retry" ] && [ "$_wrp_retry" -le 60 ] 2>/dev/null; then
            _wrp_jitter=$(awk 'BEGIN{srand(); printf "%d", rand()*3}')
            _wrp_wait=$(( _wrp_retry + _wrp_jitter ))
            echo "Rate limited. Waiting ${_wrp_wait}s..." >&2
            sleep "$_wrp_wait"
            _WM_RETRY_DONE=1 webmaster_raw_post "$_wrp_path" "$_wrp_body_data"
            return $?
        else
            echo "Error: Rate limit exceeded (429)." >&2
            return 1
        fi
    fi

    if [ -n "$_wrp_status" ] && [ "$_wrp_status" -ge 400 ] 2>/dev/null; then
        rm -f "$_wrp_headers"
        _wrp_err_code=$(json_extract_field_raw "$_wrp_resp" "error_code")
        _wrp_err_msg=$(json_extract_field_raw "$_wrp_resp" "error_message")
        if [ -n "$_wrp_err_code" ]; then
            echo "Error: HTTP $_wrp_status — $_wrp_err_code: $_wrp_err_msg" >&2
        else
            echo "Error: HTTP $_wrp_status from $_wrp_url" >&2
            echo "$_wrp_resp" >&2
        fi
        return 1
    fi

    rm -f "$_wrp_headers"
    printf '%s' "$_wrp_resp"
}

# webmaster_get <host_subpath> [extra_curl_args...]
# GET /v4/user/{USER_ID}/hosts/{HOST_ID}/<subpath>
webmaster_get() {
    _wg_sub="$1"
    shift
    webmaster_raw_get "/v4/user/${USER_ID}/hosts/${HOST_ID}${_wg_sub}" "$@"
}

# webmaster_post <host_subpath> <json_body>
# POST /v4/user/{USER_ID}/hosts/{HOST_ID}/<subpath>
webmaster_post() {
    _wp_sub="$1"
    _wp_body="$2"
    webmaster_raw_post "/v4/user/${USER_ID}/hosts/${HOST_ID}${_wp_sub}" "$_wp_body"
}

# webmaster_user_get [extra_curl_args...]
# GET /v4/user/{USER_ID}/hosts (user-level, no specific host)
webmaster_user_get() {
    _wug_sub="$1"
    shift 2>/dev/null || true
    webmaster_raw_get "/v4/user/${USER_ID}${_wug_sub}" "$@"
}

# webmaster_user_post <subpath> <json_body>
webmaster_user_post() {
    _wup_sub="$1"
    _wup_body="$2"
    webmaster_raw_post "/v4/user/${USER_ID}${_wup_sub}" "$_wup_body"
}

# --------------- Cache helpers ---------------

cache_key() {
    printf '%s' "$1" | cksum | awk '{print $1}'
}

cache_get() {
    if [ -f "$1" ] && [ -s "$1" ]; then
        cat "$1"
        return 0
    fi
    return 1
}

cache_put() {
    mkdir -p "$(dirname "$1")"
    cat > "$1"
}

# cache_get_ttl <file_path> <max_age_minutes>
# Returns 0 if file exists and is newer than max_age_minutes.
# Returns 1 (miss) if file is missing, empty, or older than TTL.
cache_get_ttl() {
    _cgt_file="$1"
    _cgt_ttl="${2:-1440}"
    if [ -f "$_cgt_file" ] && [ -s "$_cgt_file" ]; then
        # find returns the file if it's OLDER than ttl → stale
        _cgt_stale=$(find "$_cgt_file" -mmin +"$_cgt_ttl" 2>/dev/null)
        if [ -z "$_cgt_stale" ]; then
            return 0
        fi
        # Stale — delete and miss
        rm -f "$_cgt_file"
    fi
    return 1
}

cache_host_dir() {
    _chd_dir="$CACHE_DIR/host_$(printf '%s' "$HOST_ID" | sed 's/[^a-zA-Z0-9._-]/_/g')"
    mkdir -p "$_chd_dir"
    echo "$_chd_dir"
}

# --------------- JSON escape ---------------

# json_escape <string> — escapes special characters for JSON string values
json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g' | tr -d '\n\r'
}

# --------------- JSON helpers (no jq) ---------------

json_extract_field_raw() {
    printf '%s' "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//'
}

json_extract_number() {
    printf '%s' "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*[0-9.e+-]*" | head -1 | sed 's/.*:[[:space:]]*//'
}

json_extract_bool() {
    printf '%s' "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*[a-z]*" | head -1 | sed 's/.*:[[:space:]]*//'
}

# json_extract_array_strings <json> <field> — extracts string array values, one per line
json_extract_array_strings() {
    printf '%s' "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*\[[^]]*\]" | head -1 | grep -o '"[^"]*"' | sed 's/"//g'
}

# --------------- Output helpers ---------------

print_tsv_head() {
    _pth_file="$1"
    _pth_n="${2:-30}"
    if [ -f "$_pth_file" ]; then
        head -n "$_pth_n" "$_pth_file"
        _pth_total=$(wc -l < "$_pth_file" | tr -d ' ')
        if [ "$_pth_total" -gt "$_pth_n" ]; then
            echo "... ($(( _pth_total - _pth_n )) more rows, full data in: $_pth_file)"
        fi
    fi
}

# --------------- Date helpers ---------------

# date_subtract_days <YYYY-MM-DD> <days>
# Returns date N days before the given date. POSIX-compatible.
date_subtract_days() {
    _dsd_base="$1"
    _dsd_days="$2"
    # Try macOS date first, then GNU date
    if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
        date -j -f "%Y-%m-%d" "$_dsd_base" -v-"${_dsd_days}d" +%Y-%m-%d 2>/dev/null && return 0
    fi
    if date -d "$_dsd_base - $_dsd_days days" +%Y-%m-%d 2>/dev/null; then
        return 0
    fi
    # Pure shell fallback: subtract days via simple month/day arithmetic
    # Good enough for 90-day ranges
    _dsd_y=$(echo "$_dsd_base" | cut -d- -f1)
    _dsd_m=$(echo "$_dsd_base" | cut -d- -f2 | sed 's/^0//')
    _dsd_d=$(echo "$_dsd_base" | cut -d- -f3 | sed 's/^0//')
    _dsd_remain="$_dsd_days"
    while [ "$_dsd_remain" -gt 0 ]; do
        if [ "$_dsd_d" -gt "$_dsd_remain" ]; then
            _dsd_d=$(( _dsd_d - _dsd_remain ))
            _dsd_remain=0
        else
            _dsd_remain=$(( _dsd_remain - _dsd_d ))
            _dsd_m=$(( _dsd_m - 1 ))
            if [ "$_dsd_m" -lt 1 ]; then
                _dsd_m=12
                _dsd_y=$(( _dsd_y - 1 ))
            fi
            # Days in the new month
            case "$_dsd_m" in
                1|3|5|7|8|10|12) _dsd_d=31 ;;
                4|6|9|11) _dsd_d=30 ;;
                2) if [ $(( _dsd_y % 4 )) -eq 0 ] && { [ $(( _dsd_y % 100 )) -ne 0 ] || [ $(( _dsd_y % 400 )) -eq 0 ]; }; then _dsd_d=29; else _dsd_d=28; fi ;;
            esac
        fi
    done
    printf '%04d-%02d-%02d\n' "$_dsd_y" "$_dsd_m" "$_dsd_d"
}

# default_date_from [reference_date]
# Returns date 90 days before reference_date (default: today)
default_date_from() {
    _ddf_ref="${1:-$(date +%Y-%m-%d)}"
    date_subtract_days "$_ddf_ref" 90
}

# apply_default_dates — sets DATE_FROM if empty (for history scripts)
# If --date-to is set but --date-from is not: DATE_FROM = DATE_TO - 90 days
# If neither is set: DATE_FROM = today - 90 days
apply_default_dates() {
    if [ -z "$DATE_FROM" ]; then
        if [ -n "$DATE_TO" ]; then
            DATE_FROM=$(default_date_from "$DATE_TO")
        else
            DATE_FROM=$(default_date_from)
        fi
    fi
}

# --------------- Common param parsing ---------------

# parse_host_params "$@"
# Sets: HOST_ID, HOST_SEARCH, ACTION, DATE_FROM, DATE_TO, LIMIT, OFFSET, NO_CACHE
parse_host_params() {
    HOST_ID=""
    HOST_SEARCH=""
    ACTION=""
    DATE_FROM=""
    DATE_TO=""
    LIMIT=""
    OFFSET=""
    NO_CACHE=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --host-id)    HOST_ID="$2"; shift 2 ;;
            --host)       HOST_SEARCH="$2"; shift 2 ;;
            --action)     ACTION="$2"; shift 2 ;;
            --date-from)  DATE_FROM="$2"; shift 2 ;;
            --date-to)    DATE_TO="$2"; shift 2 ;;
            --limit)      LIMIT="$2"; shift 2 ;;
            --offset)     OFFSET="$2"; shift 2 ;;
            --no-cache)   NO_CACHE="1"; shift ;;
            *)            shift ;;
        esac
    done
}

# require_host — exits if host not resolved
require_host() {
    if [ -z "$HOST_ID" ]; then
        echo "Error: --host <domain> or --host-id <id> is required." >&2
        exit 1
    fi
}

# add_date_params — builds curl args for date_from/date_to
# Usage: eval "set -- $(add_date_params)"
add_date_params() {
    _adp=""
    if [ -n "$DATE_FROM" ]; then
        _adp="$_adp --data-urlencode \"date_from=${DATE_FROM}T00:00:00.000+0300\""
    fi
    if [ -n "$DATE_TO" ]; then
        _adp="$_adp --data-urlencode \"date_to=${DATE_TO}T00:00:00.000+0300\""
    fi
    echo "$_adp"
}
