#!/bin/sh
# POST /search request body: query shapes, filters, and the documented limits.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_search_body_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
PPLX_CACHE_DIR="$TMP_DIR/cache"
PERPLEXITY_API_KEY="pplx-test-key"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR PERPLEXITY_API_KEY

# shellcheck disable=SC1090
. "$PPLX_SCRIPT_DIR/common.sh"
load_config

BODY="$TMP_DIR/body.json"

field() {
    _F="$1" _K="$2" python3 -c '
import json, os, sys
with open(os.environ["_F"], encoding="utf-8") as fh:
    body = json.load(fh)
value = body
for part in os.environ["_K"].split("."):
    if isinstance(value, dict) and part in value:
        value = value[part]
    else:
        print("<missing>")
        sys.exit(0)
print(json.dumps(value, ensure_ascii=False, sort_keys=True))
'
}

reset_args() {
    queries_reset
    PPLX_ARG_DOMAINS=""
    PPLX_ARG_RECENCY=""
    PPLX_ARG_AFTER=""
    PPLX_ARG_BEFORE=""
    PPLX_ARG_UPDATED_AFTER=""
    PPLX_ARG_UPDATED_BEFORE=""
    PPLX_ARG_CONTEXT_SIZE=""
    PPLX_ARG_MAX_RESULTS=""
    PPLX_ARG_COUNTRY=""
    PPLX_ARG_LANGUAGE=""
}

# --- single query stays a string ---
reset_args
query_add "one question"
build_search_body "$BODY"
[ "$(field "$BODY" query)" = '"one question"' ] || { echo "single query should be a string"; exit 1; }

# --- several queries become an array ---
reset_args
query_add "first"
query_add "second"
query_add "third"
build_search_body "$BODY"
[ "$(field "$BODY" query)" = '["first", "second", "third"]' ] || {
    echo "multi query array wrong: $(field "$BODY" query)"; exit 1
}

# --- one multiline query must stay ONE query ---
# Newlines used to be the in-band delimiter, so a pasted or command-substituted
# question silently became several separate (billed) queries.
reset_args
query_add "$(printf 'как устроен трансформер\nи чем он лучше RNN')"
build_search_body "$BODY"
[ "$(field "$BODY" query)" = '"как устроен трансформер\nи чем он лучше RNN"' ] || {
    echo "multiline query was split: $(field "$BODY" query)"; exit 1
}

# ...and it must not eat into the five-query budget either.
reset_args
query_add "$(printf 'a\nb\nc\nd\ne\nf\ng')"
query_add "second"
build_search_body "$BODY"
[ "$(field "$BODY" query)" = '["a\nb\nc\nd\ne\nf\ng", "second"]' ] || {
    echo "multiline query miscounted: $(field "$BODY" query)"; exit 1
}

# --- filters land where the API expects them ---
reset_args
query_add "q"
PPLX_ARG_MAX_RESULTS="7"
PPLX_ARG_CONTEXT_SIZE="high"
PPLX_ARG_COUNTRY="ru"
PPLX_ARG_LANGUAGE="en,ru"
PPLX_ARG_DOMAINS="nature.com,science.org"
PPLX_ARG_RECENCY="week"
build_search_body "$BODY"
[ "$(field "$BODY" max_results)" = "7" ] || { echo "max_results missing"; exit 1; }
[ "$(field "$BODY" search_context_size)" = '"high"' ] || { echo "context size missing"; exit 1; }
[ "$(field "$BODY" country)" = '"RU"' ] || { echo "country should be upper-cased"; exit 1; }
[ "$(field "$BODY" search_language_filter)" = '["en", "ru"]' ] || { echo "language filter wrong"; exit 1; }
[ "$(field "$BODY" search_domain_filter)" = '["nature.com", "science.org"]' ] || { echo "domain filter wrong"; exit 1; }
[ "$(field "$BODY" search_recency_filter)" = '"week"' ] || { echo "recency missing"; exit 1; }

# --- explicit dates ---
reset_args
query_add "q"
PPLX_ARG_AFTER="01/15/2026"
PPLX_ARG_BEFORE="05/01/2026"
build_search_body "$BODY"
[ "$(field "$BODY" search_after_date_filter)" = '"01/15/2026"' ] || { echo "after date missing"; exit 1; }
[ "$(field "$BODY" search_before_date_filter)" = '"05/01/2026"' ] || { echo "before date missing"; exit 1; }

# --- rejected combinations ---
reset_args
query_add "q"
PPLX_ARG_RECENCY="week"
PPLX_ARG_AFTER="01/15/2026"
if ( build_search_body "$BODY" ) 2>/dev/null; then
    echo "recency + explicit date should be rejected"; exit 1
fi

reset_args
query_add "q"
PPLX_ARG_DOMAINS="nature.com,-reddit.com"
if ( build_search_body "$BODY" ) 2>/dev/null; then
    echo "mixed allowlist/denylist should be rejected"; exit 1
fi

reset_args
query_add "q"
PPLX_ARG_MAX_RESULTS="50"
if ( build_search_body "$BODY" ) 2>/dev/null; then
    echo "max_results above 20 should be rejected"; exit 1
fi

reset_args
for q in a b c d e f; do query_add "$q"; done
if ( build_search_body "$BODY" ) 2>/dev/null; then
    echo "more than 5 queries should be rejected"; exit 1
fi

reset_args
if ( build_search_body "$BODY" ) 2>/dev/null; then
    echo "empty query should be rejected"; exit 1
fi

echo PASS
