#!/bin/sh
# research.sh against a loopback mock: an identical query must never pay for a
# second background run while the first one is still in flight.
#
# Skips itself (exit 0) where a sandbox forbids binding a local socket.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
SCRIPTS="$SKILL_DIR/scripts"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_research_test.XXXXXX")
SERVER_PID=""
cleanup() {
    [ -z "$SERVER_PID" ] || kill "$SERVER_PID" 2>/dev/null || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

cat > "$TMP_DIR/mock.py" <<'PY'
import http.server, json, os, sys, time

PORT_FILE, COUNT_FILE, MODE_FILE = sys.argv[1], sys.argv[2], sys.argv[3]
RESPONSE_ID = "resp_mocked1"


def mode():
    """ok | stale | flaky — how GET /v1/agent/<id> should answer."""
    try:
        with open(MODE_FILE, encoding="utf-8") as fh:
            return fh.read().strip() or "ok"
    except OSError:
        return "ok"


class Handler(http.server.BaseHTTPRequestHandler):
    submissions = 0

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Handler.submissions += 1
        with open(COUNT_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(Handler.submissions))
        # Every submission starts a fresh background run.
        self._send({"id": f"{RESPONSE_ID}_{Handler.submissions}", "status": "queued"})

    def do_GET(self):
        current = mode()
        if current == "flaky":
            # A transient server-side failure: the run may well still be alive.
            self._send({"error": "upstream unavailable"}, code=503)
            return
        # "stale" reports a run that finished long ago.
        created = int(time.time()) - (10 * 86400 if current == "stale" else 5)
        self._send({
            "id": self.path.rsplit("/", 1)[-1],
            "object": "response",
            "created_at": created,
            "status": "completed",
            "model": "openai/gpt-5.6-sol",
            "output": [
                {"type": "search_results", "queries": ["q"], "results": [
                    {"id": 1, "title": "Src", "url": "https://example.com/x",
                     "snippet": "s", "date": "2026-01-01", "source": "web"}
                ]},
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "The finished report."}]},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        })

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
COUNT_FILE="$TMP_DIR/submissions"
MODE_FILE="$TMP_DIR/mode"
echo ok > "$MODE_FILE"
python3 "$TMP_DIR/mock.py" "$PORT_FILE" "$COUNT_FILE" "$MODE_FILE" >/dev/null 2>&1 &
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
PERPLEXITY_API_KEY="pplx-test-key"
PPLX_API_BASE="http://127.0.0.1:$(cat "$PORT_FILE")"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR PERPLEXITY_API_KEY PPLX_API_BASE

QUERY="рынок X: игроки и барьеры"

submissions() {
    cat "$COUNT_FILE" 2>/dev/null || echo 0
}

# 1. Submit and walk away.
sh "$SCRIPTS/research.sh" --query "$QUERY" --no-wait >/dev/null 2>&1
[ "$(submissions)" = "1" ] || { echo "expected 1 submission, got $(submissions)"; exit 1; }

ID_FILE=$(find "$PPLX_CACHE_DIR/research" -name '*.id' | head -1)
[ -n "$ID_FILE" ] || { echo "--no-wait did not record the response id"; exit 1; }
FIRST_ID=$(cat "$ID_FILE")

# 2. The same query again must adopt the in-flight run, not start another.
OUT=$(sh "$SCRIPTS/research.sh" --query "$QUERY" --poll 1 --timeout 20 2>&1)
[ "$(submissions)" = "1" ] || {
    echo "an identical query started a second billable run ($(submissions) submissions)"
    exit 1
}
case "$OUT" in
    *"The finished report."*) : ;;
    *) echo "the adopted run did not produce the report:"; printf '%s\n' "$OUT"; exit 1 ;;
esac
[ "$(cat "$ID_FILE")" = "$FIRST_ID" ] || { echo "the original run's id was overwritten"; exit 1; }

# 3. A completed run is then served from cache — still no new submission.
sh "$SCRIPTS/research.sh" --query "$QUERY" --poll 1 --timeout 20 >/dev/null 2>&1
[ "$(submissions)" = "1" ] || { echo "cache hit still submitted a run"; exit 1; }

# 4. --no-cache is the explicit way to pay for a fresh run.
sh "$SCRIPTS/research.sh" --query "$QUERY" --no-cache --poll 1 --timeout 20 >/dev/null 2>&1
[ "$(submissions)" = "2" ] || { echo "--no-cache did not start a new run"; exit 1; }

# Age the cached report so the cache check misses and the probe path is reached.
age_cached_report() {
    find "$PPLX_CACHE_DIR/research" -name '*.json' -exec python3 -c '
import os, sys, time
old = time.time() - 3600
for path in sys.argv[1:]:
    os.utime(path, (old, old))
' {} +
}

# 5. An inconclusive probe must not be read as "the run is gone".
# A 5xx says nothing about whether the expensive run is still alive, so
# submitting another one on that basis can double the bill.
echo flaky > "$MODE_FILE"
age_cached_report
BEFORE=$(submissions)
if sh "$SCRIPTS/research.sh" --query "$QUERY" --cache-ttl 60 --poll 1 --timeout 5 >/dev/null 2>&1; then
    echo "an inconclusive probe should not report success"
    exit 1
fi
[ "$(submissions)" = "$BEFORE" ] || {
    echo "an inconclusive probe led to a second billable run ($(submissions) vs $BEFORE)"
    exit 1
}

# 6. A completed run past its TTL is not adopted — otherwise the probe would
# keep re-blessing an expired report and nothing would ever expire.
echo stale > "$MODE_FILE"
age_cached_report
BEFORE=$(submissions)
sh "$SCRIPTS/research.sh" --query "$QUERY" --cache-ttl 60 --poll 1 --timeout 20 >/dev/null 2>&1
[ "$(submissions)" -gt "$BEFORE" ] || {
    echo "a report older than the TTL was adopted instead of starting a fresh run"
    exit 1
}
echo ok > "$MODE_FILE"

echo PASS
