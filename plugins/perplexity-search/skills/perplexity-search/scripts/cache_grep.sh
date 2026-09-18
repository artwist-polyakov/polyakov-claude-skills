#!/bin/sh
# cache_grep.sh — search the local result cache instead of re-querying the API.
# Greps the rendered text (.tsv/.txt/.md), never the raw JSON, so hits are
# readable. Output is capped: use the printed file:line to Read the exact spot.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: sh scripts/cache_grep.sh PATTERN [options]

  PATTERN                extended regex (grep -E), case-insensitive
  --type NAME            limit to search | ask | research | fetch
  --context N            lines of context around each hit (default 0)
  --max N                max output lines (default 60)
  --files                list matching files only, with hit counts
EOF
}

PATTERN=""
TYPE=""
CONTEXT="0"
MAX="60"
FILES_ONLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --type)    require_value "$1" $#; TYPE="$2"; shift 2 ;;
        --context) require_value "$1" $#; CONTEXT="$2"; shift 2 ;;
        --max)     require_value "$1" $#; MAX="$2"; shift 2 ;;
        --files)   FILES_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        -*)        usage >&2; die "Unknown option: $1" ;;
        *)
            [ -z "$PATTERN" ] || { usage >&2; die "Only one PATTERN is supported (got '$1' after '$PATTERN')"; }
            PATTERN="$1"; shift ;;
    esac
done

if [ -z "$PATTERN" ]; then
    usage >&2
    die "PATTERN is required"
fi
require_uint "--context" "$CONTEXT" 0 20
require_uint "--max" "$MAX" 1

case "$TYPE" in
    '') SEARCH_ROOT="$PPLX_CACHE_DIR" ;;
    search|ask|research|fetch) SEARCH_ROOT="$PPLX_CACHE_DIR/$TYPE" ;;
    *) die "Unknown --type '$TYPE'" "Allowed: search, ask, research, fetch." ;;
esac

if [ ! -d "$SEARCH_ROOT" ]; then
    echo "No cached results yet under $SEARCH_ROOT"
    exit 0
fi

# `find -exec ... +` rather than xargs: cache paths may contain spaces.
# -H keeps the filename even when the last batch holds a single file.
# grep exits 1 on "no match", which is not an error here.
if [ -n "$FILES_ONLY" ]; then
    HITS=$(find "$SEARCH_ROOT" -type f \( -name '*.tsv' -o -name '*.txt' -o -name '*.md' \) \
        -exec grep -H -c -i -E -e "$PATTERN" {} + 2>/dev/null | grep -v ':0$' || true)
elif [ "$CONTEXT" -gt 0 ]; then
    HITS=$(find "$SEARCH_ROOT" -type f \( -name '*.tsv' -o -name '*.txt' -o -name '*.md' \) \
        -exec grep -H -n -i -E -C "$CONTEXT" -e "$PATTERN" {} + 2>/dev/null || true)
else
    HITS=$(find "$SEARCH_ROOT" -type f \( -name '*.tsv' -o -name '*.txt' -o -name '*.md' \) \
        -exec grep -H -n -i -E -e "$PATTERN" {} + 2>/dev/null || true)
fi

if [ -z "$HITS" ]; then
    echo "No matches for '$PATTERN' in $SEARCH_ROOT"
    exit 0
fi

printf '%s\n' "$HITS" | head -n "$MAX"
TOTAL=$(printf '%s\n' "$HITS" | wc -l | tr -d ' ')
if [ "$TOTAL" -gt "$MAX" ]; then
    echo "... $((TOTAL - MAX)) more matching lines — narrow the pattern or raise --max"
fi
