#!/bin/sh
# Get top search phrases from Yandex Wordstat
# POSIX sh compatible — works in cloud sandboxes and locally
# Uses temp file for API response to avoid stdout buffer overflow

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

load_config

# Defaults
PHRASE=""
REGIONS=""
DEVICES="all"
LIMIT=""
CSV_FILE=""
CSV_SEP=";"
STDOUT_MAX=20

# Temp file for API response (avoids piping huge strings through stdout)
TMPFILE="${TMPDIR:-/tmp}/ws_result_$$.json"
cleanup() { rm -f "$TMPFILE"; }
trap cleanup EXIT

# Parse args
while [ $# -gt 0 ]; do
    case $1 in
        --phrase|-p) PHRASE="$2"; shift 2 ;;
        --regions|-r) REGIONS="$2"; shift 2 ;;
        --devices|-d) DEVICES="$2"; shift 2 ;;
        --limit|-l) LIMIT="$2"; shift 2 ;;
        --csv|-c) CSV_FILE="$2"; shift 2 ;;
        --sep) CSV_SEP="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$PHRASE" ]; then
    echo "Usage: top_requests.sh --phrase \"search query\" [options]"
    echo ""
    echo "Options:"
    echo "  --phrase, -p   Search phrase (required)"
    echo "  --regions, -r  Region IDs, comma-separated (optional)"
    echo "  --devices, -d  Device filter: all, desktop, phone, tablet (default: all)"
    echo "  --limit, -l    Number of results: 1-2000 (API default: 50)"
    echo "  --csv, -c      Export to CSV file (UTF-8 with BOM, semicolon-separated)"
    echo "  --sep          CSV separator (default: ;)"
    echo ""
    echo "Examples:"
    echo "  sh scripts/top_requests.sh --phrase \"юрист по дтп\""
    echo "  sh scripts/top_requests.sh --phrase \"юрист дтп\" --limit 500"
    echo "  sh scripts/top_requests.sh --phrase \"юрист дтп\" --limit 2000 --csv report.csv"
    exit 1
fi

# Validate --limit
if [ -n "$LIMIT" ]; then
    if ! echo "$LIMIT" | grep -qE '^[0-9]+$'; then
        echo "Error: --limit must be a positive integer (1-2000)"
        exit 1
    fi
    if [ "$LIMIT" -lt 1 ] || [ "$LIMIT" -gt 2000 ]; then
        echo "Error: --limit must be between 1 and 2000 (got: $LIMIT)"
        exit 1
    fi
fi

# Escape a value for CSV (RFC 4180)
csv_escape() {
    _csv_val=$(printf '%s' "$1" | tr -d '\n\r')
    _csv_val=$(printf '%s' "$_csv_val" | sed 's/"/""/g')
    printf '"%s"' "$_csv_val"
}

# Build JSON parameters (common.sh translates them to the cloud format).
PHRASE_ESCAPED=$(json_escape "$PHRASE")
PARAMS="{\"phrase\":\"$PHRASE_ESCAPED\""

if [ -n "$LIMIT" ]; then
    PARAMS="$PARAMS,\"numPhrases\":$LIMIT"
fi

if [ -n "$REGIONS" ]; then
    PARAMS="$PARAMS,\"regions\":[$REGIONS]"
fi

if [ "$DEVICES" != "all" ]; then
    PARAMS="$PARAMS,\"devices\":\"$DEVICES\""
fi

PARAMS="$PARAMS}"

echo "=== Yandex Wordstat: Top Requests ==="
echo "Phrase: $PHRASE"
[ -n "$REGIONS" ] && echo "Regions: $REGIONS"
echo "Devices: $DEVICES"
[ -n "$LIMIT" ] && echo "Limit: $LIMIT"
[ -n "$CSV_FILE" ] && echo "Export: $CSV_FILE (sep='$CSV_SEP')"
echo "Backend: $WORDSTAT_BACKEND"
echo ""
echo "Fetching data..."

# common.sh writes normalized JSON to stdout.
wordstat_request "topRequests" "$PARAMS" | tr -d '\n\r' > "$TMPFILE"

# Check for error
if grep -q '"error"' "$TMPFILE"; then
    echo "Error:"
    cat "$TMPFILE"
    exit 1
fi

# Init CSV file
if [ -n "$CSV_FILE" ]; then
    printf '\xEF\xBB\xBF' > "$CSV_FILE"
    printf 'n%sphrase%simpressions%stype\n' "$CSV_SEP" "$CSV_SEP" "$CSV_SEP" >> "$CSV_FILE"
fi

# Extract totalCount
total_count=$(grep -o '"totalCount":[0-9]*' "$TMPFILE" | head -1 | sed 's/"totalCount"://')

echo ""
echo "=== Top Requests ==="
if [ -n "$total_count" ]; then
    echo "Total count (broad match): $(format_number "$total_count")"
fi
echo ""
echo "| # | Phrase | Impressions |"
echo "|---|--------|-------------|"

# Process JSON array of {phrase, count} entries
process_entries() {
    _pe_str="$1"
    _pe_type="$2"
    _pe_rank=0

    _pe_total=$(printf '%s' "$_pe_str" | grep -o '{"phrase":"[^"]*","count":[0-9]*}' | wc -l | tr -d ' ')

    printf '%s' "$_pe_str" | grep -o '{"phrase":"[^"]*","count":[0-9]*}' | while IFS= read -r _pe_entry; do
        _pe_rank=$((_pe_rank + 1))

        _pe_phrase=$(printf '%s' "$_pe_entry" | grep -o '"phrase":"[^"]*"' | sed 's/"phrase":"//' | tr -d '"')
        _pe_shows=$(printf '%s' "$_pe_entry" | grep -o '"count":[0-9]*' | sed 's/"count"://')

        # stdout: show all if no CSV, or first STDOUT_MAX rows if CSV mode
        if [ -z "$CSV_FILE" ] || [ "$_pe_rank" -le "$STDOUT_MAX" ]; then
            echo "| $_pe_rank | $_pe_phrase | $(format_number "$_pe_shows") |"
        elif [ "$_pe_rank" -eq $(($STDOUT_MAX + 1)) ]; then
            echo "| ... | ... and $(($_pe_total - $STDOUT_MAX)) more rows in CSV | ... |"
        fi

        # CSV: always write all rows
        if [ -n "$CSV_FILE" ]; then
            printf '%s%s%s%s%s%s%s\n' \
                "$_pe_rank" "$CSV_SEP" \
                "$(csv_escape "$_pe_phrase")" "$CSV_SEP" \
                "$_pe_shows" "$CSV_SEP" \
                "$_pe_type" >> "$CSV_FILE"
        fi
    done
}

# Parse topRequests array — extract from file, not variable
top_entries=$(sed -n 's/.*"topRequests":\[\([^]]*\)\].*/\1/p' "$TMPFILE" | head -1)
process_entries "$top_entries" "top"

# Parse associations array
assoc_entries=$(sed -n 's/.*"associations":\[\([^]]*\)\].*/\1/p' "$TMPFILE" | head -1)

if [ -n "$assoc_entries" ] && printf '%s' "$assoc_entries" | grep -q '"phrase"'; then
    echo ""
    echo "=== Associations (similar queries) ==="
    echo ""
    echo "| # | Phrase | Impressions |"
    echo "|---|--------|-------------|"
    process_entries "$assoc_entries" "assoc"
fi

# Summary
echo ""
if [ -n "$CSV_FILE" ]; then
    csv_lines=$(($(wc -l < "$CSV_FILE") - 1))
    echo "CSV exported: $CSV_FILE ($csv_lines rows, sep='$CSV_SEP')"
fi
