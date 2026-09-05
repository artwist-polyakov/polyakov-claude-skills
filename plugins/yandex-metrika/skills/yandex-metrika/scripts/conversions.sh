#!/bin/sh
# Goal conversion report
# Usage: conversions.sh --counter <ID> --date1 YYYY-MM-DD [--date2 ...] [--group day|week|month]
#        [--goals 123,456] [--all-goals] [--device ...] [--source ...] [--attribution ...]
#        [--limit N] [--csv path] [--no-cache]
#
# By default shows only conversion_goals from cache/counter_<id>/config.json.
# Use --all-goals to show all goals, or --goals to specify goal IDs manually.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config
parse_common_params "$@"
require_counter
require_dates

GOALS=""
ALL_GOALS=""

# Re-parse for goals-specific params (parse_common_params already consumed "$@",
# but it passes unknown args through, so we re-scan the original args)
_prev=""
for _arg in "$@"; do
    if [ "$_prev" = "--goals" ]; then
        GOALS="$_arg"
        _prev=""
        continue
    fi
    case "$_arg" in
        --goals)     _prev="--goals" ;;
        --all-goals) ALL_GOALS="1" ;;
        *)           _prev="" ;;
    esac
done

ATTRIBUTION="${ATTRIBUTION:-lastsign}"
COUNTER_DIR=$(cache_dir_for_counter "$COUNTER")
CONFIG_JSON="$COUNTER_DIR/config.json"

# Determine goal IDs
if [ -n "$GOALS" ]; then
    # Manual override
    GOAL_IDS="$GOALS"
elif [ -n "$ALL_GOALS" ]; then
    # All goals from cache
    if [ -f "$COUNTER_DIR/goals.tsv" ]; then
        GOAL_IDS=$(cut -f1 "$COUNTER_DIR/goals.tsv" | tr '\n' ',' | sed 's/,$//')
    else
        echo "Error: No cached goals. Run: goals.sh --counter $COUNTER" >&2
        exit 1
    fi
else
    # Default: conversion goals from config
    if [ -f "$CONFIG_JSON" ] && grep -q "conversion_goals" "$CONFIG_JSON" 2>/dev/null; then
        GOAL_IDS=$(grep -o '"id"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_JSON" | sed 's/.*:[[:space:]]*//' | tr '\n' ',' | sed 's/,$//')
    else
        echo "Error: No conversion goals configured for counter $COUNTER." >&2
        echo "Run goals.sh --counter $COUNTER to see available goals," >&2
        echo "then save conversion goals to $CONFIG_JSON." >&2
        echo "Or use --all-goals or --goals <ids>." >&2
        exit 1
    fi
fi

# Normalize once for all three ways of selecting goals.
_goal_ids=""
_IFS="$IFS"
IFS=","
for _gid in $GOAL_IDS; do
    _gid=$(echo "$_gid" | tr -d ' ')
    [ -z "$_gid" ] && continue
    case "$_gid" in
        *[!0-9]*) echo "Error: Invalid goal ID: $_gid" >&2; exit 1 ;;
    esac
    case ",$_goal_ids," in
        *,"$_gid",*) continue ;;
    esac
    _goal_ids="${_goal_ids:+$_goal_ids,}$_gid"
done
IFS="$_IFS"
GOAL_IDS="$_goal_ids"

if [ -z "$GOAL_IDS" ]; then
    echo "Error: No valid goal IDs found." >&2
    exit 1
fi

DIMENSIONS="ym:s:${ATTRIBUTION}TrafficSource"
FIRST_GOAL="${GOAL_IDS%%,*}"
SORT="-ym:s:goal${FIRST_GOAL}visits,$DIMENSIONS"

if [ -n "$LIMIT" ]; then
    _max_limit=100000
    [ -z "$GROUP" ] || _max_limit=30
    case "$LIMIT" in
        *[!0-9]*) echo "Error: --limit must be an integer." >&2; exit 1 ;;
    esac
    if ! [ "$LIMIT" -ge 1 ] 2>/dev/null || ! [ "$LIMIT" -le "$_max_limit" ] 2>/dev/null; then
        echo "Error: --limit must be between 1 and $_max_limit sources." >&2
        exit 1
    fi
fi

# New version: includes zero-conversion sources and respects all request options.
_params_str="conv_v2_${COUNTER}_${DATE1}_${DATE2}_${GROUP}_${GOAL_IDS}_${FILTERS}_${ATTRIBUTION}_${LIMIT}"
_hash=$(cache_key "$_params_str")
CACHE_FILE="$COUNTER_DIR/reports/conversions_${DATE1}_${DATE2}_${_hash}.csv"

# Skip cache if date2 is today (data still accumulating)
if date_is_today "$DATE2"; then
    NO_CACHE="1"
fi

# Check cache
if [ -z "$NO_CACHE" ] && [ -f "$CACHE_FILE" ] && [ -s "$CACHE_FILE" ]; then
    echo "Conversions for counter $COUNTER ($DATE1 — $DATE2), goals: $GOAL_IDS"
    print_csv_head "$CACHE_FILE" 30
    [ -n "$CSV_OUT" ] && cp "$CACHE_FILE" "$CSV_OUT" && echo "Copied to: $CSV_OUT"
    exit 0
fi

# Build API path
if [ -n "$GROUP" ]; then
    API_PATH="/stat/v1/data/bytime.csv"
    REPORT_MODE="bytime"
else
    API_PATH="/stat/v1/data.csv"
    REPORT_MODE="table"
fi

command -v python3 >/dev/null 2>&1 || {
    echo "Error: Python 3 is required to combine conversion reports (standard library only)." >&2
    exit 1
}

echo "Fetching conversions for counter $COUNTER, goals: $GOAL_IDS..." >&2

CONV_TMPDIR=$(mktemp -d "${METRIKA_TMPDIR}/metrika_conv.XXXXXX")
trap 'rm -rf "$CONV_TMPDIR"' EXIT
trap 'exit 1' HUP INT TERM

fetch_batch() {
    # 18 goal metrics + sessions + optional common sorting metric = at most 20.
    # Sessions keep sources present even when every goal in this batch is zero.
    _metrics="$BATCH_METRICS,ym:s:visits"
    BATCH_HELPERS=1
    case ",$BATCH_IDS," in
        *,"$FIRST_GOAL",*) ;;
        *)
            _metrics="$_metrics,ym:s:goal${FIRST_GOAL}visits"
            BATCH_HELPERS=2
            ;;
    esac

    if [ -n "$GROUP" ]; then
        set -- --data-urlencode "group=$GROUP" \
            --data-urlencode "top_keys=${LIMIT:-30}" \
            --data-urlencode "keys_sort=$SORT"
    else
        set -- --data-urlencode "limit=${LIMIT:-100}" --data-urlencode "sort=$SORT"
    fi

    BATCH_NUMBER=$((BATCH_NUMBER + 1))
    BATCH_FILE="$CONV_TMPDIR/batch_$BATCH_NUMBER.csv"
    metrika_get_csv "$API_PATH" "$BATCH_FILE" \
        --data-urlencode "ids=$COUNTER" \
        --data-urlencode "date1=$DATE1" \
        --data-urlencode "date2=$DATE2" \
        --data-urlencode "metrics=$_metrics" \
        --data-urlencode "dimensions=$DIMENSIONS" \
        --data-urlencode "accuracy=1" \
        --data-urlencode "filters=$FILTERS" \
        "$@"
}

BATCH_NUMBER=0
BATCH_COUNT=0
BATCH_IDS=""
BATCH_METRICS=""
set --
for _gid in $(printf '%s' "$GOAL_IDS" | tr ',' ' '); do
    BATCH_IDS="${BATCH_IDS:+$BATCH_IDS,}$_gid"
    BATCH_METRICS="${BATCH_METRICS:+$BATCH_METRICS,}ym:s:goal${_gid}visits,ym:s:goal${_gid}reaches,ym:s:goal${_gid}conversionRate"
    BATCH_COUNT=$((BATCH_COUNT + 1))
    if [ "$BATCH_COUNT" -eq 6 ]; then
        fetch_batch
        set -- "$@" --batch "$BATCH_FILE" "$BATCH_COUNT" "$BATCH_HELPERS"
        BATCH_COUNT=0
        BATCH_IDS=""
        BATCH_METRICS=""
    fi
done
if [ "$BATCH_COUNT" -gt 0 ]; then
    fetch_batch
    set -- "$@" --batch "$BATCH_FILE" "$BATCH_COUNT" "$BATCH_HELPERS"
fi

python3 "$SCRIPT_DIR/merge_conversions.py" "$REPORT_MODE" "$CONV_TMPDIR/report.csv" "$@"
# Publish the cache only after every request and the merge have succeeded.
_cache_tmp=$(mktemp "$COUNTER_DIR/reports/.conversions.XXXXXX")
cp "$CONV_TMPDIR/report.csv" "$_cache_tmp" && mv -f "$_cache_tmp" "$CACHE_FILE" || {
    rm -f "$_cache_tmp"
    exit 1
}

echo "Conversions for counter $COUNTER ($DATE1 — $DATE2), goals: $GOAL_IDS"
print_csv_head "$CACHE_FILE" 30

if [ -n "$CSV_OUT" ]; then
    cp "$CACHE_FILE" "$CSV_OUT"
    echo "Exported to: $CSV_OUT"
fi
