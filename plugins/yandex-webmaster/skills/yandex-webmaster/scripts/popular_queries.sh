#!/bin/sh
# Popular search queries
# Usage: popular_queries.sh --host <domain> [--order-by TOTAL_SHOWS|TOTAL_CLICKS]
#        [--device ALL|DESKTOP|MOBILE_AND_TABLET] [--date-from] [--date-to] [--limit N]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

ORDER_BY="TOTAL_SHOWS"
DEVICE="ALL"

_args=""
while [ $# -gt 0 ]; do
    case "$1" in
        --order-by) ORDER_BY="$2"; shift 2 ;;
        --device)   DEVICE="$2"; shift 2 ;;
        *)          _args="$_args $1"; shift ;;
    esac
done
# shellcheck disable=SC2086
parse_host_params $_args
ensure_user_id
resolve_host
require_host

TMPFILE="${WM_TMPDIR}/wm_popular_q_$$.json"
trap 'rm -f "$TMPFILE"' EXIT

_curl_args="--data-urlencode order_by=$ORDER_BY"
# Compute cache path BEFORE API call
_host_dir=$(cache_host_dir)
mkdir -p "$_host_dir/queries"
_hash=$(cache_key "popular_v2_${ORDER_BY}_${DEVICE}_${DATE_FROM}_${DATE_TO}_${LIMIT}")
_out_file="$_host_dir/queries/popular_${_hash}.tsv"

# TTL cache check (24h)
if [ -z "$NO_CACHE" ] && cache_get_ttl "$_out_file" 1440; then
    print_tsv_head "$_out_file" 30
    echo ""
    echo "(cached: $_out_file)"
    exit 0
fi

_curl_args="$_curl_args --data-urlencode device_type_indicator=$DEVICE"
_curl_args="$_curl_args --data-urlencode query_indicator=TOTAL_SHOWS"
_curl_args="$_curl_args --data-urlencode query_indicator=TOTAL_CLICKS"
_curl_args="$_curl_args --data-urlencode query_indicator=AVG_SHOW_POSITION"
_curl_args="$_curl_args --data-urlencode query_indicator=AVG_CLICK_POSITION"

if [ -n "$LIMIT" ]; then
    _curl_args="$_curl_args --data-urlencode limit=$LIMIT"
fi
if [ -n "$DATE_FROM" ]; then
    _curl_args="$_curl_args --data-urlencode date_from=${DATE_FROM}T00:00:00.000+0300"
fi
if [ -n "$DATE_TO" ]; then
    _curl_args="$_curl_args --data-urlencode date_to=${DATE_TO}T00:00:00.000+0300"
fi

# shellcheck disable=SC2086
webmaster_get "/search-queries/popular" $_curl_args > "$TMPFILE"

{
    echo "query_id	query_text	shows	clicks	avg_show_pos	avg_click_pos"
    tr -d '\n\r' < "$TMPFILE" | sed 's/"query_id"/\n"query_id"/g' | while IFS= read -r _line || [ -n "$_line" ]; do
        _qid=$(json_extract_field_raw "$_line" "query_id")
        [ -z "$_qid" ] && continue
        _qt=$(json_extract_field_raw "$_line" "query_text")
        _shows=$(json_extract_number "$_line" "TOTAL_SHOWS")
        _clicks=$(json_extract_number "$_line" "TOTAL_CLICKS")
        _avgsp=$(json_extract_number "$_line" "AVG_SHOW_POSITION")
        _avgcp=$(json_extract_number "$_line" "AVG_CLICK_POSITION")
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$_qid" "$_qt" "${_shows:-0}" "${_clicks:-0}" "${_avgsp:--}" "${_avgcp:--}"
    done
} > "$_out_file"

print_tsv_head "$_out_file" 30
echo ""
echo "Cached: $_out_file"
