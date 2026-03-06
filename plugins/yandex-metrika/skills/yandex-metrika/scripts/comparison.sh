#!/bin/sh
# Period comparison report via /stat/v1/data/comparison.csv
# Usage: comparison.sh --counter <ID> \
#        --date1a YYYY-MM-DD --date2a YYYY-MM-DD \
#        --date1b YYYY-MM-DD --date2b YYYY-MM-DD \
#        [--dimension <dim>] [--metrics <metrics>]
#        [--device ...] [--source ...] [--attribution lastsign|last|first]
#        [--limit N] [--csv path] [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Custom param parsing (comparison has date1a/date2a/date1b/date2b instead of date1/date2)
COUNTER=""
DATE1A=""
DATE2A=""
DATE1B=""
DATE2B=""
DIMENSION=""
COMP_METRICS=""
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
        --date1a)      DATE1A="$2"; shift 2 ;;
        --date2a)      DATE2A="$2"; shift 2 ;;
        --date1b)      DATE1B="$2"; shift 2 ;;
        --date2b)      DATE2B="$2"; shift 2 ;;
        --dimension)   DIMENSION="$2"; shift 2 ;;
        --metrics)     COMP_METRICS="$2"; shift 2 ;;
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

FILTERS=$(build_filters "${FILTERS:-ym:s:isRobot=='No'}" "$DEVICE" "$SOURCE" "$ATTRIBUTION")

if [ -z "$COUNTER" ]; then
    echo "Error: --counter <ID> is required." >&2
    exit 1
fi
if [ -z "$DATE1A" ] || [ -z "$DATE2A" ] || [ -z "$DATE1B" ] || [ -z "$DATE2B" ]; then
    echo "Error: --date1a, --date2a, --date1b, --date2b are all required." >&2
    echo "Usage: comparison.sh --counter ID --date1a ... --date2a ... --date1b ... --date2b ..." >&2
    exit 1
fi

# Defaults
DIMENSION="${DIMENSION:-ym:s:${ATTRIBUTION}TrafficSource}"
COMP_METRICS="${COMP_METRICS:-ym:s:visits,ym:s:users,ym:s:bounceRate}"

# Cache key
_params_str="comparison_${COUNTER}_${DATE1A}_${DATE2A}_${DATE1B}_${DATE2B}_${DIMENSION}_${COMP_METRICS}_${DEVICE}_${SOURCE}_${ATTRIBUTION}_${LIMIT}"
_hash=$(cache_key "$_params_str")
COUNTER_DIR=$(cache_dir_for_counter "$COUNTER")
CACHE_FILE="$COUNTER_DIR/reports/comparison_${DATE1A}_${DATE2B}_${_hash}.csv"

# Skip cache if any period includes today
if date_is_today "$DATE2A" || date_is_today "$DATE2B"; then
    NO_CACHE="1"
fi

# Check cache
if [ -z "$NO_CACHE" ] && [ -f "$CACHE_FILE" ] && [ -s "$CACHE_FILE" ]; then
    echo "Comparison: ($DATE1A — $DATE2A) vs ($DATE1B — $DATE2B):"
    print_csv_head "$CACHE_FILE" 30
    [ -n "$CSV_OUT" ] && cp "$CACHE_FILE" "$CSV_OUT" && echo "Copied to: $CSV_OUT"
    exit 0
fi

echo "Fetching comparison for counter $COUNTER..." >&2
echo "  Period A: $DATE1A — $DATE2A" >&2
echo "  Period B: $DATE1B — $DATE2B" >&2

TMPFILE="${METRIKA_TMPDIR}/metrika_comparison_$$.csv"
trap 'rm -f "$TMPFILE"' EXIT

metrika_get_csv "/stat/v1/data/comparison.csv" "$TMPFILE" \
    --data-urlencode "ids=$COUNTER" \
    --data-urlencode "date1_a=$DATE1A" \
    --data-urlencode "date2_a=$DATE2A" \
    --data-urlencode "date1_b=$DATE1B" \
    --data-urlencode "date2_b=$DATE2B" \
    --data-urlencode "metrics=$COMP_METRICS" \
    --data-urlencode "dimensions=$DIMENSION" \
    --data-urlencode "accuracy=1" \
    --data-urlencode "filters=$FILTERS" \
    ${LIMIT:+--data-urlencode "limit=$LIMIT"}

cp "$TMPFILE" "$CACHE_FILE"

echo "Comparison: ($DATE1A — $DATE2A) vs ($DATE1B — $DATE2B):"
print_csv_head "$CACHE_FILE" 30

if [ -n "$CSV_OUT" ]; then
    cp "$CACHE_FILE" "$CSV_OUT"
    echo "Exported to: $CSV_OUT"
fi
