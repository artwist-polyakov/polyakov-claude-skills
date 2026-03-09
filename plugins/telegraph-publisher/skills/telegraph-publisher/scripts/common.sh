#!/bin/sh
# Common functions for Telegraph Publisher skill
# POSIX sh compatible — no bashisms

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/.env"

TELEGRAPH_API="https://api.telegra.ph"
GITHUB_API="https://api.github.com"

# Ensure tmp directory exists
TPH_TMPDIR="${TMPDIR:-/tmp}"
mkdir -p "$TPH_TMPDIR"

# --------------- Config ---------------

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi

    if [ -z "$TELEGRAPH_ACCESS_TOKEN" ]; then
        echo "Error: TELEGRAPH_ACCESS_TOKEN not found." >&2
        echo "Set in config/.env or environment. See config/README.md." >&2
        exit 1
    fi
}

# load_config_optional — does not fail if token is missing
load_config_optional() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi
}

load_github_config() {
    load_config_optional

    if [ -z "$GITHUB_TOKEN" ]; then
        echo "Error: GITHUB_TOKEN not found." >&2
        echo "Set in config/.env or environment. See config/README.md." >&2
        exit 1
    fi

    if [ -z "$GITHUB_ASSETS_REPO" ]; then
        echo "Error: GITHUB_ASSETS_REPO not found." >&2
        echo "Expected format: owner/repo" >&2
        echo "Set in config/.env or environment. See config/README.md." >&2
        exit 1
    fi

    GITHUB_ASSETS_BRANCH="${GITHUB_ASSETS_BRANCH:-main}"
    GITHUB_ASSETS_BASE_DIR="${GITHUB_ASSETS_BASE_DIR:-pages}"
    GITHUB_MANIFESTS_DIR="${GITHUB_MANIFESTS_DIR:-manifests}"
}

check_python3() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: python3 is required but not found." >&2
        exit 1
    fi
}

check_curl() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required but not found." >&2
        exit 1
    fi
}

check_prerequisites() {
    check_python3
    check_curl
}

make_secure_tmpdir() {
    _old_umask=$(umask)
    umask 077
    _tmpdir=$(mktemp -d "${TPH_TMPDIR}/tph_XXXXXX")
    umask "$_old_umask"
    echo "$_tmpdir"
}

slugify_filename() {
    python3 - "$1" <<'PY'
import os, re, sys

name = os.path.basename(sys.argv[1])
base, ext = os.path.splitext(name)
base = re.sub(r'[^A-Za-z0-9._-]+', '-', base.strip().lower()).strip('-')
if not base:
    base = 'asset'
print(base + ext.lower())
PY
}

# --------------- API helpers ---------------

# telegraph_post <method> [curl_args...]
# Makes POST request to Telegraph API. Returns body.
telegraph_post() {
    _tp_method="$1"
    shift
    _tp_url="${TELEGRAPH_API}/${_tp_method}"
    _tp_tmpfile="${TPH_TMPDIR}/telegraph_response_$$.json"
    trap 'rm -f "$_tp_tmpfile"' EXIT

    curl -s -X POST "$@" "$_tp_url" > "$_tp_tmpfile" || {
        echo "Error: curl failed for $_tp_url" >&2
        return 1
    }

    # Check API-level error
    _tp_ok=$(json_extract_bool "$_tp_tmpfile" "ok")
    if [ "$_tp_ok" = "false" ]; then
        _tp_error=$(json_extract_field "$_tp_tmpfile" "error")
        echo "Error: Telegraph API: $_tp_error" >&2
        cat "$_tp_tmpfile" >&2
        return 1
    fi

    cat "$_tp_tmpfile"
}

# --------------- JSON helpers (flat fields only) ---------------

# json_extract_field <file> <field_name> — extracts string value
json_extract_field() {
    grep -o "\"$2\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$1" | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//'
}

# json_extract_number <file> <field_name> — extracts numeric value
json_extract_number() {
    grep -o "\"$2\"[[:space:]]*:[[:space:]]*[0-9]*" "$1" | head -1 | sed 's/.*:[[:space:]]*//'
}

# json_extract_bool <file> <field_name> — extracts true/false
json_extract_bool() {
    grep -o "\"$2\"[[:space:]]*:[[:space:]]*[a-z]*" "$1" | head -1 | sed 's/.*:[[:space:]]*//'
}
