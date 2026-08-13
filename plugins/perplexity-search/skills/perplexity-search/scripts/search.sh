#!/bin/sh
# search.sh — raw web results from the Perplexity Search API (POST /search).
# No model, no synthesis: ranked pages with snippets. Cheapest way to ground a claim.
# stdout is a ranked table only; snippets land in cache/search/<key>.txt.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: sh scripts/search.sh --query "text" [--query "second angle" ...] [options]

  --query, -q TEXT       search query; repeat up to 5 times (one billed request)
  --max-results N        1..20, default from PPLX_MAX_RESULTS (10)
  --recency VALUE        hour | day | week | month | year
  --after DATE           published on/after (YYYY-MM-DD or MM/DD/YYYY)
  --before DATE          published on/before
  --updated-after DATE   last modified on/after
  --updated-before DATE  last modified on/before
  --domains LIST         allowlist "nature.com,science.org" OR denylist "-reddit.com"
  --country CC           ISO 3166-1 alpha-2, e.g. US
  --language LIST        ISO 639-1 codes, e.g. en,ru
  --context-size SIZE    low | medium | high — how much page text is extracted
  --profile NAME         apply PPLX_PROFILE_<NAME>_* defaults from config/.env
  --limit N              stdout table rows (default PPLX_PRINT_LIMIT=30)
  --cache-ttl SECONDS    reuse a cached identical request younger than this
  --no-cache             always hit the API
EOF
}

handle_help_flag "$@"
load_config

LIMIT=""
NO_CACHE=""
CACHE_TTL=""
PROFILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --query|-q)       require_value "$1" $#; query_add "$2"; shift 2 ;;
        --max-results)    require_value "$1" $#; PPLX_ARG_MAX_RESULTS="$2"; shift 2 ;;
        --recency)        require_value "$1" $#; PPLX_ARG_RECENCY="$2"; shift 2 ;;
        --after)          require_value "$1" $#; PPLX_ARG_AFTER="$2"; shift 2 ;;
        --before)         require_value "$1" $#; PPLX_ARG_BEFORE="$2"; shift 2 ;;
        --updated-after)  require_value "$1" $#; PPLX_ARG_UPDATED_AFTER="$2"; shift 2 ;;
        --updated-before) require_value "$1" $#; PPLX_ARG_UPDATED_BEFORE="$2"; shift 2 ;;
        --domains)        require_value "$1" $#; PPLX_ARG_DOMAINS="$2"; shift 2 ;;
        --country)        require_value "$1" $#; PPLX_ARG_COUNTRY="$2"; shift 2 ;;
        --language)       require_value "$1" $#; PPLX_ARG_LANGUAGE="$2"; shift 2 ;;
        --context-size)   require_value "$1" $#; PPLX_ARG_CONTEXT_SIZE="$2"; shift 2 ;;
        --profile)        require_value "$1" $#; PROFILE="$2"; shift 2 ;;
        --limit)          require_value "$1" $#; LIMIT="$2"; shift 2 ;;
        --cache-ttl)      require_value "$1" $#; CACHE_TTL="$2"; shift 2 ;;
        --no-cache)       NO_CACHE=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                usage >&2; die "Unknown option: $1" ;;
    esac
done

if [ "$PPLX_ARG_QUERY_COUNT" -eq 0 ]; then
    usage >&2
    die "--query is required"
fi

resolve_profile "$PROFILE"

PPLX_ARG_MAX_RESULTS="${PPLX_ARG_MAX_RESULTS:-$PPLX_MAX_RESULTS}"
PPLX_ARG_CONTEXT_SIZE="${PPLX_ARG_CONTEXT_SIZE:-$PPLX_CONTEXT_SIZE}"
PPLX_ARG_COUNTRY="${PPLX_ARG_COUNTRY:-$PPLX_COUNTRY}"
PPLX_ARG_LANGUAGE="${PPLX_ARG_LANGUAGE:-$PPLX_LANGUAGE}"
LIMIT="${LIMIT:-$PPLX_PRINT_LIMIT}"

require_uint "--max-results" "$PPLX_ARG_MAX_RESULTS" 1 20
require_uint "--limit" "$LIMIT" 1
[ -z "$CACHE_TTL" ] || require_uint "--cache-ttl" "$CACHE_TTL"
validate_recency "$PPLX_ARG_RECENCY"
validate_context_size "$PPLX_ARG_CONTEXT_SIZE"
PPLX_ARG_AFTER=$(normalize_date "$PPLX_ARG_AFTER")
PPLX_ARG_BEFORE=$(normalize_date "$PPLX_ARG_BEFORE")
PPLX_ARG_UPDATED_AFTER=$(normalize_date "$PPLX_ARG_UPDATED_AFTER")
PPLX_ARG_UPDATED_BEFORE=$(normalize_date "$PPLX_ARG_UPDATED_BEFORE")

OUT_DIR="$PPLX_CACHE_DIR/search"
mkdir -p "$OUT_DIR"

BODY_FILE="$PPLX_TMPDIR/pplx_search_body.$$.json"
trap 'rm -f "$BODY_FILE"' EXIT INT TERM
build_search_body "$BODY_FILE"

# The cache key is the request body itself, so any changed filter is a new entry.
KEY=$(cache_key "search|$(cat "$BODY_FILE")")
JSON_FILE="$OUT_DIR/$KEY.json"
TSV_FILE="$OUT_DIR/$KEY.tsv"
TXT_FILE="$OUT_DIR/$KEY.txt"

# A query may span lines; keep the header to the first one.
FIRST_QUERY=$(query_get 1 | head -1)
SOURCE="live"

if [ -n "$CACHE_TTL" ]; then
    EFFECTIVE_TTL="$CACHE_TTL"
else
    EFFECTIVE_TTL=$(effective_cache_ttl "$PPLX_CACHE_TTL" "$PPLX_ARG_RECENCY")
fi

if [ -z "$NO_CACHE" ] && cache_fresh "$JSON_FILE" "$EFFECTIVE_TTL"; then
    SOURCE="cache, $(format_age "$(cache_age_seconds "$JSON_FILE")") old"
else
    pplx_post "/search" "$BODY_FILE" "$JSON_FILE"
    index_append "search" "$KEY" "$FIRST_QUERY" "$JSON_FILE"
fi

COUNT=$(render_search_response "$JSON_FILE" "$TSV_FILE" "$TXT_FILE")

echo "=== Perplexity Search ($SOURCE): $FIRST_QUERY ==="
if [ -n "$PPLX_PROFILE_LABEL" ]; then
    echo "profile: $PPLX_PROFILE_LABEL"
fi
echo "results: $COUNT"
print_head "$TSV_FILE" "$LIMIT"
echo ""
echo "snippets: $TXT_FILE"
echo "raw json: $JSON_FILE"
