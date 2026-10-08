#!/bin/sh
# Test that _xlate_request correctly transforms legacy params to cloud body.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
FIXTURES="$TESTS_DIR/fixtures"

WORDSTAT_SCRIPT_DIR="$SCRIPTS_DIR"
WORDSTAT_SKILL_DIR="$SKILL_DIR"
export WORDSTAT_SCRIPT_DIR WORDSTAT_SKILL_DIR
# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

# Set folder id for translation injection
WORDSTAT_CLOUD_FOLDER_ID="b1g-test-folder"

json_eq() {
    _a="$1"; _b="$2"
    _A="$_a" _B="$_b" python3 -c "
import json, os, sys
a = json.loads(os.environ['_A'])
b = json.loads(os.environ['_B'])
if a == b:
    sys.exit(0)
print(f'NEQ: actual={json.dumps(a, ensure_ascii=False)} expected={json.dumps(b, ensure_ascii=False)}')
sys.exit(1)
"
}

run_translate_test() {
    _method="$1"
    _params=$(cat "$FIXTURES/legacy-${_method}-params.json")
    _expected=$(cat "$FIXTURES/cloud-${_method}-request-expected.json")

    actual=$(_xlate_request "$_method" "$_params")
    if json_eq "$actual" "$_expected"; then
        echo "  ok: translate $_method"
    else
        echo "  FAIL: translate $_method"
        echo "    actual:   $actual"
        echo "    expected: $_expected"
        return 1
    fi
}

run_translate_test topRequests
run_translate_test dynamics
run_translate_test regions

# Defaults apply to every caller, while explicit limits remain unchanged.
for limit in default 1 500 2000; do
    params='{"phrase":"тест"}'
    expected_limit=50
    if [ "$limit" != default ]; then
        params="{\"phrase\":\"тест\",\"numPhrases\":$limit}"
        expected_limit="$limit"
    fi
    actual=$(_xlate_request topRequests "$params")
    json_eq "$actual" "{\"phrase\":\"тест\",\"numPhrases\":\"$expected_limit\",\"folderId\":\"b1g-test-folder\"}"
    echo "  ok: topRequests limit $limit"
done

# A Windows output encoding must preserve Unicode and JSON escaping.
for method in topRequests dynamics regions; do
    actual=$(PYTHONIOENCODING=cp1251 _xlate_request "$method" \
        '{"phrase":"Ёж \"кафе\" \\ / ☕","period":"daily","fromDate":"2025-01-01"}')
    ACTUAL="$actual" python3 - <<'PY'
import json, os

body = os.environ["ACTUAL"]
assert body.isascii(), "request body must survive Windows argument encoding"
assert json.loads(body.encode("cp1251").decode("cp1251"))["phrase"] == 'Ёж "кафе" \\ / ☕'
PY
    echo "  ok: $method preserves Unicode through cp1251"
done

echo "test_translate: all passed"
