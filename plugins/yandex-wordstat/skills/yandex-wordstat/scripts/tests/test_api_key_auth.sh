#!/bin/sh
# Verify AI Studio API-key selection and request authorization without network.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"

td="${TMPDIR:-/tmp}/wordstat_api_key_test_$$"
rm -rf "$td"
mkdir -p "$td/config" "$td/cache" "$td/bin"
trap 'rm -rf "$td"' EXIT INT TERM

cat > "$td/config/config.json" <<'EOF'
{"yandex_cloud_folder_id":"b1g-api-key-test","auth":{"mode":"api_key"}}
EOF

cat > "$td/bin/curl" <<'EOF'
#!/bin/sh
capture="${FAKE_CAPTURE:?}"
response="${FAKE_RESPONSE:?}"
out=""

printf '%s\n' "$@" > "$capture"
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            out="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

cp "$response" "$out"
printf '200'
EOF
chmod +x "$td/bin/curl"

WORDSTAT_SCRIPT_DIR="$SCRIPTS_DIR"
WORDSTAT_SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
WORDSTAT_CONFIG_DIR="$td/config"
WORDSTAT_CACHE_DIR="$td/cache"
YANDEX_AI_API_KEY="test-api-secret"
FAKE_CAPTURE="$td/curl-args"
FAKE_RESPONSE="$TESTS_DIR/fixtures/cloud-topRequests-response.json"
PATH="$td/bin:$PATH"
export WORDSTAT_SCRIPT_DIR WORDSTAT_SKILL_DIR WORDSTAT_CONFIG_DIR
export WORDSTAT_CACHE_DIR YANDEX_AI_API_KEY FAKE_CAPTURE FAKE_RESPONSE PATH

# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

load_config

[ "$WORDSTAT_BACKEND" = "cloud" ] || {
    echo "FAIL: expected cloud backend, got '$WORDSTAT_BACKEND'"
    exit 1
}
[ "$WORDSTAT_CLOUD_AUTH_MODE" = "api_key" ] || {
    echo "FAIL: expected api_key auth mode, got '$WORDSTAT_CLOUD_AUTH_MODE'"
    exit 1
}

actual=$(wordstat_request "topRequests" '{"phrase":"юрист дтп"}')
expected=$(cat "$TESTS_DIR/fixtures/legacy-topRequests-expected.json")

ACTUAL="$actual" EXPECTED="$expected" python3 - <<'PY'
import json
import os

assert json.loads(os.environ["ACTUAL"]) == json.loads(os.environ["EXPECTED"])
PY

grep -Fxq 'Authorization: Api-Key test-api-secret' "$FAKE_CAPTURE" || {
    echo "FAIL: Api-Key authorization header was not sent"
    exit 1
}
if grep -Fq 'Authorization: Bearer' "$FAKE_CAPTURE"; then
    echo "FAIL: IAM Bearer header leaked into API-key mode"
    exit 1
fi

echo "test_api_key_auth: all passed"
