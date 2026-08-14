#!/bin/sh
# normalize_date and the small validators.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_dates_test.XXXXXX")
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

check() {
    _got=$(normalize_date "$1")
    [ "$_got" = "$2" ] || { echo "normalize_date '$1' → '$_got', expected '$2'"; exit 1; }
}

check "2026-05-01" "05/01/2026"
check "2026-5-1"   "05/01/2026"
check "05/01/2026" "05/01/2026"
check "5/1/2026"   "05/01/2026"
check ""           ""

for bad in "2026" "01-05-2026" "May 1 2026" "2026/05/01" "not-a-date"; do
    if ( normalize_date "$bad" ) >/dev/null 2>&1; then
        echo "normalize_date accepted '$bad'"; exit 1
    fi
done

for good in hour day week month year; do
    validate_recency "$good" || { echo "validate_recency rejected '$good'"; exit 1; }
done
for bad in decade "" 1h; do
    [ -z "$bad" ] && continue
    if ( validate_recency "$bad" ) >/dev/null 2>&1; then
        echo "validate_recency accepted '$bad'"; exit 1
    fi
done

for good in low medium high; do
    validate_context_size "$good" || { echo "validate_context_size rejected '$good'"; exit 1; }
done
if ( validate_context_size "huge" ) >/dev/null 2>&1; then
    echo "validate_context_size accepted 'huge'"; exit 1
fi

require_uint "--n" 5 1 10 || { echo "require_uint rejected a valid value"; exit 1; }
for bad_call in "abc" "-1" "3.5"; do
    if ( require_uint "--n" "$bad_call" ) >/dev/null 2>&1; then
        echo "require_uint accepted '$bad_call'"; exit 1
    fi
done
if ( require_uint "--n" 11 1 10 ) >/dev/null 2>&1; then
    echo "require_uint ignored the max bound"; exit 1
fi

# Which existing background run is worth reusing instead of paying again.
for s in queued in_progress completed; do
    adoptable_status "$s" || { echo "adoptable_status rejected '$s'"; exit 1; }
done
for s in failed cancelled incomplete unknown ""; do
    if adoptable_status "$s"; then
        echo "adoptable_status accepted '$s' — a fresh run is the right move there"
        exit 1
    fi
done

validate_response_id "resp_abc-123_X" || { echo "valid response id rejected"; exit 1; }
for bad in "" "resp/../x" 'a;b' "a b"; do
    if ( validate_response_id "$bad" ) >/dev/null 2>&1; then
        echo "validate_response_id accepted '$bad'"; exit 1
    fi
done

echo PASS
