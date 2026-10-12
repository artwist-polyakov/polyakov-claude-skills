#!/bin/sh
# search_region.sh: case-insensitive Cyrillic match, and a quote in the name
# must not break the Python code that searches.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SEARCH_REGION="$TESTS_DIR/../search_region.sh"

sh "$SEARCH_REGION" --name "москва" | grep -Fq '| 213 | Москва |' || {
    echo 'FAIL: lowercase Cyrillic name did not find Москва'
    exit 1
}

out=$(sh "$SEARCH_REGION" --name "O'Brien")
printf '%s\n' "$out" | grep -Fq "No regions found matching \"O'Brien\"" || {
    echo 'FAIL: a quote in the name broke the search'
    exit 1
}

echo PASS
