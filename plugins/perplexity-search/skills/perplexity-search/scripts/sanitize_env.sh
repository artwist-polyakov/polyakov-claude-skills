#!/bin/sh
# Quote unquoted .env values that contain spaces, before any shell-style loading.
#
# The value group must end in a non-space, non-'#' character, so a trailing
# comment stays outside the quotes: `K=Tech news # note` becomes
# `K="Tech news" # note`, never `K="Tech news "# note` — the latter is not a
# valid assignment any more and would send the rest of the line to the shell.

set -e

ENV_FILE="${1:-config/.env}"

if [ ! -f "$ENV_FILE" ]; then
    exit 0
fi

SED_SKIP_COMMENT='/^[[:space:]]*#/b'
SED_SKIP_EMPTY='/^[[:space:]]*$/b'
SED_QUOTE_VALUE='s/^([[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*)([^"'\''#][^#]*[[:space:]][^#]*[^#[:space:]])([[:space:]]*(#.*)?)$/\1"\3"\4/'

if sed --version >/dev/null 2>&1; then
    sed -i -E -e "$SED_SKIP_COMMENT" -e "$SED_SKIP_EMPTY" -e "$SED_QUOTE_VALUE" "$ENV_FILE"
else
    sed -i '' -E -e "$SED_SKIP_COMMENT" -e "$SED_SKIP_EMPTY" -e "$SED_QUOTE_VALUE" "$ENV_FILE"
fi
