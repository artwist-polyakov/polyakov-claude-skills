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
SNIPPETS=$(cfg_get "search.smart_snippets.enabled" "true")
SNIPPET_DOCS=$(cfg_get "search.smart_snippets.docs" "20")
PAGE=0
QUERIES_FILE=""
RESULTS_EXPLICIT=0
BATCH=0

while [ $# -gt 0 ]; do
    case $1 in
        --query|-q) QUERY="$2"; shift 2 ;;
        --region-id|-r) REGION_ID="$2"; shift 2 ;;
        --results|-n) RESULTS_PER_PAGE="$2"; RESULTS_EXPLICIT=1; shift 2 ;;
        --page|-p) PAGE="$2"; shift 2 ;;
        --search-type) SEARCH_TYPE="$2"; shift 2 ;;
        --family-mode) FAMILY_MODE="$2"; shift 2 ;;
        --file|-f) QUERIES_FILE="$2"; shift 2 ;;
        --snippets) SNIPPETS="true"; shift ;;
        --no-snippets) SNIPPETS="false"; shift ;;
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
    echo "  --results, -n      Results per page 1-100 (default: $SNIPPET_DOCS with snippets, else $RESULTS_PER_PAGE)"
    echo "  --page, -p         Page number 0+ (default: 0)"
    echo "  --search-type      SEARCH_TYPE_RU|SEARCH_TYPE_TR|SEARCH_TYPE_COM|SEARCH_TYPE_KK|SEARCH_TYPE_BE|SEARCH_TYPE_UZ"
    echo "  --family-mode      FAMILY_MODE_NONE|FAMILY_MODE_MODERATE|FAMILY_MODE_STRICT"
    echo "  --snippets         Ask for smart snippets — page extracts (default: $SNIPPETS)"
    echo "  --no-snippets      Links and short snippets only"
    echo ""
    echo "Examples:"
    echo "  bash scripts/web_search_sync.sh --query \"купить дымоход\" --region-id 213"
    echo "  bash scripts/web_search_sync.sh --file queries.txt --region-id 225"
    exit 1
fi

SNIPPETS_ON=0
if [ "$SNIPPETS" = "true" ]; then
    if [ "$SEARCH_TYPE" != "$SMART_SNIPPETS_SEARCH_TYPE" ]; then
        # Инфоконтексты живут только в русской поисковой базе. Молча подменять
        # выбранный пользователем тип поиска нельзя — ищем то, что просили,
        # но уже без них.
        echo "Note: smart snippets need $SMART_SNIPPETS_SEARCH_TYPE, got $SEARCH_TYPE — searching without them" >&2
    else
        SNIPPETS_ON=1
        if [ "$RESULTS_EXPLICIT" -eq 0 ]; then
            RESULTS_PER_PAGE="$SNIPPET_DOCS"
        fi
    fi
fi

# Показать тело запроса и выйти, ничего не оплачивая. Этим же режимом
# офлайн-тесты проверяют разбор флагов и подстановку дефолтов.
if [ "${YSA_DRY_RUN:-0}" = "1" ]; then
    build_search_body "$QUERY" "$REGION_ID" "$RESULTS_PER_PAGE" "$PAGE" "$SNIPPETS_ON"
    exit 0
fi

# Ensure IAM token is available
_token=$(get_cached_iam_token)
if [ -z "$_token" ]; then
    echo "No valid IAM token. Generating..." >&2
    sh "$SCRIPT_DIR/iam_token_get.sh"
fi

# Create results directory
mkdir -p "$CACHE_DIR/results"

# Function to search a single query
search_single() {
    _sq_query="$1"
    _sq_hash=$(file_hash "$_sq_query")

    echo "--- Searching: $_sq_query (hash: $_sq_hash) ---" >&2

    _body=$(build_search_body "$_sq_query" "$REGION_ID" "$RESULTS_PER_PAGE" "$PAGE" "$SNIPPETS_ON")

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

    # Decode base64 -> тело ответа (XML или JSON, решает сервер)
    echo "$_raw_b64" | b64_decode > "$CACHE_DIR/results/${_sq_hash}.raw"

    # Разобрать ответ: XML для обычной выдачи, JSON для инфоконтекстов
    _sq_json="$CACHE_DIR/results/${_sq_hash}.json"
    parse_search_response "$CACHE_DIR/results/${_sq_hash}.raw" > "$_sq_json"

    # Выдержки целиком не помещаются в буфер stdout песочницы — складываем их
    # в markdown-пак, а печатаем только индекс.
    _sq_pack=""
    if [ "$SNIPPETS_ON" -eq 1 ]; then
        _sq_pack="$CACHE_DIR/results/${_sq_hash}.md"
        render_snippet_pack "$_sq_json" "$_sq_pack" "$_sq_query" "$REGION_ID" >/dev/null || _sq_pack=""
    fi

    if [ "$BATCH" -eq 1 ]; then
        render_results_table "$_sq_json" "$_sq_pack" "line" "$_sq_query"
        return 0
    fi

    echo ""
    echo "=== Results for: $_sq_query ==="
    echo "Region: $REGION_ID | Page: $PAGE | Snippets: $SNIPPETS"
    echo ""

    render_results_table "$_sq_json" "$_sq_pack" "full" "$_sq_query"

    echo "  JSON: $_sq_json"
    echo "  Raw:  $CACHE_DIR/results/${_sq_hash}.raw"
}

# Execute search(es)
if [ -n "$QUERIES_FILE" ]; then
    if [ ! -f "$QUERIES_FILE" ]; then
        echo "Error: Queries file not found: $QUERIES_FILE" >&2
        exit 1
    fi
    BATCH=1
    echo "  запрос                                     результаты            пак"
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
