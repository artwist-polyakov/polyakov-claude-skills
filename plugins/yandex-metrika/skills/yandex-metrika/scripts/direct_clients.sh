#!/bin/sh
# Get Yandex Direct client logins linked to a Metrika counter
# Usage: direct_clients.sh --counter <ID> [--no-cache]
#
# Calls GET /management/v1/clients?counters=<counterId>
# Saves chief_login to cache/counter_<id>/direct_clients.json

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

COUNTER=""
NO_CACHE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --counter)  COUNTER="$2"; shift 2 ;;
        --no-cache) NO_CACHE="1"; shift ;;
        *)          shift ;;
    esac
done

if [ -z "$COUNTER" ]; then
    echo "Error: --counter <ID> is required." >&2
    echo "Usage: direct_clients.sh --counter <ID> [--no-cache]" >&2
    exit 1
fi

COUNTER_DIR=$(cache_dir_for_counter "$COUNTER")
CACHE_JSON="$COUNTER_DIR/direct_clients.json"

# Try cache first
if [ -z "$NO_CACHE" ] && [ -f "$CACHE_JSON" ] && [ -s "$CACHE_JSON" ]; then
    echo "Direct clients for counter $COUNTER:"
    # Extract logins from cache
    _all_accessible=$(grep -o '"all_clients_accessible_to_user"[[:space:]]*:[[:space:]]*[a-z]*' "$CACHE_JSON" | head -1 | sed 's/.*:[[:space:]]*//')
    grep -o '"chief_login"[[:space:]]*:[[:space:]]*"[^"]*"' "$CACHE_JSON" | sed 's/.*"chief_login"[[:space:]]*:[[:space:]]*"//;s/"$//' | while IFS= read -r _login || [ -n "$_login" ]; do
        echo "  - $_login"
    done
    if [ "$_all_accessible" = "false" ]; then
        echo ""
        echo "WARNING: Not all Direct clients are accessible. Cost data may be incomplete."
    fi
    echo ""
    echo "(cached: $CACHE_JSON)"
    exit 0
fi

# Fetch from API
echo "Fetching Direct clients for counter $COUNTER..." >&2

TMPFILE="${METRIKA_TMPDIR}/metrika_direct_clients_$$.json"
trap 'rm -f "$TMPFILE"' EXIT

metrika_mgmt_get "/management/v1/clients" \
    --data-urlencode "counters=$COUNTER" > "$TMPFILE"

# Save raw JSON
cp "$TMPFILE" "$CACHE_JSON"

# Output
echo "Direct clients for counter $COUNTER:"

_all_accessible=$(grep -o '"all_clients_accessible_to_user"[[:space:]]*:[[:space:]]*[a-z]*' "$CACHE_JSON" | head -1 | sed 's/.*:[[:space:]]*//')

_count=0
grep -o '"chief_login"[[:space:]]*:[[:space:]]*"[^"]*"' "$CACHE_JSON" | sed 's/.*"chief_login"[[:space:]]*:[[:space:]]*"//;s/"$//' | while IFS= read -r _login || [ -n "$_login" ]; do
    echo "  - $_login"
    _count=$(( _count + 1 ))
done

if [ "$_all_accessible" = "false" ]; then
    echo ""
    echo "WARNING: Not all Direct clients are accessible. Cost data may be incomplete."
fi

echo ""
echo "Cached: $CACHE_JSON"
