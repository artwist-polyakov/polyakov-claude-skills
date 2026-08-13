#!/bin/sh
# HTTP layer against a loopback mock: auth header delivery, UTF-8 body,
# Retry-After handling on 429, and no-retry on 401.
#
# Skips itself (exit 0) where a sandbox forbids binding a local socket.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_http_test.XXXXXX")
SERVER_PID=""
cleanup() {
    [ -z "$SERVER_PID" ] || kill "$SERVER_PID" 2>/dev/null || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

cat > "$TMP_DIR/mock.py" <<'PY'
import http.server, json, sys

PORT_FILE = sys.argv[1]


class Handler(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/unauthorized":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"bad key"}')
            return
        Handler.hits += 1
        if Handler.hits == 1:
            # First call is throttled; the client must honour Retry-After.
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return
        payload = json.dumps({
            "auth": self.headers.get("Authorization", ""),
            "content_type": self.headers.get("Content-Type", ""),
            "echo": json.loads(body),
            "hits": Handler.hits,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/unauthorized":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"bad key"}')
            return
        payload = b'{"status":"completed"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


try:
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
except OSError:
    sys.exit(3)
with open(PORT_FILE, "w", encoding="utf-8") as fh:
    fh.write(str(server.server_address[1]))
server.serve_forever()
PY

PORT_FILE="$TMP_DIR/port"
python3 "$TMP_DIR/mock.py" "$PORT_FILE" >/dev/null 2>&1 &
SERVER_PID=$!

WAITED=0
while [ ! -s "$PORT_FILE" ]; do
    if [ "$WAITED" -ge 20 ] || ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "cannot bind a loopback socket in this environment" >&2
        echo SKIP
        exit 0
    fi
    sleep 0.25 2>/dev/null || sleep 1
    WAITED=$((WAITED + 1))
done

PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
PPLX_CACHE_DIR="$TMP_DIR/cache"
PERPLEXITY_API_KEY="pplx-secret-key"
PPLX_API_BASE="http://127.0.0.1:$(cat "$PORT_FILE")"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR PERPLEXITY_API_KEY PPLX_API_BASE

# shellcheck disable=SC1090
. "$PPLX_SCRIPT_DIR/common.sh"
load_config

printf '{"probe":true,"unicode":"привет"}' > "$TMP_DIR/body.json"
pplx_post "/ok" "$TMP_DIR/body.json" "$TMP_DIR/out.json" 2>/dev/null

_F="$TMP_DIR/out.json" python3 - <<'PY'
import json, os, sys

with open(os.environ["_F"], encoding="utf-8") as fh:
    data = json.load(fh)

problems = []
if data["auth"] != "Bearer pplx-secret-key":
    problems.append(f"auth header not delivered: {data['auth']!r}")
if data["content_type"] != "application/json":
    problems.append(f"content type wrong: {data['content_type']!r}")
if data["echo"] != {"probe": True, "unicode": "привет"}:
    problems.append(f"body corrupted: {data['echo']!r}")
if data["hits"] != 2:
    problems.append(f"429 was not retried (hits={data['hits']})")

if problems:
    print("; ".join(problems), file=sys.stderr)
    sys.exit(1)
PY

# 401 must fail immediately rather than burning retries.
if ( pplx_post "/unauthorized" "$TMP_DIR/body.json" "$TMP_DIR/out401.json" ) >/dev/null 2>&1; then
    echo "401 should abort the request"
    exit 1
fi

# --- a failed request must not poison the cache path ---
# Without this, the error body sits at the cache path and the next identical
# call inside the TTL reads it back as a legitimate empty result.
[ ! -e "$TMP_DIR/out401.json" ] || {
    echo "error body was written to the cache path"
    exit 1
}

# ...and it must not destroy an answer that is already cached there.
GOOD="$TMP_DIR/good.json"
printf '{"results":[{"title":"cached"}]}' > "$GOOD"
if ( pplx_post "/unauthorized" "$TMP_DIR/body.json" "$GOOD" ) >/dev/null 2>&1; then
    echo "401 should abort the request"
    exit 1
fi
[ "$(cat "$GOOD")" = '{"results":[{"title":"cached"}]}' ] || {
    echo "a failed refresh clobbered the cached response"
    exit 1
}

# pplx_probe_get reports the HTTP status instead of aborting, so a caller can
# tell "definitely gone" from "could not tell" before spending money.
PROBE_STATUS=$(pplx_probe_get "/unauthorized" "$TMP_DIR/probe.json")
[ "$PROBE_STATUS" = "401" ] || { echo "probe reported '$PROBE_STATUS', expected 401"; exit 1; }
[ ! -e "$TMP_DIR/probe.json" ] || { echo "probe wrote an error body to its output file"; exit 1; }

PROBE_STATUS=$(pplx_probe_get "/ok" "$TMP_DIR/probe_ok.json")
[ "$PROBE_STATUS" = "200" ] || { echo "probe reported '$PROBE_STATUS', expected 200"; exit 1; }
[ -s "$TMP_DIR/probe_ok.json" ] || { echo "probe did not save a successful response"; exit 1; }

# No scratch files left behind either.
if find "$TMP_DIR" -name '*.part.*' | grep -q .; then
    echo "temporary .part files survived a failed request"
    exit 1
fi

# An empty request body is an internal error, not a request.
: > "$TMP_DIR/empty.json"
if ( pplx_post "/ok" "$TMP_DIR/empty.json" "$TMP_DIR/out2.json" ) >/dev/null 2>&1; then
    echo "empty body should be refused"
    exit 1
fi

echo PASS
