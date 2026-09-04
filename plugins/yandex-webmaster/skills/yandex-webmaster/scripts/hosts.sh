#!/bin/sh
# List Yandex Webmaster sites with cache + TSV index
# Usage: hosts.sh [--no-cache] [--search <text>]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

SEARCH=""
NO_CACHE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-cache) NO_CACHE="1"; shift ;;
        --search)   SEARCH="$2"; shift 2 ;;
        *)          shift ;;
    esac
done

SEARCH_NORMALIZED=$(normalize_host_search "$SEARCH")
ensure_user_id

CACHE_TSV="$CACHE_DIR/hosts.tsv"

# Try cache first
if [ -z "$NO_CACHE" ] && [ -f "$CACHE_TSV" ] && [ -s "$CACHE_TSV" ]; then
    if [ -n "$SEARCH" ]; then
        echo "host_id	url	verified"
        grep -i -- "$SEARCH_NORMALIZED" "$CACHE_TSV" || echo "(no matches for '$SEARCH')"
    else
        # Prepend header for print_tsv_head
        _tmp_display="${WM_TMPDIR}/wm_hosts_display_$$.tsv"
        { echo "host_id	url	verified"; cat "$CACHE_TSV"; } > "$_tmp_display"
        print_tsv_head "$_tmp_display" 30
        rm -f "$_tmp_display"
    fi
    echo ""
    echo "(cached: $CACHE_TSV)"
    exit 0
fi

# Fetch from API
echo "Fetching hosts from API..." >&2

TMPFILE="${WM_TMPDIR}/wm_hosts_list_$$.json"
trap 'rm -f "$TMPFILE"' EXIT

webmaster_user_get "/hosts" > "$TMPFILE"

# Save raw JSON
mkdir -p "$CACHE_DIR"
cp "$TMPFILE" "$CACHE_DIR/hosts.json"

# Generate TSV: host_id<TAB>url<TAB>verified
{
    tr -d '\n\r' < "$TMPFILE" | sed 's/},{/}\n{/g' | while IFS= read -r _line || [ -n "$_line" ]; do
        _hid=$(json_extract_field_raw "$_line" "host_id")
        _url=$(json_extract_field_raw "$_line" "ascii_host_url")
        _ver=$(json_extract_bool "$_line" "verified")
        [ -z "$_hid" ] && continue
        printf '%s\t%s\t%s\n' "$_hid" "$_url" "$_ver"
    done
} > "$CACHE_TSV"

# Output
echo "host_id	url	verified"
if [ -n "$SEARCH" ]; then
    grep -i -- "$SEARCH_NORMALIZED" "$CACHE_TSV" || echo "(no matches for '$SEARCH')"
else
    print_tsv_head "$CACHE_TSV" 30
fi
echo ""
echo "Total hosts: $(wc -l < "$CACHE_TSV" | tr -d ' ')"
echo "Cached: $CACHE_TSV"
