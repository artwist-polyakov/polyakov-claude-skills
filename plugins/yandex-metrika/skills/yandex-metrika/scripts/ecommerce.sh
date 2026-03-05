#!/bin/sh
# Ecommerce report: purchases, revenue, avg check
# Usage: ecommerce.sh --counter <ID> --date1 YYYY-MM-DD [--date2 ...] [--group day|week|month]
#        [--device ...] [--source ...] [--attribution lastsign|last|first]
#        [--currency RUB|USD|EUR] [--limit N] [--csv path] [--no-cache]
#
# Currency: uses documented ecommerce<CUR>ConvertedRevenue* metrics.
# Default currency is read from counter info cache (currency_code field).
# Override with --currency RUB|USD|EUR.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Parse --currency before parse_common_params (which ignores unknown flags)
CURRENCY=""
_prev_ec=""
for _arg in "$@"; do
    case "$_prev_ec" in
        --currency) CURRENCY="$_arg"; _prev_ec=""; continue ;;
    esac
    _prev_ec="$_arg"
done

parse_common_params "$@"
require_counter
require_dates

ATTRIBUTION="${ATTRIBUTION:-lastsign}"

# Auto-detect currency from counter info cache if not specified
if [ -z "$CURRENCY" ]; then
    COUNTER_DIR=$(cache_dir_for_counter "$COUNTER")
    _info_cache="$COUNTER_DIR/info.json"
    if [ -f "$_info_cache" ]; then
        CURRENCY=$(grep -o '"currency_code":"[^"]*"' "$_info_cache" | head -1 | sed 's/.*"currency_code":"//;s/"//' || true)
    fi
fi
# Fallback to RUB if still empty
CURRENCY="${CURRENCY:-RUB}"

# Build metrics using documented ecommerce<CUR>ConvertedRevenue* names
_rev="ym:s:ecommerce${CURRENCY}ConvertedRevenue"
_rev_per_purchase="ym:s:ecommerce${CURRENCY}ConvertedRevenuePerPurchase"
_rev_per_visit="ym:s:ecommerce${CURRENCY}ConvertedRevenuePerVisit"
METRICS="ym:s:ecommercePurchases,${_rev},${_rev_per_purchase},${_rev_per_visit},ym:s:visits,ym:s:users"
DIMENSIONS="ym:s:${ATTRIBUTION}TrafficSource"

# Cache key
_params_str="ecommerce_${COUNTER}_${DATE1}_${DATE2}_${GROUP}_${DEVICE}_${SOURCE}_${ATTRIBUTION}_${CURRENCY}_${LIMIT}"
_hash=$(cache_key "$_params_str")
COUNTER_DIR=$(cache_dir_for_counter "$COUNTER")
CACHE_FILE="$COUNTER_DIR/reports/ecommerce_${DATE1}_${DATE2}_${_hash}.csv"

# Skip cache if date2 is today (data still accumulating)
if date_is_today "$DATE2"; then
    NO_CACHE="1"
fi

# Check cache
if [ -z "$NO_CACHE" ] && [ -f "$CACHE_FILE" ] && [ -s "$CACHE_FILE" ]; then
    echo "Ecommerce report for counter $COUNTER ($DATE1 — $DATE2):"
    print_csv_head "$CACHE_FILE" 30
    [ -n "$CSV_OUT" ] && cp "$CACHE_FILE" "$CSV_OUT" && echo "Copied to: $CSV_OUT"
    exit 0
fi

# Build API path
if [ -n "$GROUP" ]; then
    API_PATH="/stat/v1/data/bytime.csv"
else
    API_PATH="/stat/v1/data.csv"
fi

echo "Fetching ecommerce report for counter $COUNTER ($DATE1 — $DATE2)..." >&2

TMPFILE="${METRIKA_TMPDIR}/metrika_ecommerce_$$.csv"
trap 'rm -f "$TMPFILE"' EXIT

metrika_get_csv "$API_PATH" "$TMPFILE" \
    --data-urlencode "ids=$COUNTER" \
    --data-urlencode "date1=$DATE1" \
    --data-urlencode "date2=$DATE2" \
    --data-urlencode "metrics=$METRICS" \
    --data-urlencode "dimensions=$DIMENSIONS" \
    --data-urlencode "accuracy=1" \
    --data-urlencode "filters=$FILTERS" \
    ${GROUP:+--data-urlencode "group=$GROUP"} \
    ${LIMIT:+--data-urlencode "limit=$LIMIT"}

cp "$TMPFILE" "$CACHE_FILE"

echo "Ecommerce report for counter $COUNTER ($DATE1 — $DATE2):"
print_csv_head "$CACHE_FILE" 30

if [ -n "$CSV_OUT" ]; then
    cp "$CACHE_FILE" "$CSV_OUT"
    echo "Exported to: $CSV_OUT"
fi
