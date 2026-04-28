#!/bin/sh
# Common functions for reddit-skill
# POSIX sh — no bashisms (cloud-sandbox compatible)
#
# Public API (sourced by other scripts):
#   load_config              — read .env, pick auth mode (app-only|user)
#   reddit_token             — get a valid access token (cached, auto-refresh)
#   reddit_get  PATH OUTFILE [-d k=v ...]    — authenticated GET, body to OUTFILE
#   reddit_post PATH OUTFILE [-d k=v ...]    — authenticated POST, body to OUTFILE
#   cache_key STRING         — deterministic 32-bit hex from input
#   require_write_enabled    — fail unless REDDIT_ENABLE_WRITE=1 and --confirm
#   parse_submission_id ARG  — extract base36 id from URL or raw id
#   require_user_mode        — fail unless mode=user
#   print_listing_summary FILE [N]  — print first N rows of cached JSON listing
#
# Variables exported by load_config:
#   REDDIT_AUTH_MODE         — "app-only" | "user"
#   REDDIT_OAUTH_BASE        — "https://oauth.reddit.com"
#   REDDIT_TOKEN_URL         — "https://www.reddit.com/api/v1/access_token"
#   REDDIT_USER_AGENT        — required for all requests
#   REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET   — sourced from .env

set -e

# --- Resolve dirs ----------------------------------------------------

if [ -z "${REDDIT_SCRIPT_DIR:-}" ]; then
    REDDIT_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi
if [ -z "${REDDIT_SKILL_DIR:-}" ]; then
    REDDIT_SKILL_DIR="$(cd "$REDDIT_SCRIPT_DIR/.." && pwd)"
fi
REDDIT_CONFIG_DIR="${REDDIT_CONFIG_DIR:-$REDDIT_SKILL_DIR/config}"
REDDIT_CACHE_DIR="${REDDIT_CACHE_DIR:-$REDDIT_SKILL_DIR/cache}"

REDDIT_OAUTH_BASE="https://oauth.reddit.com"
REDDIT_TOKEN_URL="https://www.reddit.com/api/v1/access_token"

REDDIT_TMPDIR="${TMPDIR:-/tmp}"
mkdir -p "$REDDIT_TMPDIR" "$REDDIT_CACHE_DIR"

# --- Error helper ----------------------------------------------------

die() {
    echo "[reddit-skill] $1" >&2
    [ -n "${2:-}" ] && echo "$2" >&2
    echo "" >&2
    echo "See: config/README.md" >&2
    exit 1
}

# --- Config loader ---------------------------------------------------

load_config() {
    _env_file="$REDDIT_CONFIG_DIR/.env"
    if [ -f "$_env_file" ]; then
        # shellcheck disable=SC1090
        . "$_env_file"
    fi

    [ -n "${REDDIT_CLIENT_ID:-}" ]     || die "REDDIT_CLIENT_ID not set in config/.env"
    [ -n "${REDDIT_CLIENT_SECRET:-}" ] || die "REDDIT_CLIENT_SECRET not set in config/.env"

    if [ -z "${REDDIT_USER_AGENT:-}" ]; then
        die "REDDIT_USER_AGENT not set" \
            "Reddit requires a custom User-Agent. See config/README.md."
    fi

    # Auth mode auto-detect: presence of username+password → user mode
    if [ -n "${REDDIT_USERNAME:-}" ] && [ -n "${REDDIT_PASSWORD:-}" ]; then
        REDDIT_AUTH_MODE="user"
    else
        REDDIT_AUTH_MODE="app-only"
    fi
    export REDDIT_AUTH_MODE
}

# --- Token cache -----------------------------------------------------

_token_cache_file() {
    echo "$REDDIT_CACHE_DIR/token.json"
}

# Echo cached token if not expired (30s safety margin), else nothing.
_get_cached_token() {
    _cf=$(_token_cache_file)
    [ -f "$_cf" ] || return 0
    _MODE="$REDDIT_AUTH_MODE" _CACHE="$_cf" python3 - <<'PY' 2>/dev/null
import json, os, time
try:
    with open(os.environ["_CACHE"]) as f:
        d = json.load(f)
    if d.get("mode") != os.environ["_MODE"]:
        raise SystemExit
    if d.get("expires_at", 0) - time.time() <= 30:
        raise SystemExit
    print(d.get("access_token", ""))
except SystemExit:
    pass
except Exception:
    pass
PY
}

_save_token() {
    _tok="$1"
    _exp="$2"
    _scope="$3"
    _cf=$(_token_cache_file)
    _old_umask=$(umask)
    umask 077
    _tmp="$REDDIT_CACHE_DIR/.token.tmp.$$"
    _T="$_tok" _E="$_exp" _S="$_scope" _M="$REDDIT_AUTH_MODE" _F="$_tmp" python3 - <<'PY'
import json, os
d = {
    "access_token": os.environ["_T"],
    "expires_at": int(os.environ["_E"]),
    "scope": os.environ["_S"],
    "mode": os.environ["_M"],
}
with open(os.environ["_F"], "w") as f:
    json.dump(d, f)
PY
    mv "$_tmp" "$_cf"
    umask "$_old_umask"
}

# Issue fresh token via OAuth POST. Echoes token on stdout.
_token_issue() {
    _now=$(date +%s)
    _resp_file="$REDDIT_TMPDIR/reddit_token_resp.$$.json"
    _hdr_file="$REDDIT_TMPDIR/reddit_token_hdr.$$.txt"
    # shellcheck disable=SC2064
    trap "rm -f '$_resp_file' '$_hdr_file'" EXIT INT TERM

    case "$REDDIT_AUTH_MODE" in
        app-only)
            _body="grant_type=client_credentials"
            ;;
        user)
            # urlencode user+password (POSIX sh — minimal; rely on python3)
            _body=$(REDDIT_USERNAME="$REDDIT_USERNAME" REDDIT_PASSWORD="$REDDIT_PASSWORD" \
                python3 -c '
import os, urllib.parse
u = urllib.parse.quote(os.environ["REDDIT_USERNAME"], safe="")
p = urllib.parse.quote(os.environ["REDDIT_PASSWORD"], safe="")
print(f"grant_type=password&username={u}&password={p}")
')
            ;;
        *) die "Unknown REDDIT_AUTH_MODE: $REDDIT_AUTH_MODE" ;;
    esac

    _status=$(curl -s -o "$_resp_file" -D "$_hdr_file" -w '%{http_code}' \
        -X POST "$REDDIT_TOKEN_URL" \
        -u "$REDDIT_CLIENT_ID:$REDDIT_CLIENT_SECRET" \
        -A "$REDDIT_USER_AGENT" \
        --data "$_body")

    if [ "$_status" != "200" ]; then
        _err=$(cat "$_resp_file" 2>/dev/null)
        rm -f "$_resp_file" "$_hdr_file"
        trap - EXIT INT TERM
        die "Token request failed (HTTP $_status, mode=$REDDIT_AUTH_MODE)" "$_err"
    fi

    # Parse {"access_token":"...","token_type":"bearer","expires_in":N,"scope":"..."}
    _result=$(_NOW="$_now" _F="$_resp_file" python3 - <<'PY'
import json, os, sys
try:
    with open(os.environ["_F"]) as f:
        d = json.load(f)
except Exception as e:
    print(f"PARSE:{e}")
    sys.exit(0)
tok = d.get("access_token", "")
exp_in = d.get("expires_in", 3600)
scope = d.get("scope", "")
err = d.get("error", "")
if not tok:
    print(f"ERR:{err or json.dumps(d)[:200]}")
    sys.exit(0)
print(f"{tok}|{int(os.environ['_NOW'])+int(exp_in)}|{scope}")
PY
)
    rm -f "$_resp_file" "$_hdr_file"
    trap - EXIT INT TERM

    case "$_result" in
        PARSE:*) die "Token response parse error: ${_result#PARSE:}" ;;
        ERR:*)   die "Token response error: ${_result#ERR:}" ;;
    esac

    _tok=$(printf '%s' "$_result" | cut -d'|' -f1)
    _exp=$(printf '%s' "$_result" | cut -d'|' -f2)
    _scope=$(printf '%s' "$_result" | cut -d'|' -f3-)
    _save_token "$_tok" "$_exp" "$_scope"
    printf '%s' "$_tok"
}

reddit_token() {
    _cached=$(_get_cached_token)
    if [ -n "$_cached" ]; then
        printf '%s' "$_cached"
        return 0
    fi
    _token_issue
}

# --- HTTP wrappers ---------------------------------------------------
#
# reddit_get PATH OUTFILE [extra curl args ...]
# reddit_post PATH OUTFILE [extra curl args ...]
#
# OUTFILE receives the response body.
# Honors x-ratelimit-* headers and Retry-After on 429 (one retry if reset ≤ 60s).
# On 401 invalidates cached token and retries once.

_reddit_request() {
    _method="$1"   # GET | POST
    _path="$2"
    _out="$3"
    shift 3

    _tok=$(reddit_token)
    [ -n "$_tok" ] || die "Failed to obtain access token"

    _hdr="$REDDIT_TMPDIR/reddit_hdr.$$.txt"
    _url="${REDDIT_OAUTH_BASE}${_path}"

    # Append raw_json=1 to all calls — Reddit returns un-HTML-escaped strings
    case "$_url" in
        *\?*) _url="${_url}&raw_json=1" ;;
        *)    _url="${_url}?raw_json=1" ;;
    esac

    if [ "$_method" = "GET" ]; then
        _status=$(curl -s -G -o "$_out" -D "$_hdr" -w '%{http_code}' \
            -A "$REDDIT_USER_AGENT" \
            -H "Authorization: Bearer $_tok" \
            "$@" \
            "$_url")
    else
        _status=$(curl -s -X "$_method" -o "$_out" -D "$_hdr" -w '%{http_code}' \
            -A "$REDDIT_USER_AGENT" \
            -H "Authorization: Bearer $_tok" \
            "$@" \
            "$_url")
    fi

    case "$_status" in
        2[0-9][0-9])
            rm -f "$_hdr"
            return 0
            ;;
        401)
            # Token might be stale — invalidate cache, retry once.
            if [ -z "${_REDDIT_RETRY_401:-}" ]; then
                rm -f "$(_token_cache_file)"
                rm -f "$_hdr"
                _REDDIT_RETRY_401=1 _reddit_request "$_method" "$_path" "$_out" "$@"
                return $?
            fi
            _err=$(cat "$_out" 2>/dev/null)
            rm -f "$_hdr"
            die "HTTP 401 Unauthorized after token refresh" "$_err"
            ;;
        429)
            _retry=$(grep -i '^Retry-After:' "$_hdr" | sed 's/[^0-9]//g' | head -1)
            _reset=$(grep -i '^x-ratelimit-reset:' "$_hdr" | sed 's/[^0-9.]//g' | head -1 | cut -d. -f1)
            _wait="$_retry"
            [ -z "$_wait" ] && _wait="$_reset"
            rm -f "$_hdr"
            if [ -z "${_REDDIT_RETRY_429:-}" ] && [ -n "$_wait" ] && [ "$_wait" -le 60 ] 2>/dev/null; then
                echo "Rate limited (429). Waiting ${_wait}s..." >&2
                sleep "$_wait"
                _REDDIT_RETRY_429=1 _reddit_request "$_method" "$_path" "$_out" "$@"
                return $?
            fi
            _err=$(cat "$_out" 2>/dev/null | head -c 500)
            die "HTTP 429 Too Many Requests (wait=${_wait:-?}s)" "$_err"
            ;;
        *)
            _err=$(cat "$_out" 2>/dev/null | head -c 1000)
            rm -f "$_hdr"
            die "HTTP $_status from $_path" "$_err"
            ;;
    esac
}

reddit_get()  { _reddit_request "GET"  "$@"; }
reddit_post() { _reddit_request "POST" "$@"; }

# --- Cache helpers ---------------------------------------------------

# cache_key STRING — 32-bit hex hash via cksum
cache_key() {
    printf '%s' "$1" | cksum | awk '{printf "%08x", $1}'
}

# --- Write-mode guard ------------------------------------------------
#
# Each write-script must call:
#   require_write_enabled "$CONFIRM_FLAG" "human-readable action"
# where CONFIRM_FLAG is "1" if --confirm was passed and "" otherwise.
#
# Behavior (matches docs in SKILL.md / config/README.md):
#   - REDDIT_ENABLE_WRITE != 1     → die with error (envelope-level gate)
#   - REDDIT_AUTH_MODE != user     → die with error (need password grant)
#   - CONFIRM_FLAG empty           → returns 1 — caller prints dry-run and exits 0
#   - all guards pass              → returns 0 — caller proceeds with real POST

require_write_enabled() {
    _confirm="$1"
    _action="$2"

    if [ "${REDDIT_ENABLE_WRITE:-}" != "1" ]; then
        die "Write is disabled" \
            "Set REDDIT_ENABLE_WRITE=1 in config/.env to allow ${_action}."
    fi

    if [ "$REDDIT_AUTH_MODE" != "user" ]; then
        die "Write requires user mode (REDDIT_USERNAME + REDDIT_PASSWORD)" \
            "Currently in ${REDDIT_AUTH_MODE}. ${_action} is not possible without a user token."
    fi

    if [ -z "$_confirm" ]; then
        echo "DRY-RUN: ${_action}" >&2
        echo "  No request was sent. Pass --confirm to actually execute." >&2
        return 1
    fi
    return 0
}

require_user_mode() {
    [ "$REDDIT_AUTH_MODE" = "user" ] || \
        die "This operation requires user mode (REDDIT_USERNAME + REDDIT_PASSWORD)" \
            "Currently in ${REDDIT_AUTH_MODE}."
}

# --- Submission id parsing -------------------------------------------
#
# parse_submission_id ARG → echoes base36 id (without t3_ prefix)
# Accepts:
#   abc123                                                  → abc123
#   t3_abc123                                               → abc123
#   https://www.reddit.com/r/sub/comments/abc123/title/     → abc123
#   https://www.reddit.com/r/sub/comments/abc123/           → abc123
#   https://redd.it/abc123                                  → abc123

parse_submission_id() {
    _ARG="$1" python3 - <<'PY'
import os, re, sys
arg = os.environ["_ARG"].strip()
if not arg:
    sys.exit(1)
# Try URL paths
m = re.search(r"/comments/([a-z0-9]{4,12})(?:/|$)", arg, re.I)
if m:
    print(m.group(1).lower())
    sys.exit(0)
m = re.search(r"redd\.it/([a-z0-9]{4,12})(?:/|$|\?)", arg, re.I)
if m:
    print(m.group(1).lower())
    sys.exit(0)
# Strip t3_ prefix
if arg.startswith("t3_"):
    arg = arg[3:]
if re.fullmatch(r"[a-z0-9]{4,12}", arg, re.I):
    print(arg.lower())
    sys.exit(0)
sys.exit(1)
PY
}

# --- Output helpers --------------------------------------------------
#
# print_listing_summary FILE [N]
#   Pretty-prints first N items of a Reddit listing JSON. Default N=10.
#   Hard-capped at REDDIT_PRINT_CAP (default 8) to keep stdout ≤ 30 lines —
#   the fetched listing on disk may be larger; only the print is capped.

print_listing_summary() {
    _F="$1" _N="${2:-8}" _CAP="${REDDIT_PRINT_CAP:-8}" python3 - <<'PY'
import json, os, sys
n_req = int(os.environ.get("_N", "8"))
cap = int(os.environ.get("_CAP", "8"))
n = min(n_req, cap)
try:
    with open(os.environ["_F"]) as f:
        d = json.load(f)
except Exception as e:
    print(f"(parse error: {e})")
    sys.exit(0)

# Listing shape: {"kind":"Listing","data":{"children":[{"kind":"t3","data":{...}}, ...]}}
data = d.get("data") if isinstance(d, dict) else None
children = data.get("children") if isinstance(data, dict) else None
if not isinstance(children, list):
    # Maybe it's an array of two listings (e.g. /comments/{id})
    if isinstance(d, list) and d and isinstance(d[0], dict):
        children = d[0].get("data", {}).get("children", [])
    else:
        print("(no listing children)")
        sys.exit(0)

shown = 0
for c in children[:n]:
    cd = c.get("data", {}) if isinstance(c, dict) else {}
    kind = c.get("kind", "?") if isinstance(c, dict) else "?"
    if kind == "t3":  # post
        title = (cd.get("title") or "").strip().replace("\n", " ")[:120]
        sub = cd.get("subreddit", "")
        score = cd.get("score", 0)
        n_comm = cd.get("num_comments", 0)
        author = cd.get("author", "")
        url = "https://www.reddit.com" + cd.get("permalink", "")
        print(f"  [{score:>5} ↑ {n_comm:>4} 💬] r/{sub}  by u/{author}")
        print(f"    {title}")
        print(f"    {url}")
    elif kind == "t1":  # comment
        body = (cd.get("body") or "").strip().replace("\n", " ")[:160]
        sub = cd.get("subreddit", "")
        score = cd.get("score", 0)
        author = cd.get("author", "")
        link = "https://www.reddit.com" + cd.get("permalink", "")
        print(f"  [{score:>5} ↑] r/{sub}  by u/{author}")
        print(f"    {body}")
        print(f"    {link}")
    elif kind == "t5":  # subreddit
        name = cd.get("display_name_prefixed", cd.get("display_name", ""))
        subs = cd.get("subscribers", 0)
        active = cd.get("accounts_active") or cd.get("active_user_count") or 0
        print(f"  {name:<30}  subs={subs:>9}  active={active}")
    else:
        print(f"  ({kind}) {cd.get('id','?')}")
    shown += 1

total = len(children)
if total > shown:
    suffix = ""
    if n_req > cap and total > cap:
        suffix = f" — print capped at {cap}, fetched {total}"
    print(f"  ... ({total - shown} more in cache file{suffix})")
PY
}
