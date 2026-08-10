#!/bin/sh
# Synchronous web search via Yandex Cloud Search API v2
# Usage: web_search_sync.sh --query "search text" [--region-id 225] [--results 10] [--page 0]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

check_prerequisites
load_config

# Defaults from config
QUERY=""
REGION_ID=$(cfg_get "search.region_id" "225")
SEARCH_TYPE=$(cfg_get "search.search_type" "SEARCH_TYPE_RU")
FAMILY_MODE=$(cfg_get "search.family_mode" "FAMILY_MODE_MODERATE")
FIX_TYPO=$(cfg_get "search.fix_typo_mode" "FIX_TYPO_MODE_ON")
RESULTS_PER_PAGE=$(cfg_get "search.results_per_page" "10")
PAGE=0
QUERIES_FILE=""

while [ $# -gt 0 ]; do
    case $1 in
        --query|-q) QUERY="$2"; shift 2 ;;
        --region-id|-r) REGION_ID="$2"; shift 2 ;;
        --results|-n) RESULTS_PER_PAGE="$2"; shift 2 ;;
        --page|-p) PAGE="$2"; shift 2 ;;
        --search-type) SEARCH_TYPE="$2"; shift 2 ;;
        --family-mode) FAMILY_MODE="$2"; shift 2 ;;
        --file|-f) QUERIES_FILE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$QUERY" ] && [ -z "$QUERIES_FILE" ]; then
    echo "Usage: web_search_sync.sh --query \"search text\" [options]"
    echo "       web_search_sync.sh --file queries.txt [options]"
    echo ""
    echo "Options:"
    echo "  --query, -q        Search query text"
    echo "  --file, -f         File with queries (one per line)"
    echo "  --region-id, -r    Region ID (default: $REGION_ID)"
    echo "  --results, -n      Results per page 1-100 (default: $RESULTS_PER_PAGE)"
    echo "  --page, -p         Page number 0+ (default: 0)"
    echo "  --search-type      SEARCH_TYPE_RU|SEARCH_TYPE_TR|SEARCH_TYPE_COM|SEARCH_TYPE_KK|SEARCH_TYPE_BE|SEARCH_TYPE_UZ"
    echo "  --family-mode      FAMILY_MODE_NONE|FAMILY_MODE_MODERATE|FAMILY_MODE_STRICT"
    echo ""
    echo "Examples:"
    echo "  bash scripts/web_search_sync.sh --query \"купить дымоход\" --region-id 213"
    echo "  bash scripts/web_search_sync.sh --file queries.txt --region-id 225"
    exit 1
fi

# Create results directory
mkdir -p "$CACHE_DIR/results"

# Function to search a single query
search_single() {
    _sq_query="$1"
    _sq_hash=$(file_hash "$_sq_query")

    echo "--- Searching: $_sq_query (hash: $_sq_hash) ---" >&2

    # Build request body (query passed via env to avoid shell injection)
    _body=$(_YSA_QUERY="$_sq_query" python3 -c "
import json, os
body = {
    'query': {
        'searchType': '$SEARCH_TYPE',
        'queryText': os.environ['_YSA_QUERY'],
        'familyMode': '$FAMILY_MODE',
        'fixTypoMode': '$FIX_TYPO',
        'page': $PAGE
    },
    'sortSpec': {},
    'groupSpec': {
        'groupMode': 'GROUP_MODE_FLAT',
        'groupsOnPage': $RESULTS_PER_PAGE,
        'docsInGroup': 1
    },
    'maxPassages': 3,
    'region': '$REGION_ID',
    'l10n': 'LOCALIZATION_RU',
    'folderId': '$(cfg_get "yandex_cloud_folder_id")'
}
print(json.dumps(body, ensure_ascii=False))
")

    # Make API call
    _response=$(auth_request "POST" "$SEARCH_API_URL/v2/web/search" "$_body") || {
        echo "Error: Search API call failed for query: $_sq_query" >&2
        return 1
    }

    # Extract rawData and decode
    _raw_b64=$(echo "$_response" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('rawData', ''))
")

    if [ -z "$_raw_b64" ]; then
        echo "Error: No rawData in response for query: $_sq_query" >&2
        echo "Response saved to: $CACHE_DIR/results/${_sq_hash}_error.json" >&2
        echo "$_response" > "$CACHE_DIR/results/${_sq_hash}_error.json"
        return 1
    fi

    # Decode base64 -> raw XML/HTML
    echo "$_raw_b64" | b64_decode > "$CACHE_DIR/results/${_sq_hash}.raw"

    # Parse XML to JSON
    parse_search_xml "$CACHE_DIR/results/${_sq_hash}.raw" > "$CACHE_DIR/results/${_sq_hash}.json"

    # Print summary
    echo ""
    echo "=== Results for: $_sq_query ==="
    echo "Region: $REGION_ID | Page: $PAGE"
    echo ""

    python3 -c "
import json
with open('$CACHE_DIR/results/${_sq_hash}.json') as f:
    results = json.load(f)

if isinstance(results, dict) and 'error' in results:
    print(f'  Parse error: {results[\"error\"]}')
    print(f'  Raw data saved to: $CACHE_DIR/results/${_sq_hash}.raw')
else:
    shown = min(len(results), 10)
    for r in results[:shown]:
        pos = r.get('position', '?')
        title = r.get('title', 'No title')[:80]
        url = r.get('url', '')
        snippet = r.get('snippet', '')[:120]
        print(f'  {pos}. {title}')
        print(f'     {url}')
        if snippet:
            print(f'     {snippet}')
        print()

    total = len(results)
    if total > shown:
        print(f'  ... and {total - shown} more results')
    print(f'  Total: {total} results')

print()
print(f'Files:')
print(f'  Raw: $CACHE_DIR/results/${_sq_hash}.raw')
print(f'  JSON: $CACHE_DIR/results/${_sq_hash}.json')
"
}

# Execute search(es)
if [ -n "$QUERIES_FILE" ]; then
    if [ ! -f "$QUERIES_FILE" ]; then
        echo "Error: Queries file not found: $QUERIES_FILE" >&2
        exit 1
    fi
    _total=0
    _ok=0
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(echo "$_line" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
        if [ -n "$_line" ]; then
            _total=$((_total + 1))
            if search_single "$_line"; then
                _ok=$((_ok + 1))
            fi
        fi
    done < "$QUERIES_FILE"
    echo ""
    echo "=== Batch complete: $_ok/$_total queries processed ==="
else
    search_single "$QUERY"
fi
