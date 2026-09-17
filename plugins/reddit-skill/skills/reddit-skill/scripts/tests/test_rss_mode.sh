#!/bin/sh
# Exercise public commands with an isolated config/cache and a fake curl.
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"
TEST_PATH="$PATH"
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/reddit-test-rss.XXXXXX")
trap 'rm -rf "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/bin"

cat > "$TEST_ROOT/bin/curl" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith("https://"))
kind = ("TOKEN" if "/access_token" in url else "RSS" if url.endswith(".rss") else
        "JSON" if "www.reddit.com/comments/" in url else "API")
log = Path(os.environ["FAKE_LOG"])
calls = [json.loads(line) for line in log.read_text().splitlines()]
index = sum(call["kind"] == kind for call in calls)
with log.open("a") as stream:
    stream.write(json.dumps({"kind": kind, "url": url, "args": args}) + "\n")
statuses = os.environ["FAKE_" + kind + "_STATUS"].split(",")
status = statuses[min(index, len(statuses) - 1)]
if status == "network":
    sys.exit(7)
if kind == "TOKEN":
    error = os.environ.get("FAKE_TOKEN_ERROR")
    body = json.dumps({"error": error} if error else {
        "access_token": "test_token", "expires_in": 3600, "scope": "*"})
elif kind in {"API", "JSON"}:
    public = kind == "JSON"
    listing = {"kind": "Listing", "data": {"children": [{"kind": "t3", "data": {
        "id": "abc123", "title": "Public snapshot" if public else "API snapshot",
        "subreddit": "test", "author": "alice", "selftext": "Full post text",
        "permalink": "/r/test/comments/abc123/test/",
        "score": 84 if public else 42, "num_comments": 11 if public else 7}}]}}
    comments = {"kind": "Listing", "data": {"children": [
        {"kind": "t1", "data": {"id": "comment1", "body": "A public comment", "score": 3,
                                 "author": "bob", "replies": ""}},
        {"kind": "more", "data": {"count": 10, "children": ["comment2"]}}]}}
    body = json.dumps([listing, comments] if "/comments/" in url else listing)
    if public and os.environ.get("FAKE_JSON_MALFORMED") == "1":
        body = '{"kind": "Listing", "data": {"children": []}}'
else:
    body = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>t3_abc123</id><title>RSS snapshot</title>
    <link href="https://www.reddit.com/r/test/comments/abc123/test/"/>
    <updated>2026-09-17T08:00:00Z</updated><category term="test"/>
    <author><name>/u/alice</name></author><content type="html">&lt;p&gt;Text&lt;/p&gt;</content>
    </entry></feed>'''
    if os.environ.get("FAKE_RSS_MALFORMED") == "1":
        body = "<html>not a feed</html>"
if status != "200":
    body = '{"error": "request rejected"}'
Path(args[args.index("-o") + 1]).write_text(body)
Path(args[args.index("-D") + 1]).write_text("HTTP/1.1 " + status + "\r\n\r\n")
sys.stdout.write(status)
PY
chmod +x "$TEST_ROOT/bin/curl"

fail() {
    echo "FAIL: $*" >&2
    cat "$TEST_ROOT/stdout" "$TEST_ROOT/stderr" >&2
    exit 1
}

reset_case() {
    rm -rf "$TEST_ROOT/config" "$TEST_ROOT/cache" "$TEST_ROOT/tmp"
    mkdir -p "$TEST_ROOT/config" "$TEST_ROOT/cache" "$TEST_ROOT/tmp"
    : > "$TEST_ROOT/requests.jsonl"
    : > "$TEST_ROOT/stdout"
    : > "$TEST_ROOT/stderr"
    RSS_MODE=0
    TOKEN_STATUS=200
    TOKEN_ERROR=""
    API_STATUS=200
    RSS_STATUS=200
    RSS_MALFORMED=0
    JSON_STATUS=200
    JSON_MALFORMED=0
}

api_config() {
    cat > "$TEST_ROOT/config/.env" <<'EOF'
REDDIT_CLIENT_ID=test_id
REDDIT_CLIENT_SECRET=test_secret
REDDIT_USER_AGENT=test-agent
EOF
}

run_script() {
    script="$1"; shift
    env -i PATH="$TEST_ROOT/bin:$TEST_PATH" TMPDIR="$TEST_ROOT/tmp" \
        REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" REDDIT_SKILL_DIR="$TEST_ROOT" \
        REDDIT_CONFIG_DIR="$TEST_ROOT/config" REDDIT_CACHE_DIR="$TEST_ROOT/cache" \
        REDDIT_RSS_MODE="$RSS_MODE" FAKE_LOG="$TEST_ROOT/requests.jsonl" \
        FAKE_TOKEN_STATUS="$TOKEN_STATUS" FAKE_TOKEN_ERROR="$TOKEN_ERROR" \
        FAKE_API_STATUS="$API_STATUS" FAKE_RSS_STATUS="$RSS_STATUS" \
        FAKE_RSS_MALFORMED="$RSS_MALFORMED" FAKE_JSON_STATUS="$JSON_STATUS" \
        FAKE_JSON_MALFORMED="$JSON_MALFORMED" \
        sh "$SCRIPTS_DIR/$script" "$@" > "$TEST_ROOT/stdout" 2> "$TEST_ROOT/stderr"
}

run_ok() { run_script "$@" || fail "$* should succeed"; }
run_fail() {
    if run_script "$@"; then fail "$* should fail"; fi
}

# Public RSS and JSON requests must never receive OAuth credentials.
assert_calls() {
    python3 - "$TEST_ROOT/requests.jsonl" "$@" <<'PY'
import json
import sys
calls = [json.loads(line) for line in open(sys.argv[1])]
actual = [call["kind"] for call in calls]
assert actual == sys.argv[2:], (actual, sys.argv[2:])
for call in calls:
    if call["kind"] in {"RSS", "JSON"}:
        args = call["args"]
        assert "-u" not in args and "--user" not in args, args
        assert not any("authorization:" in arg.lower() for arg in args), args
        assert "-A" in args and args[args.index("-A") + 1], args
PY
}

assert_request() {
    python3 - "$TEST_ROOT/requests.jsonl" "$@" <<'PY'
import json
import sys
call = [json.loads(line) for line in open(sys.argv[1]) if json.loads(line)["kind"] == sys.argv[2]][-1]
assert call["url"].split("?")[0] == "https://www.reddit.com" + sys.argv[3], call
args = call["args"]
params = [args[i + 1] for i, value in enumerate(args) if value == "--data-urlencode"]
assert sorted(params) == sorted(sys.argv[4:]), params
PY
}

assert_payload() {
    PAYLOAD=$(sed -n 's/^Full payload: //p' "$TEST_ROOT/stdout")
    [ -s "$PAYLOAD" ] || fail "missing cached payload"
    python3 - "$PAYLOAD" "$1" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1]))
listing = data[0] if isinstance(data, list) else data
post = listing["data"]["children"][0]["data"]
assert listing["kind"] == "Listing"
if sys.argv[2] == "rss":
    assert data["source"] == "rss"
    assert post["title"] == "RSS snapshot"
    assert post["score"] is None and post["num_comments"] is None
elif sys.argv[2] == "public":
    assert isinstance(data, list) and len(data) == 2
    assert all(item["source"] == "public-json" for item in data)
    assert post["title"] == "Public snapshot" and post["selftext"] == "Full post text"
    assert post["score"] == 84 and post["num_comments"] == 11
    assert data[1]["data"]["children"][0]["data"]["body"] == "A public comment"
    assert data[1]["data"]["children"][1]["kind"] == "more"
else:
    assert post["title"] == "API snapshot"
    assert post["score"] == 42 and post["num_comments"] == 7
PY
}

# The environment switch must skip the file entirely, even if it is invalid.
reset_case
RSS_MODE=1
printf '%s\n' 'exit 99' > "$TEST_ROOT/config/.env"
run_ok auth_check.sh
assert_calls RSS
assert_request RSS /r/all/new.rss limit=1
grep -q 'RSS' "$TEST_ROOT/stdout" || fail "auth_check should report RSS"

# File switch and missing credentials both permit public reading without a UA.
reset_case
printf '%s\n' 'REDDIT_RSS_MODE=1' > "$TEST_ROOT/config/.env"
run_ok subreddit_top.sh --subreddit test --time week --limit 3 --include-comments
assert_calls RSS JSON
assert_request RSS /r/test/top.rss t=week limit=3
assert_request JSON /comments/abc123.json limit=25 raw_json=1
assert_payload rss
DETAIL_DIR=$(sed -n 's/^Comments cached for [0-9][0-9]* posts: //p' "$TEST_ROOT/stdout")
python3 - "$DETAIL_DIR" <<'PY'
import json
import sys
from pathlib import Path
files = list(Path(sys.argv[1]).rglob("abc123.json"))
assert len(files) == 1, files
data = json.load(files[0].open())
assert data[0]["data"]["children"][0]["data"]["score"] == 84
assert data[1]["data"]["children"][0]["data"]["body"] == "A public comment"
PY

reset_case
run_ok search.sh --query 'space & science' --subreddit test --sort new --time month --limit 4
assert_calls RSS
assert_request RSS /r/test/search.rss 'q=space & science' sort=new t=month limit=4 restrict_sr=on
assert_payload rss

reset_case
run_ok search.sh --query 'space & science' --sort top --time day --limit 2
assert_calls RSS
assert_request RSS /search.rss 'q=space & science' sort=top t=day limit=2
assert_payload rss

reset_case
run_ok user_posts.sh --username alice --sort top --time year --limit 5
assert_calls RSS
assert_request RSS /user/alice/submitted.rss sort=top t=year limit=5
assert_payload rss

reset_case
run_ok submission.sh --url https://www.reddit.com/r/test/comments/abc123/test/ --limit-comments 4
assert_calls JSON
assert_request JSON /comments/abc123.json limit=4 raw_json=1
assert_payload public
grep -Eqi 'more|incomplete' "$TEST_ROOT/stdout" "$TEST_ROOT/stderr" ||
    fail "submission should report incomplete comments"
run_ok submission.sh --url https://www.reddit.com/comments/abc123.json --limit-comments 4
assert_calls JSON
assert_payload public

reset_case
api_config
TOKEN_STATUS=403
run_ok submission.sh --id abc123
assert_calls TOKEN JSON
assert_payload public

reset_case
JSON_STATUS=503
run_fail subreddit_top.sh --subreddit test --include-comments
assert_calls RSS JSON
assert_payload rss
grep -Eq 'JSON|HTTP|503' "$TEST_ROOT/stderr" || fail "public detail error should be explicit"

# Authentication failures fall back; HTTP 401 first gets one token refresh.
for code in 400 401 403; do
    reset_case
    api_config
    TOKEN_STATUS="$code"
    run_ok subreddit_top.sh --subreddit test
    assert_calls TOKEN RSS
    assert_payload rss
done
for error in invalid_client invalid_grant unauthorized_client access_denied; do
    reset_case
    api_config
    TOKEN_ERROR="$error"
    run_ok subreddit_top.sh --subreddit test
    assert_calls TOKEN RSS
    assert_payload rss
done
reset_case
api_config
API_STATUS=403
run_ok subreddit_top.sh --subreddit test --include-comments
assert_calls TOKEN API RSS JSON
assert_payload rss

reset_case
api_config
API_STATUS=401
run_ok subreddit_top.sh --subreddit test
assert_calls TOKEN API TOKEN API RSS
assert_payload rss

reset_case
api_config
API_STATUS=401,200
run_ok subreddit_top.sh --subreddit test
assert_calls TOKEN API TOKEN API
assert_payload api

# Network failures, rate limits and service failures are not credential errors.
for code in network 429 500; do
    reset_case
    api_config
    TOKEN_STATUS="$code"
    run_fail subreddit_top.sh --subreddit test
    assert_calls TOKEN

    reset_case
    api_config
    API_STATUS="$code"
    run_fail subreddit_top.sh --subreddit test
    assert_calls TOKEN API
done

# Unsupported commands cannot silently change to public RSS or send writes.
for script in subreddit_info.sh post_create.sh comment_reply.sh; do
    reset_case
    RSS_MODE=1
    run_fail "$script" --subreddit test --id abc123 --title test --text test --confirm
    assert_calls
done

# Sources have separate cache files; failed refreshes preserve both snapshots.
for command in top search user submission; do
    PUBLIC_KIND=rss
    case "$command" in
        top) set -- subreddit_top.sh --subreddit test ;;
        search) set -- search.sh --query test ;;
        user) set -- user_posts.sh --username alice ;;
        submission) set -- submission.sh --id abc123; PUBLIC_KIND=public ;;
    esac
    reset_case
    api_config
    run_ok "$@"
    assert_payload api
    API_PAYLOAD="$PAYLOAD"
    cp "$API_PAYLOAD" "$TEST_ROOT/api-before.json"

    API_STATUS=403
    run_ok "$@" --no-cache
    assert_payload "$PUBLIC_KIND"
    RSS_PAYLOAD="$PAYLOAD"
    [ "$API_PAYLOAD" != "$RSS_PAYLOAD" ] || fail "$command cache sources should differ"
    cmp "$API_PAYLOAD" "$TEST_ROOT/api-before.json" || fail "$command RSS replaced API cache"
    cp "$RSS_PAYLOAD" "$TEST_ROOT/rss-before.json"

    RSS_MODE=1
    : > "$TEST_ROOT/requests.jsonl"
    run_ok "$@"
    assert_calls
    assert_payload "$PUBLIC_KIND"

    RSS_STATUS=503
    JSON_STATUS=503
    run_fail "$@" --no-cache
    cmp "$RSS_PAYLOAD" "$TEST_ROOT/rss-before.json" || fail "$command RSS error damaged cache"
    RSS_STATUS=200
    JSON_STATUS=200
    RSS_MALFORMED=1
    JSON_MALFORMED=1
    run_fail "$@" --no-cache
    cmp "$RSS_PAYLOAD" "$TEST_ROOT/rss-before.json" || fail "$command invalid RSS damaged cache"

    RSS_MODE=0
    API_STATUS=500
    run_fail "$@" --no-cache
    cmp "$API_PAYLOAD" "$TEST_ROOT/api-before.json" || fail "$command API error damaged cache"
done

echo "test_rss_mode: all assertions passed"
