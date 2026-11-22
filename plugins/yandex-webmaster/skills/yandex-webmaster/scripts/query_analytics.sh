#!/bin/sh
# Advanced query analytics (POST endpoint, last 14 days)
# Usage: query_analytics.sh --host <domain> --text-indicator QUERY|URL
#        [--filter-text "..."] [--filter-impressions ">100"] [--filter-clicks "<50"]
#        [--filter-position "<10"] [--filter-ctr ">0.05"] [--filter-demand ">1000"]
#        [--region-ids "213,2"] [--device ALL|DESKTOP|MOBILE_AND_TABLET|MOBILE|TABLET]
#        [--search-location WEB_LOCATION|ALL_LOCATIONS] [--limit N] [--offset N]
#        [--sort-by ASC|DESC]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

TEXT_INDICATOR="QUERY"
FILTER_TEXT=""
FILTER_IMPRESSIONS=""
FILTER_CLICKS=""
FILTER_POSITION=""
FILTER_CTR=""
FILTER_DEMAND=""
REGION_IDS=""
DEVICE_QA="ALL"
SEARCH_LOCATION="WEB_LOCATION"
SORT_BY=""

_args=""
while [ $# -gt 0 ]; do
    case "$1" in
        --text-indicator)    TEXT_INDICATOR="$2"; shift 2 ;;
        --filter-text)       FILTER_TEXT="$2"; shift 2 ;;
        --filter-impressions) FILTER_IMPRESSIONS="$2"; shift 2 ;;
        --filter-clicks)     FILTER_CLICKS="$2"; shift 2 ;;
        --filter-position)   FILTER_POSITION="$2"; shift 2 ;;
        --filter-ctr)        FILTER_CTR="$2"; shift 2 ;;
        --filter-demand)     FILTER_DEMAND="$2"; shift 2 ;;
        --region-ids)        REGION_IDS="$2"; shift 2 ;;
        --device)            DEVICE_QA="$2"; shift 2 ;;
        --search-location)   SEARCH_LOCATION="$2"; shift 2 ;;
        --sort-by)           SORT_BY="$2"; shift 2 ;;
        *)                   _args="$_args $1"; shift ;;
    esac
done
# shellcheck disable=SC2086
parse_host_params $_args
ensure_user_id
resolve_host
require_host

TMPFILE="${WM_TMPDIR}/wm_qa_$$.json"
TMPFILE2="${WM_TMPDIR}/wm_qa_parsed_$$.tsv"
trap 'rm -f "$TMPFILE" "$TMPFILE2"' EXIT

# Build JSON request body
_limit="${LIMIT:-500}"
_offset="${OFFSET:-0}"

_json="{\"offset\":$_offset,\"limit\":$_limit"
_json="$_json,\"device_type_indicator\":\"$DEVICE_QA\""
_json="$_json,\"text_indicator\":\"$TEXT_INDICATOR\""
_json="$_json,\"search_location\":\"$SEARCH_LOCATION\""

# Filters
_filters=""

# Text filter (with JSON escaping)
if [ -n "$FILTER_TEXT" ]; then
    _escaped_ft=$(json_escape "$FILTER_TEXT")
    _filters="$_filters,\"text_filters\":[{\"text_indicator\":\"$TEXT_INDICATOR\",\"operation\":\"TEXT_CONTAINS\",\"value\":\"$_escaped_ft\"}]"
fi

# Statistic filters
_stat_filters=""
_build_stat_filter() {
    _bsf_val="$1"
    _bsf_field="$2"
    if [ -n "$_bsf_val" ]; then
        _bsf_op=""
        _bsf_num=""
        case "$_bsf_val" in
            ">="*) _bsf_op="GREATER_EQUAL";   _bsf_num=$(echo "$_bsf_val" | sed 's/^>=//') ;;
            "<="*) _bsf_op="LESS_EQUAL";       _bsf_num=$(echo "$_bsf_val" | sed 's/^<=//') ;;
            ">"*)  _bsf_op="GREATER_THAN";     _bsf_num=$(echo "$_bsf_val" | sed 's/^>//') ;;
            "<"*)  _bsf_op="LESS_THAN";        _bsf_num=$(echo "$_bsf_val" | sed 's/^<//') ;;
            "="*)  _bsf_op="EQUAL";            _bsf_num=$(echo "$_bsf_val" | sed 's/^=//') ;;
            *)     _bsf_op="GREATER_THAN";     _bsf_num="$_bsf_val" ;;
        esac
        if [ -n "$_stat_filters" ]; then
            _stat_filters="$_stat_filters,"
        fi
        _stat_filters="${_stat_filters}{\"field\":\"$_bsf_field\",\"operation\":\"$_bsf_op\",\"value\":\"$_bsf_num\"}"
    fi
}

_build_stat_filter "$FILTER_IMPRESSIONS" "IMPRESSIONS"
_build_stat_filter "$FILTER_CLICKS" "CLICKS"
_build_stat_filter "$FILTER_POSITION" "POSITION"
_build_stat_filter "$FILTER_CTR" "CTR"
_build_stat_filter "$FILTER_DEMAND" "DEMAND"

if [ -n "$_stat_filters" ]; then
    _filters="$_filters,\"statistic_filters\":[$_stat_filters]"
fi

if [ -n "$_filters" ]; then
    _json="$_json,\"filters\":{$(echo "$_filters" | sed 's/^,//')}"
fi

# Region IDs
if [ -n "$REGION_IDS" ]; then
    _regions=$(echo "$REGION_IDS" | sed 's/,/,/g')
    _json="$_json,\"region_ids\":[$_regions]"
fi

# Sort
if [ -n "$SORT_BY" ]; then
    _json="$_json,\"sort_by_date\":{\"OrderDirection\":\"$SORT_BY\"}"
fi

_json="$_json}"

# Compute cache path BEFORE API call
_host_dir=$(cache_host_dir)
mkdir -p "$_host_dir/queries"
# Hash the exact payload sent below, including pagination and every filter.
# The versioned prefix also prevents reuse of legacy partial-key cache entries.
_hash=$(cache_key "analytics_v2_${_json}")
_out_file="$_host_dir/queries/analytics_${_hash}.tsv"

# TTL cache check (24h)
if [ -z "$NO_CACHE" ] && cache_get_ttl "$_out_file" 1440; then
    print_tsv_head "$_out_file" 30
    echo ""
    echo "(cached: $_out_file)"
    exit 0
fi

webmaster_post "/query-analytics/list" "$_json" > "$TMPFILE"

_count=$(json_extract_number "$(cat "$TMPFILE")" "count")

# Split response into one block per text_indicator, extract fields with sed
# Each block contains: "value":"<text>", then "field":"<name>","value":<num> pairs
tr -d '\n\r' < "$TMPFILE" | sed 's/"text_indicator"/\n"text_indicator"/g' > "$TMPFILE2"

echo "text	impressions	clicks	ctr	position	demand" > "$_out_file"

while IFS= read -r _line || [ -n "$_line" ]; do
    _text_val=$(json_extract_field_raw "$_line" "value")
    [ -z "$_text_val" ] && continue

    # Extract all field:value pairs inline, no subshell
    _impr=$(printf '%s' "$_line" | grep -o '"field":"IMPRESSIONS"[^}]*' | head -1 | grep -o '"value":[0-9.e+-]*' | sed 's/"value"://')
    _cl=$(printf '%s' "$_line" | grep -o '"field":"CLICKS"[^}]*' | head -1 | grep -o '"value":[0-9.e+-]*' | sed 's/"value"://')
    _ctr=$(printf '%s' "$_line" | grep -o '"field":"CTR"[^}]*' | head -1 | grep -o '"value":[0-9.e+-]*' | sed 's/"value"://')
    _pos=$(printf '%s' "$_line" | grep -o '"field":"POSITION"[^}]*' | head -1 | grep -o '"value":[0-9.e+-]*' | sed 's/"value"://')
    _dem=$(printf '%s' "$_line" | grep -o '"field":"DEMAND"[^}]*' | head -1 | grep -o '"value":[0-9.e+-]*' | sed 's/"value"://')

    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$_text_val" "${_impr:--}" "${_cl:--}" "${_ctr:--}" "${_pos:--}" "${_dem:--}"
done < "$TMPFILE2" >> "$_out_file"

# Output with 30-line limit
print_tsv_head "$_out_file" 30

echo ""
echo "Total results: ${_count:-?}"
echo "Cached: $_out_file"
echo "(Data covers last 14 days only)"
