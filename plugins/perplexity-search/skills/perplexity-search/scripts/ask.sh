#!/bin/sh
# ask.sh — grounded answer with citations via the Perplexity Agent API (POST /v1/agent).
# Use when you want a synthesized answer; use search.sh when you want raw sources.
# stdout shows the first --limit lines of the answer; the full report with all
# sources and snippets is written to cache/ask/<key>.md.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: sh scripts/ask.sh --query "question" [options]

  --query, -q TEXT       the question (required)
  --preset NAME          fast | low | medium | high | xhigh | wide-research
                         (default PPLX_PRESET=medium)
  --model ID             override the preset model, e.g. perplexity/sonar,
                         openai/gpt-5.6-sol, anthropic/claude-sonnet-5
  --tools LIST           web_search,fetch_url,finance_search,people_search,sandbox
                         (default web_search; merges with the preset's own tools)
  --instructions TEXT    system-level guidance for the run
  --schema FILE          JSON Schema file → structured JSON answer
  --max-output-tokens N  response cap (auto-set to 8192 for anthropic/* models)
  --recency VALUE        hour | day | week | month | year
  --after DATE           published on/after (YYYY-MM-DD or MM/DD/YYYY)
  --before DATE          published on/before
  --updated-after DATE   last modified on/after
  --updated-before DATE  last modified on/before
  --domains LIST         allowlist "nature.com,science.org" OR denylist "-reddit.com"
  --country CC           ISO 3166-1 alpha-2 for location-aware search
  --language CODE        ISO 639-1 answer language, e.g. ru
  --context-size SIZE    low | medium | high
  --max-results N        web_search results per call (1..50)
  --profile NAME         apply PPLX_PROFILE_<NAME>_* defaults from config/.env
  --limit N              stdout lines of the answer (default PPLX_PRINT_LIMIT=30)
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
        --query|-q)          require_value "$1" $#; PPLX_ARG_INPUT="$2"; shift 2 ;;
        --preset)            require_value "$1" $#; PPLX_ARG_PRESET="$2"; shift 2 ;;
        --model)             require_value "$1" $#; PPLX_ARG_MODEL="$2"; shift 2 ;;
        --tools)             require_value "$1" $#; PPLX_ARG_TOOLS="$2"; shift 2 ;;
        --instructions)      require_value "$1" $#; PPLX_ARG_INSTRUCTIONS="$2"; shift 2 ;;
        --schema)            require_value "$1" $#; PPLX_ARG_SCHEMA_FILE="$2"; shift 2 ;;
        --max-output-tokens) require_value "$1" $#; PPLX_ARG_MAX_OUTPUT_TOKENS="$2"; shift 2 ;;
        --recency)           require_value "$1" $#; PPLX_ARG_RECENCY="$2"; shift 2 ;;
        --after)             require_value "$1" $#; PPLX_ARG_AFTER="$2"; shift 2 ;;
        --before)            require_value "$1" $#; PPLX_ARG_BEFORE="$2"; shift 2 ;;
        --updated-after)     require_value "$1" $#; PPLX_ARG_UPDATED_AFTER="$2"; shift 2 ;;
        --updated-before)    require_value "$1" $#; PPLX_ARG_UPDATED_BEFORE="$2"; shift 2 ;;
        --domains)           require_value "$1" $#; PPLX_ARG_DOMAINS="$2"; shift 2 ;;
        --country)           require_value "$1" $#; PPLX_ARG_COUNTRY="$2"; shift 2 ;;
        --language)          require_value "$1" $#; PPLX_ARG_LANGUAGE="$2"; shift 2 ;;
        --context-size)      require_value "$1" $#; PPLX_ARG_CONTEXT_SIZE="$2"; shift 2 ;;
        --max-results)       require_value "$1" $#; PPLX_ARG_MAX_RESULTS="$2"; shift 2 ;;
        --profile)           require_value "$1" $#; PROFILE="$2"; shift 2 ;;
        --limit)             require_value "$1" $#; LIMIT="$2"; shift 2 ;;
        --cache-ttl)         require_value "$1" $#; CACHE_TTL="$2"; shift 2 ;;
        --no-cache)          NO_CACHE=1; shift ;;
        -h|--help)           usage; exit 0 ;;
        *)                   usage >&2; die "Unknown option: $1" ;;
    esac
done

if [ -z "$PPLX_ARG_INPUT" ]; then
    usage >&2
    die "--query is required"
fi
if [ -n "$PPLX_ARG_SCHEMA_FILE" ] && [ ! -f "$PPLX_ARG_SCHEMA_FILE" ]; then
    die "Schema file not found: $PPLX_ARG_SCHEMA_FILE"
fi

resolve_profile "$PROFILE"

# An explicit --model without a --preset means "just that model": don't silently
# mix in the config preset, which would fight the model choice.
if [ -z "$PPLX_ARG_PRESET" ] && [ -z "$PPLX_ARG_MODEL" ]; then
    PPLX_ARG_PRESET="$PPLX_PRESET"
    PPLX_ARG_MODEL="$PPLX_MODEL"
fi
PPLX_ARG_CONTEXT_SIZE="${PPLX_ARG_CONTEXT_SIZE:-$PPLX_CONTEXT_SIZE}"
PPLX_ARG_COUNTRY="${PPLX_ARG_COUNTRY:-$PPLX_COUNTRY}"
PPLX_ARG_LANGUAGE="${PPLX_ARG_LANGUAGE:-$PPLX_LANGUAGE}"
LIMIT="${LIMIT:-$PPLX_PRINT_LIMIT}"

[ -z "$PPLX_ARG_MAX_RESULTS" ] || require_uint "--max-results" "$PPLX_ARG_MAX_RESULTS" 1 50
[ -z "$PPLX_ARG_MAX_OUTPUT_TOKENS" ] || require_uint "--max-output-tokens" "$PPLX_ARG_MAX_OUTPUT_TOKENS" 1
require_uint "--limit" "$LIMIT" 1
[ -z "$CACHE_TTL" ] || require_uint "--cache-ttl" "$CACHE_TTL"
validate_tools "$PPLX_ARG_TOOLS"
validate_recency "$PPLX_ARG_RECENCY"
validate_context_size "$PPLX_ARG_CONTEXT_SIZE"
PPLX_ARG_AFTER=$(normalize_date "$PPLX_ARG_AFTER")
PPLX_ARG_BEFORE=$(normalize_date "$PPLX_ARG_BEFORE")
PPLX_ARG_UPDATED_AFTER=$(normalize_date "$PPLX_ARG_UPDATED_AFTER")
PPLX_ARG_UPDATED_BEFORE=$(normalize_date "$PPLX_ARG_UPDATED_BEFORE")

OUT_DIR="$PPLX_CACHE_DIR/ask"
mkdir -p "$OUT_DIR"

BODY_FILE="$PPLX_TMPDIR/pplx_ask_body.$$.json"
trap 'rm -f "$BODY_FILE"' EXIT INT TERM
build_agent_body "$BODY_FILE"

KEY=$(cache_key "ask|$(cat "$BODY_FILE")")
JSON_FILE="$OUT_DIR/$KEY.json"
MD_FILE="$OUT_DIR/$KEY.md"
SOURCE="live"

if [ -z "$NO_CACHE" ] && cache_fresh "$JSON_FILE" "${CACHE_TTL:-$PPLX_CACHE_TTL}"; then
    SOURCE="cache"
else
    pplx_post "/v1/agent" "$BODY_FILE" "$JSON_FILE"
    index_append "ask" "$KEY" "$PPLX_ARG_INPUT" "$JSON_FILE"
fi

SOURCES=$(render_agent_response "$JSON_FILE" "$MD_FILE" "$PPLX_ARG_INPUT")

echo "=== Perplexity Ask ($SOURCE) ==="
echo "sources: $SOURCES"
print_head "$MD_FILE" "$LIMIT"
echo ""
echo "full answer: $MD_FILE"
echo "raw json:    $JSON_FILE"
