#!/bin/sh
# cache_key determinism, cache_fresh TTL handling, index_append / find_latest.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_cache_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
PPLX_CACHE_DIR="$TMP_DIR/cache"
PERPLEXITY_API_KEY="pplx-test-key"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR PERPLEXITY_API_KEY

# shellcheck disable=SC1090
. "$PPLX_SCRIPT_DIR/common.sh"
load_config

# --- cache_key ---
A=$(cache_key "same input")
B=$(cache_key "same input")
C=$(cache_key "other input")
[ "$A" = "$B" ] || { echo "cache_key not deterministic"; exit 1; }
[ "$A" != "$C" ] || { echo "cache_key collides on different input"; exit 1; }
case "$A" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) : ;;
    *) echo "cache_key is not 8 hex chars: $A"; exit 1 ;;
esac

# --- cache_fresh ---
FRESH="$TMP_DIR/fresh.json"
echo '{}' > "$FRESH"

cache_fresh "$FRESH" 3600 || { echo "fresh file reported stale"; exit 1; }

if cache_fresh "$FRESH" 0; then
    echo "TTL 0 should disable the cache"; exit 1
fi
if cache_fresh "$TMP_DIR/absent.json" 3600; then
    echo "missing file reported fresh"; exit 1
fi

: > "$TMP_DIR/empty.json"
if cache_fresh "$TMP_DIR/empty.json" 3600; then
    echo "empty file reported fresh"; exit 1
fi

# Backdate the file by an hour — it must now read as stale for a 60s TTL.
python3 -c 'import os,sys,time; p=sys.argv[1]; os.utime(p, (time.time()-3600, time.time()-3600))' "$FRESH"
if cache_fresh "$FRESH" 60; then
    echo "stale file reported fresh"; exit 1
fi

# A non-numeric TTL must not crash; it disables the cache.
if cache_fresh "$FRESH" "abc"; then
    echo "non-numeric TTL should disable the cache"; exit 1
fi

# --- index_append + find_latest ---
touch "$TMP_DIR/artifact.json"
index_append "search" "deadbeef" "query with	tab and  spaces" "$TMP_DIR/artifact.json"
index_append "ask" "cafe0000" "another question" "$TMP_DIR/artifact.json"

LINES=$(wc -l < "$PPLX_CACHE_DIR/index.tsv" | tr -d ' ')
[ "$LINES" = "2" ] || { echo "index should hold 2 rows, has $LINES"; exit 1; }
FIELDS=$(head -1 "$PPLX_CACHE_DIR/index.tsv" | awk -F'\t' '{print NF}')
[ "$FIELDS" = "5" ] || { echo "index row should have 5 columns, has $FIELDS"; exit 1; }

OUT=$(sh "$PPLX_SCRIPT_DIR/find_latest.sh" --script ask)
case "$OUT" in
    *another\ question*) : ;;
    *) echo "find_latest did not return the ask run: $OUT"; exit 1 ;;
esac
case "$OUT" in
    *deadbeef*) echo "find_latest ignored the --script filter"; exit 1 ;;
esac

PATH_OUT=$(sh "$PPLX_SCRIPT_DIR/find_latest.sh" --script search --path)
[ "$PATH_OUT" = "$TMP_DIR/artifact.json" ] || { echo "--path returned '$PATH_OUT'"; exit 1; }

if sh "$PPLX_SCRIPT_DIR/find_latest.sh" --script fetch --path >/dev/null 2>&1; then
    echo "--path should exit 1 when nothing matches"; exit 1
fi

echo PASS
