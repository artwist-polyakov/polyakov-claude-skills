#!/bin/sh
# Yandex Direct costs report: clicks, ad cost, visits by campaign/date
# Usage: direct_costs.sh --counter <ID> --date1 YYYY-MM-DD [--date2 ...]
#        [--direct-client-logins "login1,login2"] [--limit N] [--csv path] [--no-cache]
#
# Requires direct_client_logins — auto-fetched from cache or via direct_clients.sh.
# Override with --direct-client-logins if cache is empty or endpoint deprecated.
# Uses ym:ad:* metrics (ad scope, not visit scope).
# No --group support: ym:ad:date dimension gives daily granularity,
# aggregate to week/month in CSV/Excel.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Parse --direct-client-logins before parse_common_params (which ignores unknown flags)
DIRECT_LOGINS_OVERRIDE=""
_prev_dc=""
for _arg in "$@"; do
    case "$_prev_dc" in
        --direct-client-logins) DIRECT_LOGINS_OVERRIDE="$_arg"; _prev_dc=""; continue ;;
    esac
    _prev_dc="$_arg"
done

parse_common_params "$@"
require_counter
require_dates

# --- Get direct_client_logins ---
COUNTER_DIR=$(cache_dir_for_counter "$COUNTER")

if [ -n "$DIRECT_LOGINS_OVERRIDE" ]; then
    DIRECT_LOGINS="$DIRECT_LOGINS_OVERRIDE"
else
    _dc_cache="$COUNTER_DIR/direct_clients.json"

    if [ ! -f "$_dc_cache" ] || [ ! -s "$_dc_cache" ]; then
        echo "Direct clients cache not found. Fetching..." >&2
        sh "$SCRIPT_DIR/direct_clients.sh" --counter "$COUNTER" >/dev/null || true
    fi

    if [ ! -f "$_dc_cache" ] || [ ! -s "$_dc_cache" ]; then
        echo "Error: could not get Direct client logins for counter $COUNTER." >&2
        echo "The /management/v1/clients endpoint may be unavailable." >&2
        echo "Specify logins manually: --direct-client-logins \"login1,login2\"" >&2
        exit 1
    fi

    # Extract comma-separated logins
    _logins_tmp=$(grep -o '"chief_login"[[:space:]]*:[[:space:]]*"[^"]*"' "$_dc_cache" | sed 's/.*"chief_login"[[:space:]]*:[[:space:]]*"//;s/"$//')
    echo "$_logins_tmp" | while IFS= read -r _login || [ -n "$_login" ]; do
        if [ -n "$_login" ]; then
            printf '%s\n' "$_login"
        fi
    done > "${METRIKA_TMPDIR}/metrika_logins_$$.txt"

    # Build comma-separated string
    DIRECT_LOGINS=""
    _first=1
    while IFS= read -r _login || [ -n "$_login" ]; do
        if [ -n "$_login" ]; then
            if [ "$_first" = "1" ]; then
                DIRECT_LOGINS="$_login"
                _first=0
            else
                DIRECT_LOGINS="${DIRECT_LOGINS},${_login}"
            fi
        fi
    done < "${METRIKA_TMPDIR}/metrika_logins_$$.txt"
    rm -f "${METRIKA_TMPDIR}/metrika_logins_$$.txt"

    if [ -z "$DIRECT_LOGINS" ]; then
        echo "Error: no Direct client logins found for counter $COUNTER." >&2
        echo "This counter may not have linked Yandex Direct accounts." >&2
        echo "Specify logins manually: --direct-client-logins \"login\"" >&2
        exit 1
    fi

    # Check accessibility warning
    _all_accessible=$(grep -o '"all_clients_accessible_to_user"[[:space:]]*:[[:space:]]*[a-z]*' "$_dc_cache" | head -1 | sed 's/.*:[[:space:]]*//')
    if [ "$_all_accessible" = "false" ]; then
        echo "WARNING: Not all Direct clients are accessible. Cost data may be incomplete." >&2
    fi
fi

# --- Build report ---
METRICS="ym:ad:clicks,ym:ad:RUBConvertedAdCost,ym:ad:visits"
DIMENSIONS="ym:ad:date,ym:ad:directOrder"

# Cache key
_params_str="direct_costs_${COUNTER}_${DATE1}_${DATE2}_${DIRECT_LOGINS}_${LIMIT}"
_hash=$(cache_key "$_params_str")
CACHE_FILE="$COUNTER_DIR/reports/direct_costs_${DATE1}_${DATE2}_${_hash}.csv"

# Skip cache if date2 is today
if date_is_today "$DATE2"; then
    NO_CACHE="1"
fi

# Check cache
if [ -z "$NO_CACHE" ] && [ -f "$CACHE_FILE" ] && [ -s "$CACHE_FILE" ]; then
    echo "Direct costs for counter $COUNTER ($DATE1 — $DATE2):"
    print_csv_head "$CACHE_FILE" 30
    [ -n "$CSV_OUT" ] && cp "$CACHE_FILE" "$CSV_OUT" && echo "Copied to: $CSV_OUT"
    exit 0
fi

# Always use /stat/v1/data.csv (no bytime — ym:ad:date gives daily granularity)
API_PATH="/stat/v1/data.csv"

echo "Fetching Direct costs for counter $COUNTER ($DATE1 — $DATE2)..." >&2

TMPFILE="${METRIKA_TMPDIR}/metrika_direct_costs_$$.csv"
trap 'rm -f "$TMPFILE"' EXIT

metrika_get_csv "$API_PATH" "$TMPFILE" \
    --data-urlencode "ids=$COUNTER" \
    --data-urlencode "date1=$DATE1" \
    --data-urlencode "date2=$DATE2" \
    --data-urlencode "metrics=$METRICS" \
    --data-urlencode "dimensions=$DIMENSIONS" \
    --data-urlencode "direct_client_logins=$DIRECT_LOGINS" \
    --data-urlencode "accuracy=1" \
    ${LIMIT:+--data-urlencode "limit=$LIMIT"}

cp "$TMPFILE" "$CACHE_FILE"

echo "Direct costs for counter $COUNTER ($DATE1 — $DATE2):"
print_csv_head "$CACHE_FILE" 30

if [ -n "$CSV_OUT" ]; then
    cp "$CACHE_FILE" "$CSV_OUT"
    echo "Exported to: $CSV_OUT"
fi
