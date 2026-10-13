#!/bin/sh
# search_region.sh: case-insensitive Cyrillic match, and a quote in the name
# must not break the Python code that searches.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SEARCH_REGION="$TESTS_DIR/../search_region.sh"
# shellcheck disable=SC1091
. "$TESTS_DIR/helpers.sh"

sh "$SEARCH_REGION" --name "москва" | grep -Fq '| 213 | Москва |' || {
    echo 'FAIL: lowercase Cyrillic name did not find Москва'
    exit 1
}

# Native Windows Python writes pipes in the ANSI code page; the ru_RU.CP1251
# locale emulates it on macOS and Linux. Git Bash needs no emulation: the check
# above already runs under the real code page (see windows_shell).
if ! windows_shell; then
    (export LC_ALL=ru_RU.CP1251; sh "$SEARCH_REGION" --name "москва") \
        | grep -Fq '| 213 | Москва |' || {
        echo 'FAIL: region names garbled under a Windows code page'
        exit 1
    }
fi

out=$(sh "$SEARCH_REGION" --name "O'Brien")
printf '%s\n' "$out" | grep -Fq "No regions found matching \"O'Brien\"" || {
    echo 'FAIL: a quote in the name broke the search'
    exit 1
}

echo PASS
