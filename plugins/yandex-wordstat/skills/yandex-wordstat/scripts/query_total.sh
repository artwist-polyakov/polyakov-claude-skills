#!/bin/sh
# Get totalCount from Yandex Wordstat for an OR-query.
# Routes through common.sh wordstat_request.
#
# Usage:
#   sh scripts/query_total.sh --phrase "(купить|заказать) телефон ретро" [--regions "213"]
#
# Output: JSON {"total_count": N, "query": "..."} or {"error": "...", "query": "..."}

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

# Parse arguments
PHRASE=""
REGIONS=""

while [ $# -gt 0 ]; do
    case $1 in
        --phrase|-p) PHRASE="$2"; shift 2 ;;
        --regions|-r) REGIONS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$PHRASE" ]; then
    echo "Usage: query_total.sh --phrase \"(a|b) query\" [--regions \"213\"]"
    echo ""
    echo "Options:"
    echo "  --phrase, -p   Search phrase with operators and minus-words (required)"
    echo "  --regions, -r  Region IDs, comma-separated (optional)"
    echo ""
    echo "Output: JSON with total_count"
    exit 1
fi

load_config

# Build JSON params (same shape as top_requests.sh).
PHRASE_ESCAPED=$(json_escape "$PHRASE")
PARAMS="{\"phrase\":\"$PHRASE_ESCAPED\""
if [ -n "$REGIONS" ]; then
    PARAMS="$PARAMS,\"regions\":[$REGIONS]"
fi
PARAMS="$PARAMS}"

TMPFILE="${TMPDIR:-/tmp}/ws_query_total_$$.json"
cleanup() { rm -f "$TMPFILE"; }
trap cleanup EXIT

# common.sh writes normalized JSON to stdout.
wordstat_request "topRequests" "$PARAMS" > "$TMPFILE"

# Delegate totalCount extraction + JSON formatting to Python helper.
uv run --script "$SCRIPT_DIR/missed_demand.py" query-total \
    --json-file "$TMPFILE" \
    --phrase "$PHRASE"
