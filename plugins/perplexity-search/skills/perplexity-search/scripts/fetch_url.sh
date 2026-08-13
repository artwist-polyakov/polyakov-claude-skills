#!/bin/sh
# fetch_url.sh — pull the contents of specific URLs through the Agent API's
# fetch_url tool. Useful when search.sh gave you the link and you now need the
# page itself, or when a page resists plain curl (JS, soft paywalls, redirects).
#
# The page text goes to cache/fetch/<key>.md; stdout stays short.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: sh scripts/fetch_url.sh --url "https://..." [--url ...] [options]

  --url, -u URL          page to fetch; repeat for several (required)
  --query, -q TEXT       what to pull out; default is the full readable content
  --preset NAME          fast | low | medium | high (default low)
  --model ID             override the preset model
  --language CODE        ISO 639-1 output language
  --max-output-tokens N  response cap (auto-set to 8192 for anthropic/* models)
  --limit N              stdout lines (default PPLX_PRINT_LIMIT=30)
  --cache-ttl SECONDS    reuse a cached identical request younger than this
  --no-cache             always hit the API
EOF
}

handle_help_flag "$@"
load_config

URLS=""
EXTRACT=""
LIMIT=""
NO_CACHE=""
CACHE_TTL=""

add_url() {
    case "$1" in
        http://*|https://*) : ;;
        *) die "Not an http(s) URL: $1" ;;
    esac
    if [ -z "$URLS" ]; then
        URLS="$1"
    else
        URLS="$(printf '%s\n%s' "$URLS" "$1")"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --url|-u)            require_value "$1" $#; add_url "$2"; shift 2 ;;
        --query|-q)          require_value "$1" $#; EXTRACT="$2"; shift 2 ;;
        --preset)            require_value "$1" $#; PPLX_ARG_PRESET="$2"; shift 2 ;;
        --model)             require_value "$1" $#; PPLX_ARG_MODEL="$2"; shift 2 ;;
        --language)          require_value "$1" $#; PPLX_ARG_LANGUAGE="$2"; shift 2 ;;
        --max-output-tokens) require_value "$1" $#; PPLX_ARG_MAX_OUTPUT_TOKENS="$2"; shift 2 ;;
        --limit)             require_value "$1" $#; LIMIT="$2"; shift 2 ;;
        --cache-ttl)         require_value "$1" $#; CACHE_TTL="$2"; shift 2 ;;
        --no-cache)          NO_CACHE=1; shift ;;
        -h|--help)           usage; exit 0 ;;
        *)                   usage >&2; die "Unknown option: $1" ;;
    esac
done

if [ -z "$URLS" ]; then
    usage >&2
    die "--url is required"
fi

LIMIT="${LIMIT:-$PPLX_PRINT_LIMIT}"
require_uint "--limit" "$LIMIT" 1
[ -z "$CACHE_TTL" ] || require_uint "--cache-ttl" "$CACHE_TTL"
[ -z "$PPLX_ARG_MAX_OUTPUT_TOKENS" ] || require_uint "--max-output-tokens" "$PPLX_ARG_MAX_OUTPUT_TOKENS" 1

if [ -z "$PPLX_ARG_PRESET" ] && [ -z "$PPLX_ARG_MODEL" ]; then
    PPLX_ARG_PRESET="low"
fi
PPLX_ARG_TOOLS="fetch_url"
PPLX_ARG_LANGUAGE="${PPLX_ARG_LANGUAGE:-$PPLX_LANGUAGE}"
PPLX_ARG_INSTRUCTIONS="Use the fetch_url tool on every URL the user lists. Report each URL under its own '## <url>' heading, quoting the page text as-is. Never invent content that the tool did not return; if a fetch fails, say so under that URL's heading."

if [ -n "$EXTRACT" ]; then
    PPLX_ARG_INPUT="$(printf 'Fetch these URLs and answer: %s\n\nURLs:\n%s' "$EXTRACT" "$URLS")"
else
    PPLX_ARG_INPUT="$(printf 'Fetch these URLs and return their readable content in full, preserving headings, lists and tables.\n\nURLs:\n%s' "$URLS")"
fi

OUT_DIR="$PPLX_CACHE_DIR/fetch"
mkdir -p "$OUT_DIR"

BODY_FILE="$PPLX_TMPDIR/pplx_fetch_body.$$.json"
trap 'rm -f "$BODY_FILE"' EXIT INT TERM
build_agent_body "$BODY_FILE"

KEY=$(cache_key "fetch|$(cat "$BODY_FILE")")
JSON_FILE="$OUT_DIR/$KEY.json"
MD_FILE="$OUT_DIR/$KEY.md"
FIRST_URL=$(printf '%s' "$URLS" | head -1)
SOURCE="live"

if [ -z "$NO_CACHE" ] && cache_fresh "$JSON_FILE" "${CACHE_TTL:-$PPLX_CACHE_TTL}"; then
    SOURCE="cache, $(format_age "$(cache_age_seconds "$JSON_FILE")") old"
else
    pplx_post "/v1/agent" "$BODY_FILE" "$JSON_FILE"
    index_append "fetch" "$KEY" "$FIRST_URL" "$JSON_FILE"
fi

render_agent_response "$JSON_FILE" "$MD_FILE" "$FIRST_URL" >/dev/null

echo "=== Perplexity Fetch ($SOURCE): $FIRST_URL ==="
print_head "$MD_FILE" "$LIMIT"
echo ""
echo "full content: $MD_FILE"
echo "raw json:     $JSON_FILE"
