#!/bin/sh
# Test that _normalize_response correctly transforms cloud responses to legacy shape.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
FIXTURES="$TESTS_DIR/fixtures"

# Pre-set dir vars so common.sh doesn't try to resolve them via $0
# (POSIX sh has no portable way to get the path to a sourced script)
WORDSTAT_SCRIPT_DIR="$SCRIPTS_DIR"
WORDSTAT_SKILL_DIR="$SKILL_DIR"
export WORDSTAT_SCRIPT_DIR WORDSTAT_SKILL_DIR
# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

assert_eq() {
    _name="$1"; _actual="$2"; _expected="$3"
    if [ "$_actual" = "$_expected" ]; then
        echo "  ok: $_name"
        return 0
    fi
    echo "  FAIL: $_name"
    echo "    actual:   $_actual"
    echo "    expected: $_expected"
    return 1
}

# Compare two JSON strings semantically (key order independent)
json_eq() {
    _a="$1"; _b="$2"
    _A="$_a" _B="$_b" python3 -c "
import json, os, sys
try:
    a = json.loads(os.environ['_A'])
    b = json.loads(os.environ['_B'])
except Exception as e:
    print(f'PARSE: {e}')
    sys.exit(1)
if a == b:
    sys.exit(0)
print(f'NEQ: actual={json.dumps(a, ensure_ascii=False)} expected={json.dumps(b, ensure_ascii=False)}')
sys.exit(1)
"
}

# Args: $1 = method, $2 = optional locale for the python3 that normalizes
run_normalize_test() {
    _method="$1"
    _locale="${2:-}"
    _cloud_file="$FIXTURES/cloud-${_method}-response.json"
    _expected_file="$FIXTURES/legacy-${_method}-expected.json"
    _label="normalize $_method${_locale:+ under $_locale}"

    actual=$([ -z "$_locale" ] || export LC_ALL="$_locale"
        _normalize_response "$_method" < "$_cloud_file")
    expected=$(cat "$_expected_file")

    if json_eq "$actual" "$expected"; then
        echo "  ok: $_label"
    else
        echo "  FAIL: $_label"
        echo "    actual:   $actual"
        echo "    expected: $expected"
        return 1
    fi
}

run_normalize_test topRequests
run_normalize_test dynamics
run_normalize_test regions

# Native Windows Python writes pipes in the ANSI code page: cp1251 garbles
# Cyrillic phrases, cp1252 cannot encode them at all. These locales emulate it
# on macOS; where one is missing, Python falls back to UTF-8.
run_normalize_test topRequests ru_RU.CP1251
run_normalize_test topRequests en_US.ISO8859-1

echo "test_normalize: all passed"
