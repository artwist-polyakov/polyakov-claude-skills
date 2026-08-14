#!/bin/sh
# Response rendering: Search API → TSV/TXT, Agent API → markdown report.
# Uses recorded fixtures, so it runs offline.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_render_test.XXXXXX")
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

# --- Search API response ---
COUNT=$(render_search_response \
    "$TESTS_DIR/fixtures/search_response.json" "$TMP_DIR/out.tsv" "$TMP_DIR/out.txt")
[ "$COUNT" = "3" ] || { echo "expected 3 results, got '$COUNT'"; exit 1; }

head -1 "$TMP_DIR/out.tsv" | grep -q '^n	date	domain	title	url$' || { echo "TSV header wrong"; exit 1; }
[ "$(wc -l < "$TMP_DIR/out.tsv" | tr -d ' ')" = "4" ] || { echo "TSV should have header + 3 rows"; exit 1; }
awk -F'\t' 'NR>1 && NF!=5 {exit 1}' "$TMP_DIR/out.tsv" || { echo "TSV rows must have 5 columns"; exit 1; }

# Tabs and newlines inside titles must not break the table.
grep -q 'Messy	title with tabs' "$TMP_DIR/out.tsv" && { echo "raw tab leaked into TSV"; exit 1; }
grep -q 'example.org' "$TMP_DIR/out.tsv" || { echo "domain column not derived from url"; exit 1; }
grep -q 'full snippet body for the first result' "$TMP_DIR/out.txt" || { echo "snippet missing from txt"; exit 1; }

# Long snippets stay out of the table.
grep -q 'full snippet body' "$TMP_DIR/out.tsv" && { echo "snippet leaked into TSV"; exit 1; }

# --- Agent API response ---
SOURCES=$(render_agent_response \
    "$TESTS_DIR/fixtures/agent_response.json" "$TMP_DIR/answer.md" "what is a transformer")
[ "$SOURCES" = "2" ] || { echo "expected 2 unique sources, got '$SOURCES'"; exit 1; }

head -1 "$TMP_DIR/answer.md" | grep -q '^# what is a transformer$' || { echo "title missing"; exit 1; }
grep -q 'The answer text, with a citation' "$TMP_DIR/answer.md" || { echo "answer text missing"; exit 1; }
grep -q '^## Sources$' "$TMP_DIR/answer.md" || { echo "sources section missing"; exit 1; }
grep -q 'https://example.com/a' "$TMP_DIR/answer.md" || { echo "first source missing"; exit 1; }
grep -qi 'cost: \$0\.00' "$TMP_DIR/answer.md" || { echo "cost line missing"; exit 1; }
# The duplicated URL must be collapsed.
[ "$(grep -c 'example.com/a)' "$TMP_DIR/answer.md")" = "1" ] || { echo "duplicate source not deduped"; exit 1; }

# --- a failed run must not be rendered as a successful answer ---
if ( render_agent_response \
        "$TESTS_DIR/fixtures/agent_failed.json" "$TMP_DIR/failed.md" "boom" ) >/dev/null 2>&1; then
    echo "failed agent run rendered as success"; exit 1
fi

echo PASS
