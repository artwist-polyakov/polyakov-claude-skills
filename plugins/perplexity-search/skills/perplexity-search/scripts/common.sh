#!/bin/sh
# Common functions for perplexity-search.
# POSIX sh — no bashisms (cloud-sandbox compatible).
# JSON is built and parsed with inline python3 stdlib: sed/grep parsing breaks
# on escaped text and nested citations (see AGENTS.md).
#
# Public API (sourced by other scripts):
#   die MSG [DETAIL]                   — error out with a pointer to config/README.md
#   load_config                        — read config/.env, validate key, set defaults
#   cache_key STRING                   — deterministic 8-hex-char key
#   cache_fresh FILE [TTL]             — 0 if FILE exists, non-empty, younger than TTL
#   pplx_post PATH BODY_FILE OUT_FILE  — authenticated POST, response body to OUT_FILE
#   pplx_get  PATH OUT_FILE            — authenticated GET
#   build_agent_body OUT_FILE          — JSON body for POST /v1/agent from PPLX_ARG_*
#   build_search_body OUT_FILE         — JSON body for POST /search from PPLX_ARG_*
#   render_agent_response JSON MD      — markdown report from an agent response
#   render_search_response JSON TSV TXT — table + snippet dump from a Search response
#   normalize_date VALUE               — YYYY-MM-DD | MM/DD/YYYY → MM/DD/YYYY
#   resolve_profile NAME               — apply PPLX_PROFILE_<NAME>_* defaults
#   index_append SCRIPT KEY LABEL PATH — append a row to cache/index.tsv
#   print_head FILE [N]                — first N lines, then a pointer to FILE
#
# Context-window contract: scripts print at most a few dozen lines. Every full
# payload (raw JSON, snippets, answers) lands in cache/ and is read back with
# grep / Read on demand — see cache_grep.sh.

set -e

# --- Resolve dirs ----------------------------------------------------

if [ -z "${PPLX_SCRIPT_DIR:-}" ]; then
    PPLX_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi
if [ -z "${PPLX_SKILL_DIR:-}" ]; then
    PPLX_SKILL_DIR="$(cd "$PPLX_SCRIPT_DIR/.." && pwd)"
fi
PPLX_CONFIG_DIR="${PPLX_CONFIG_DIR:-$PPLX_SKILL_DIR/config}"
PPLX_CACHE_DIR="${PPLX_CACHE_DIR:-$PPLX_SKILL_DIR/cache}"
PPLX_CONFIG_FILE="${PPLX_CONFIG_FILE:-$PPLX_CONFIG_DIR/.env}"
PPLX_INDEX_FILE="$PPLX_CACHE_DIR/index.tsv"

PPLX_API_BASE="${PPLX_API_BASE:-https://api.perplexity.ai}"
PPLX_TMPDIR="${TMPDIR:-/tmp}"

# Request parameters shared by all scripts. Flag parsing lives in each script;
# body shaping reads these and stays in one testable place.
PPLX_ARG_QUERY_COUNT=0       # queries live in PPLX_ARG_QUERY_1..N, see below
PPLX_ARG_INPUT=""
PPLX_ARG_PRESET=""
PPLX_ARG_MODEL=""
PPLX_ARG_TOOLS=""
PPLX_ARG_INSTRUCTIONS=""
PPLX_ARG_DOMAINS=""
PPLX_ARG_RECENCY=""
PPLX_ARG_AFTER=""
PPLX_ARG_BEFORE=""
PPLX_ARG_UPDATED_AFTER=""
PPLX_ARG_UPDATED_BEFORE=""
PPLX_ARG_CONTEXT_SIZE=""
PPLX_ARG_MAX_RESULTS=""
PPLX_ARG_COUNTRY=""
PPLX_ARG_LANGUAGE=""
PPLX_ARG_MAX_OUTPUT_TOKENS=""
PPLX_ARG_SCHEMA_FILE=""
PPLX_ARG_BACKGROUND=""
PPLX_PROFILE_LABEL=""

# --- Error helper ----------------------------------------------------

die() {
    echo "[perplexity-search] $1" >&2
    if [ -n "${2:-}" ]; then
        echo "$2" >&2
    fi
    echo "" >&2
    echo "See: config/README.md" >&2
    exit 1
}

require_python3() {
    command -v python3 >/dev/null 2>&1 || \
        die "python3 is required but not found" \
            "Install Python 3.7+ and make sure python3 is on PATH."
}

require_curl() {
    command -v curl >/dev/null 2>&1 || die "curl is required but not found"
}

# --- Config loader ---------------------------------------------------
#
# .env is normalized by sanitize_env.sh (quotes unquoted values containing
# spaces) and then converted to `export` lines by python3, so config text is
# never executed as shell code.

load_config() {
    require_python3
    require_curl

    if [ -f "$PPLX_CONFIG_FILE" ]; then
        if [ -f "$PPLX_SCRIPT_DIR/sanitize_env.sh" ] && [ -w "$PPLX_CONFIG_FILE" ]; then
            sh "$PPLX_SCRIPT_DIR/sanitize_env.sh" "$PPLX_CONFIG_FILE"
        fi
        _lc_exports=$(mktemp "$PPLX_TMPDIR/pplx_env.XXXXXX") || die "mktemp failed"
        chmod 600 "$_lc_exports"
        if ! _F="$PPLX_CONFIG_FILE" python3 - > "$_lc_exports" <<'PY'
import os, pathlib, re, shlex

path = pathlib.Path(os.environ["_F"])
key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_value(raw):
    """Read one .env value as a POSIX shell reads the right-hand side of an
    assignment: quoting modes may be mixed inside a single word.

      K=Tech\\ news      → Tech news     (backslash escapes the space)
      K=Tech" "news      → Tech news     (a quoted run inside a bare word)
      K="C:\\temp\\q"     → C:\\temp\\q     (\\ is literal unless it escapes " \\ $ `)
      K='a # b'          → a # b         (single quotes take everything)
      K=a\\#b             → a#b           (escaped, so not a comment)
      K=a #b             → a             (# starts a word → comment)
    """
    raw = raw.lstrip()
    out = []
    keep = 0            # length of `out` up to the last character worth keeping
    i, n = 0, len(raw)
    at_word_start = True  # start of the value, or just after unquoted whitespace

    def emit(text):
        nonlocal keep
        out.append(text)
        keep = len(out)

    while i < n:
        ch = raw[i]

        if ch == "\\" and i + 1 < n:
            emit(raw[i + 1])
            i += 2
            at_word_start = False
            continue

        if ch == "'":
            end = raw.find("'", i + 1)
            if end == -1:  # unterminated — keep the rest rather than dropping it
                emit(raw[i + 1:])
                break
            emit(raw[i + 1:end])
            i = end + 1
            at_word_start = False
            continue

        if ch == '"':
            i += 1
            while i < n:
                c = raw[i]
                # Inside double quotes a backslash only escapes these four.
                if c == "\\" and raw[i + 1:i + 2] in ('"', "\\", "$", "`"):
                    emit(raw[i + 1])
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                emit(c)
                i += 1
            at_word_start = False
            continue

        if ch == "#" and at_word_start:
            break  # trailing comment

        out.append(ch)
        if not ch.isspace():
            keep = len(out)
        at_word_start = ch.isspace()
        i += 1

    return "".join(out[:keep])


for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key[len("export "):].strip()
    if not key_re.fullmatch(key):
        continue
    print(f"export {key}={shlex.quote(parse_value(value))}")
PY
        then
            rm -f "$_lc_exports"
            die "Failed to parse $PPLX_CONFIG_FILE"
        fi
        # shellcheck disable=SC1090
        . "$_lc_exports"
        rm -f "$_lc_exports"
    fi

    [ -n "${PERPLEXITY_API_KEY:-}" ] || \
        die "PERPLEXITY_API_KEY not set" \
            "Copy config/.env.example to config/.env and paste your key from https://www.perplexity.ai/account/api"

    # The key is streamed to curl inside a quoted header value; reject anything
    # that could break out of the quoting.
    case "$PERPLEXITY_API_KEY" in
        *[!A-Za-z0-9._-]*)
            die "PERPLEXITY_API_KEY contains unexpected characters" \
                "Expected only letters, digits, dot, underscore and dash (keys look like pplx-...). Check config/.env for stray quotes or spaces."
            ;;
    esac

    # Remember whether the preset came from config before the default masks it:
    # scripts with their own fallback (research → high, fetch_url → low) must be
    # able to tell "the user chose medium" from "nobody chose anything".
    PPLX_PRESET_EXPLICIT="${PPLX_PRESET:+1}"
    PPLX_PRESET="${PPLX_PRESET:-medium}"
    PPLX_MODEL="${PPLX_MODEL:-}"
    PPLX_CONTEXT_SIZE="${PPLX_CONTEXT_SIZE:-medium}"
    PPLX_MAX_RESULTS="${PPLX_MAX_RESULTS:-10}"
    PPLX_COUNTRY="${PPLX_COUNTRY:-}"
    PPLX_LANGUAGE="${PPLX_LANGUAGE:-}"
    PPLX_CACHE_TTL="${PPLX_CACHE_TTL:-900}"
    # Deep research costs minutes and dollars — keep its answers around for a day.
    PPLX_RESEARCH_CACHE_TTL="${PPLX_RESEARCH_CACHE_TTL:-86400}"
    PPLX_HTTP_TIMEOUT="${PPLX_HTTP_TIMEOUT:-300}"
    PPLX_MAX_RETRIES="${PPLX_MAX_RETRIES:-3}"
    PPLX_PRINT_LIMIT="${PPLX_PRINT_LIMIT:-30}"

    mkdir -p "$PPLX_CACHE_DIR"
}

# --- Cache helpers ---------------------------------------------------

# cache_key STRING → 8 hex chars, stable across runs
cache_key() {
    printf '%s' "$1" | cksum | awk '{printf "%08x", $1}'
}

# cache_fresh FILE [TTL_SECONDS]
# 0 → usable cache hit; 1 → miss (absent, empty, or older than TTL).
# TTL of 0 disables cache reads entirely.
cache_fresh() {
    _cf_file="$1"
    _cf_ttl="${2:-${PPLX_CACHE_TTL:-900}}"

    case "$_cf_ttl" in
        ''|*[!0-9]*) _cf_ttl=0 ;;
    esac
    [ "$_cf_ttl" -gt 0 ] || return 1
    [ -s "$_cf_file" ] || return 1

    _F="$_cf_file" _TTL="$_cf_ttl" python3 - <<'PY'
import os, sys, time
try:
    age = time.time() - os.path.getmtime(os.environ["_F"])
except OSError:
    sys.exit(1)
sys.exit(0 if age < float(os.environ["_TTL"]) else 1)
PY
}

# cache_age_seconds FILE — age in whole seconds, exit 1 if the file is gone
cache_age_seconds() {
    _F="$1" python3 - <<'PY'
import os, sys, time
try:
    print(int(time.time() - os.path.getmtime(os.environ["_F"])))
except OSError:
    sys.exit(1)
PY
}

# format_age SECONDS → 42s | 12m | 3h | 2d
format_age() {
    _fa_s="$1"
    case "$_fa_s" in
        ''|*[!0-9]*) printf '?'; return 0 ;;
    esac
    if [ "$_fa_s" -lt 60 ]; then
        printf '%ss' "$_fa_s"
    elif [ "$_fa_s" -lt 3600 ]; then
        printf '%sm' "$((_fa_s / 60))"
    elif [ "$_fa_s" -lt 86400 ]; then
        printf '%sh' "$((_fa_s / 3600))"
    else
        printf '%sd' "$((_fa_s / 86400))"
    fi
}

# effective_cache_ttl CONFIGURED_TTL RECENCY
# A --recency filter is the caller saying "this question is time-sensitive".
# Honour that by capping how old a reused answer may be, so a 15-minute cache
# cannot quietly answer a "what happened in the last hour" question.
# An explicit --cache-ttl is the more deliberate knob and is never capped —
# callers pass the configured default here only when they did not set one.
effective_cache_ttl() {
    _ect_ttl="$1"
    case "$2" in
        hour) _ect_cap=300 ;;
        day)  _ect_cap=3600 ;;
        week) _ect_cap=86400 ;;
        *)    printf '%s' "$_ect_ttl"; return 0 ;;
    esac
    case "$_ect_ttl" in
        ''|*[!0-9]*) printf '%s' "$_ect_ttl"; return 0 ;;
    esac
    if [ "$_ect_ttl" -gt "$_ect_cap" ]; then
        printf '%s' "$_ect_cap"
    else
        printf '%s' "$_ect_ttl"
    fi
}

# index_append SCRIPT KEY LABEL PATH — one grep-able row per run
index_append() {
    _IDX="$PPLX_INDEX_FILE" _S="$1" _K="$2" _L="$3" _P="$4" python3 - <<'PY'
import os, time
row = [
    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    os.environ["_S"],
    os.environ["_K"],
    " ".join(os.environ["_L"].split())[:200],
    os.environ["_P"],
]
with open(os.environ["_IDX"], "a", encoding="utf-8") as fh:
    fh.write("\t".join(c.replace("\t", " ") for c in row) + "\n")
PY
}

# --- HTTP ------------------------------------------------------------
#
# The API key is streamed to curl through `--config -`, so it never appears in
# argv (visible via ps) and is never written to disk.
#
# The response lands in a sibling temp file and is renamed onto OUT_FILE only
# after a 2xx. Writing straight to OUT_FILE would park the error body at the
# cache path, where the next identical call inside the TTL would read it back as
# a legitimate (empty) result — and it would also destroy a good cached answer
# whenever a refresh failed. The temp file sits in the destination directory so
# the rename is atomic.

# _rq_fail MESSAGE [DETAIL] — drop the scratch files _pplx_request is holding,
# then report. Split out so every failure path cleans up the same way.
_rq_fail() {
    rm -f "$_rq_tmp" "$_rq_hdr"
    die "$1" "${2:-}"
}

_pplx_request() {
    _rq_method="$1"
    _rq_path="$2"
    _rq_body="$3"
    _rq_out="$4"

    _rq_hdr="$PPLX_TMPDIR/pplx_hdr.$$.txt"
    _rq_tmp="${_rq_out}.part.$$"
    _rq_attempt=0
    _rq_delay=2

    while :; do
        _rq_attempt=$((_rq_attempt + 1))

        if [ "$_rq_method" = "POST" ]; then
            _rq_status=$(printf 'header = "Authorization: Bearer %s"\n' "$PERPLEXITY_API_KEY" \
                | curl -sS --config - \
                    -H "Content-Type: application/json" \
                    -o "$_rq_tmp" -D "$_rq_hdr" -w '%{http_code}' \
                    --max-time "$PPLX_HTTP_TIMEOUT" \
                    -X POST --data-binary "@$_rq_body" \
                    "${PPLX_API_BASE}${_rq_path}") || _rq_status="000"
        else
            _rq_status=$(printf 'header = "Authorization: Bearer %s"\n' "$PERPLEXITY_API_KEY" \
                | curl -sS --config - \
                    -o "$_rq_tmp" -D "$_rq_hdr" -w '%{http_code}' \
                    --max-time "$PPLX_HTTP_TIMEOUT" \
                    "${PPLX_API_BASE}${_rq_path}") || _rq_status="000"
        fi

        case "$_rq_status" in
            2[0-9][0-9])
                mv -f "$_rq_tmp" "$_rq_out" || _rq_fail "Failed to store the response at $_rq_out"
                rm -f "$_rq_hdr"
                return 0
                ;;
            401|403)
                _rq_fail "HTTP $_rq_status — API key rejected" \
                    "Check PERPLEXITY_API_KEY in config/.env and that the key has API credits."
                ;;
            429)
                # Retry-After may also be an HTTP-date; fall back to backoff then.
                _rq_wait=$(grep -i '^retry-after:' "$_rq_hdr" 2>/dev/null | head -1 \
                    | sed 's/^[^:]*:[[:space:]]*//' | tr -d '\r\n')
                case "$_rq_wait" in
                    ''|*[!0-9]*) _rq_wait="$_rq_delay" ;;
                esac
                if [ "$_rq_attempt" -lt "$PPLX_MAX_RETRIES" ] && [ "$_rq_wait" -le 60 ] 2>/dev/null; then
                    echo "Rate limited (429). Retry ${_rq_attempt}/${PPLX_MAX_RETRIES} in ${_rq_wait}s..." >&2
                    sleep "$_rq_wait"
                    _rq_delay=$((_rq_delay * 2))
                    continue
                fi
                _rq_fail "HTTP 429 Too Many Requests (gave up after ${_rq_attempt} attempts)" \
                    "Search API allows 50 query units/s; Agent API limits are tier-based. Rejected 429s are not billed."
                ;;
            000|5[0-9][0-9])
                if [ "$_rq_attempt" -lt "$PPLX_MAX_RETRIES" ]; then
                    echo "Transient failure (HTTP $_rq_status). Retry ${_rq_attempt}/${PPLX_MAX_RETRIES} in ${_rq_delay}s..." >&2
                    sleep "$_rq_delay"
                    _rq_delay=$((_rq_delay * 2))
                    continue
                fi
                _rq_fail "HTTP $_rq_status from $_rq_path after ${_rq_attempt} attempts" \
                    "$(head -c 500 "$_rq_tmp" 2>/dev/null)"
                ;;
            *)
                _rq_fail "HTTP $_rq_status from $_rq_path" \
                    "$(head -c 800 "$_rq_tmp" 2>/dev/null)"
                ;;
        esac
    done
}

# pplx_post PATH BODY_FILE OUT_FILE
pplx_post() {
    [ -s "$2" ] || die "Internal error: empty request body for $1"
    _pplx_request "POST" "$1" "$2" "$3"
}

# pplx_get PATH OUT_FILE
pplx_get() {
    _pplx_request "GET" "$1" "" "$2"
}

# pplx_probe_get PATH OUT_FILE — one non-fatal GET, no retries.
# Prints the HTTP status ("000" when the request never completed) and always
# returns 0, so the caller can tell "definitely gone" (404) from "could not
# tell" (000/5xx) — the difference decides whether spending money is safe.
# OUT_FILE is written only on 2xx.
pplx_probe_get() {
    _pg_tmp="$2.probe.$$"
    _pg_status=$(printf 'header = "Authorization: Bearer %s"\n' "$PERPLEXITY_API_KEY" \
        | curl -sS --config - \
            -o "$_pg_tmp" -w '%{http_code}' \
            --max-time "$PPLX_HTTP_TIMEOUT" \
            "${PPLX_API_BASE}$1" 2>/dev/null) || _pg_status="000"

    case "$_pg_status" in
        2[0-9][0-9]) mv -f "$_pg_tmp" "$2" 2>/dev/null || _pg_status="000" ;;
        *)           rm -f "$_pg_tmp" ;;
    esac
    rm -f "$_pg_tmp"
    printf '%s' "$_pg_status"
}

# adoptable_status STATUS — 0 when a run in this state is worth reusing at all.
# A run that failed, was cancelled, or came back incomplete is not adopted:
# starting over is the useful move there. `completed` additionally has to pass
# the freshness check in the caller — see research.sh.
adoptable_status() {
    case "$1" in
        queued|in_progress|completed) return 0 ;;
        *) return 1 ;;
    esac
}

# response_age_seconds FILE — seconds since the run's own created_at.
# Exits 1 when the response carries no usable timestamp.
response_age_seconds() {
    _F="$1" python3 - <<'PY'
import json, os, sys, time
try:
    with open(os.environ["_F"], encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    sys.exit(1)
created = data.get("created_at") if isinstance(data, dict) else None
if not isinstance(created, (int, float)) or isinstance(created, bool):
    sys.exit(1)
print(int(max(0, time.time() - created)))
PY
}

# --- Input normalization ---------------------------------------------

# normalize_date VALUE → MM/DD/YYYY (accepts YYYY-MM-DD, M/D/YYYY, MM/DD/YYYY)
normalize_date() {
    [ -n "$1" ] || return 0
    _nd_out=$(_D="$1" python3 - <<'PY'
import os, re, sys
v = os.environ["_D"].strip()
m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
if m:
    y, mo, d = m.groups()
    print(f"{int(mo):02d}/{int(d):02d}/{y}")
    sys.exit(0)
m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
if m:
    mo, d, y = m.groups()
    print(f"{int(mo):02d}/{int(d):02d}/{y}")
    sys.exit(0)
sys.exit(1)
PY
) || die "Bad date: $1" "Use YYYY-MM-DD or MM/DD/YYYY."
    printf '%s' "$_nd_out"
}

# --- Query list -------------------------------------------------------
#
# Queries are kept as PPLX_ARG_QUERY_1..N, one variable per CLI argument,
# instead of one newline-joined string. A single --query may legitimately
# contain newlines — a pasted prompt, a command substitution — and an in-band
# delimiter would silently turn it into several separate (billed) queries.

queries_reset() {
    _qr_i=1
    while [ "$_qr_i" -le "${PPLX_ARG_QUERY_COUNT:-0}" ]; do
        unset "PPLX_ARG_QUERY_$_qr_i"
        _qr_i=$((_qr_i + 1))
    done
    PPLX_ARG_QUERY_COUNT=0
}

query_add() {
    PPLX_ARG_QUERY_COUNT=$((PPLX_ARG_QUERY_COUNT + 1))
    eval "PPLX_ARG_QUERY_${PPLX_ARG_QUERY_COUNT}=\$1"
}

# query_get N — print the Nth query (1-based), empty if absent
query_get() {
    eval "printf '%s' \"\${PPLX_ARG_QUERY_$1:-}\""
}

# require_value FLAG REMAINING_ARGC — call as `require_value "$1" $#` before a
# `shift 2`, so a trailing flag reports itself instead of failing inside shift.
require_value() {
    [ "$2" -ge 2 ] || die "Option $1 requires a value" "Run the script with --help for usage."
}

# handle_help_flag "$@" — print usage and exit before load_config, so --help
# works without an API key. Each script defines usage() first.
handle_help_flag() {
    for _hh_arg in "$@"; do
        case "$_hh_arg" in
            -h|--help) usage; exit 0 ;;
        esac
    done
}

validate_tools() {
    [ -n "$1" ] || return 0
    _vt_old_ifs="$IFS"
    IFS=','
    for _vt_name in $1; do
        case "$_vt_name" in
            ''|web_search|fetch_url|finance_search|people_search|sandbox) : ;;
            *)
                IFS="$_vt_old_ifs"
                die "Unknown tool '$_vt_name'" \
                    "Allowed: web_search, fetch_url, finance_search, people_search, sandbox."
                ;;
        esac
    done
    IFS="$_vt_old_ifs"
}

# require_uint FLAG VALUE [MIN] [MAX]
require_uint() {
    case "$2" in
        ''|*[!0-9]*) die "Option $1 expects a non-negative integer, got '$2'" ;;
    esac
    if [ -n "${3:-}" ] && [ "$2" -lt "$3" ]; then
        die "Option $1 must be >= $3, got '$2'"
    fi
    if [ -n "${4:-}" ] && [ "$2" -gt "$4" ]; then
        die "Option $1 must be <= $4, got '$2'"
    fi
}

# validate_response_id VALUE — the id is interpolated into a URL path
validate_response_id() {
    case "$1" in
        ''|*[!A-Za-z0-9_-]*)
            die "Invalid response id: '$1'" "Ids look like resp_abc123 (letters, digits, _ and - only)."
            ;;
    esac
}

validate_recency() {
    [ -n "$1" ] || return 0
    case "$1" in
        hour|day|week|month|year) return 0 ;;
        *) die "Bad --recency '$1'" "Allowed: hour, day, week, month, year." ;;
    esac
}

validate_context_size() {
    [ -n "$1" ] || return 0
    case "$1" in
        low|medium|high) return 0 ;;
        *) die "Bad --context-size '$1'" "Allowed: low, medium, high." ;;
    esac
}

# resolve_profile NAME — pull domain/recency/context defaults out of .env.
# Profile ids are restricted to [A-Za-z0-9_] because they are expanded via eval.
resolve_profile() {
    _rp_name="$1"
    [ -n "$_rp_name" ] || return 0

    _rp_clean=$(printf '%s' "$_rp_name" | sed 's/[^A-Za-z0-9_]//g')
    if [ "$_rp_clean" != "$_rp_name" ]; then
        die "Invalid profile '$_rp_name'" "Only letters, digits and underscores are allowed."
    fi

    _rp_upper=$(printf '%s' "$_rp_name" | tr '[:lower:]' '[:upper:]')
    eval "_rp_domains=\${PPLX_PROFILE_${_rp_upper}_DOMAINS:-}"
    eval "_rp_recency=\${PPLX_PROFILE_${_rp_upper}_RECENCY:-}"
    eval "_rp_context=\${PPLX_PROFILE_${_rp_upper}_CONTEXT:-}"
    eval "_rp_label=\${PPLX_PROFILE_${_rp_upper}_LABEL:-}"

    if [ -z "$_rp_domains" ] && [ -z "$_rp_recency" ] && [ -z "$_rp_context" ]; then
        die "Unknown profile '$_rp_name'" \
            "Define PPLX_PROFILE_${_rp_upper}_DOMAINS in config/.env. Registered profiles: ${PPLX_PROFILES:-none}."
    fi

    # Explicit CLI flags always win over the profile.
    [ -n "$PPLX_ARG_DOMAINS" ] || PPLX_ARG_DOMAINS="$_rp_domains"
    [ -n "$PPLX_ARG_RECENCY" ] || PPLX_ARG_RECENCY="$_rp_recency"
    [ -n "$PPLX_ARG_CONTEXT_SIZE" ] || PPLX_ARG_CONTEXT_SIZE="$_rp_context"
    PPLX_PROFILE_LABEL="$_rp_label"
}

# resolve_model_defaults FALLBACK_PRESET
# Precedence for who runs the request: CLI flags > config/.env > the calling
# script's own fallback preset. Without this, a script with a fallback would
# silently ignore PPLX_MODEL / PPLX_PRESET and bill the user for another model.
resolve_model_defaults() {
    if [ -n "$PPLX_ARG_PRESET" ] || [ -n "$PPLX_ARG_MODEL" ]; then
        return 0
    fi

    PPLX_ARG_MODEL="$PPLX_MODEL"
    if [ -n "${PPLX_PRESET_EXPLICIT:-}" ]; then
        PPLX_ARG_PRESET="$PPLX_PRESET"
    elif [ -z "$PPLX_ARG_MODEL" ]; then
        # No configuration at all — use what this script is tuned for.
        PPLX_ARG_PRESET="$1"
    fi
}

# --- Request builders -------------------------------------------------

# build_agent_body OUT_FILE — POST /v1/agent
build_agent_body() {
    _INPUT="$PPLX_ARG_INPUT" \
    _PRESET="$PPLX_ARG_PRESET" \
    _MODEL="$PPLX_ARG_MODEL" \
    _TOOLS="$PPLX_ARG_TOOLS" \
    _INSTRUCTIONS="$PPLX_ARG_INSTRUCTIONS" \
    _DOMAINS="$PPLX_ARG_DOMAINS" \
    _RECENCY="$PPLX_ARG_RECENCY" \
    _AFTER="$PPLX_ARG_AFTER" \
    _BEFORE="$PPLX_ARG_BEFORE" \
    _UPD_AFTER="$PPLX_ARG_UPDATED_AFTER" \
    _UPD_BEFORE="$PPLX_ARG_UPDATED_BEFORE" \
    _CONTEXT="$PPLX_ARG_CONTEXT_SIZE" \
    _MAX_RESULTS="$PPLX_ARG_MAX_RESULTS" \
    _COUNTRY="$PPLX_ARG_COUNTRY" \
    _LANGUAGE="$PPLX_ARG_LANGUAGE" \
    _MAX_OUT="$PPLX_ARG_MAX_OUTPUT_TOKENS" \
    _SCHEMA="$PPLX_ARG_SCHEMA_FILE" \
    _BACKGROUND="$PPLX_ARG_BACKGROUND" \
    python3 - > "$1" <<'PY'
import json, os, sys


def env(name):
    return os.environ.get(name, "").strip()


def split_list(raw):
    return [p.strip() for p in raw.split(",") if p.strip()]


body = {"input": env("_INPUT")}
if not body["input"]:
    print("empty input for /v1/agent", file=sys.stderr)
    sys.exit(1)

preset, model = env("_PRESET"), env("_MODEL")
if preset:
    body["preset"] = preset
if model:
    body["model"] = model
if not preset and not model:
    body["preset"] = "medium"

if env("_INSTRUCTIONS"):
    body["instructions"] = env("_INSTRUCTIONS")
# PPLX_LANGUAGE doubles as the Search API's language *filter* (a list), but the
# Agent API takes a single ISO 639-1 code — keep the first one.
languages = split_list(env("_LANGUAGE"))
if languages:
    body["language_preference"] = languages[0]
if env("_BACKGROUND") == "1":
    body["background"] = True

max_out = env("_MAX_OUT")
if not max_out and model.startswith("anthropic/"):
    # The Agent API rejects anthropic/* models without an explicit cap.
    max_out = "8192"
if max_out:
    body["max_output_tokens"] = int(max_out)

# --- web_search tool config ---
filters = {}
domains = split_list(env("_DOMAINS"))
if domains:
    allow = [d for d in domains if not d.startswith("-")]
    deny = [d for d in domains if d.startswith("-")]
    if allow and deny:
        print(
            "search_domain_filter works as an allowlist OR a denylist, not both; "
            "drop the '-' prefixes or remove the plain domains",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(domains) > 20:
        print("search_domain_filter accepts at most 20 domains", file=sys.stderr)
        sys.exit(1)
    filters["search_domain_filter"] = domains

recency = env("_RECENCY")
dated = {
    "search_after_date_filter": env("_AFTER"),
    "search_before_date_filter": env("_BEFORE"),
    "last_updated_after_filter": env("_UPD_AFTER"),
    "last_updated_before_filter": env("_UPD_BEFORE"),
}
dated = {k: v for k, v in dated.items() if v}
if recency and dated:
    print("use either --recency or explicit --after/--before dates, not both", file=sys.stderr)
    sys.exit(1)
if recency:
    filters["search_recency_filter"] = recency
filters.update(dated)

web_search = {"type": "web_search"}
if filters:
    web_search["filters"] = filters
if env("_CONTEXT"):
    web_search["search_context_size"] = env("_CONTEXT")
if env("_MAX_RESULTS"):
    web_search["max_results"] = int(env("_MAX_RESULTS"))
if env("_COUNTRY"):
    web_search["user_location"] = {"country": env("_COUNTRY").upper()}

tools = []
for name in split_list(env("_TOOLS")) or ["web_search"]:
    tools.append(dict(web_search) if name == "web_search" else {"type": name})
body["tools"] = tools

schema_path = env("_SCHEMA")
if schema_path:
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    # Accept either a bare JSON Schema or a ready-made response_format object.
    if schema.get("type") == "json_schema" and "json_schema" in schema:
        body["response_format"] = schema
    else:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "schema": schema},
        }

json.dump(body, sys.stdout, ensure_ascii=False)
PY
}

# build_search_body OUT_FILE — POST /search
build_search_body() {
    # Hand each query to python in its own variable so embedded newlines survive.
    _bsb_i=1
    while [ "$_bsb_i" -le "$PPLX_ARG_QUERY_COUNT" ]; do
        eval "export _PPLX_Q$_bsb_i=\"\$PPLX_ARG_QUERY_$_bsb_i\""
        _bsb_i=$((_bsb_i + 1))
    done

    _bsb_status=0
    _Q_COUNT="$PPLX_ARG_QUERY_COUNT" \
    _DOMAINS="$PPLX_ARG_DOMAINS" \
    _RECENCY="$PPLX_ARG_RECENCY" \
    _AFTER="$PPLX_ARG_AFTER" \
    _BEFORE="$PPLX_ARG_BEFORE" \
    _UPD_AFTER="$PPLX_ARG_UPDATED_AFTER" \
    _UPD_BEFORE="$PPLX_ARG_UPDATED_BEFORE" \
    _CONTEXT="$PPLX_ARG_CONTEXT_SIZE" \
    _MAX_RESULTS="$PPLX_ARG_MAX_RESULTS" \
    _COUNTRY="$PPLX_ARG_COUNTRY" \
    _LANGUAGE="$PPLX_ARG_LANGUAGE" \
    python3 - > "$1" <<'PY' || _bsb_status=$?
import json, os, sys


def env(name):
    return os.environ.get(name, "").strip()


def split_list(raw):
    return [p.strip() for p in raw.split(",") if p.strip()]


# One variable per query: a query may contain newlines and must stay intact.
queries = []
for i in range(1, int(env("_Q_COUNT") or 0) + 1):
    q = os.environ.get(f"_PPLX_Q{i}", "").strip()
    if q:
        queries.append(q)

if not queries:
    print("no query given", file=sys.stderr)
    sys.exit(1)
if len(queries) > 5:
    print("the Search API accepts at most 5 queries per request", file=sys.stderr)
    sys.exit(1)

body = {"query": queries[0] if len(queries) == 1 else queries}

max_results = env("_MAX_RESULTS")
if max_results:
    n = int(max_results)
    if not 1 <= n <= 20:
        print("--max-results must be between 1 and 20 for the Search API", file=sys.stderr)
        sys.exit(1)
    body["max_results"] = n

if env("_CONTEXT"):
    body["search_context_size"] = env("_CONTEXT")
if env("_COUNTRY"):
    body["country"] = env("_COUNTRY").upper()
if env("_LANGUAGE"):
    body["search_language_filter"] = split_list(env("_LANGUAGE"))

domains = split_list(env("_DOMAINS"))
if domains:
    allow = [d for d in domains if not d.startswith("-")]
    deny = [d for d in domains if d.startswith("-")]
    if allow and deny:
        print(
            "search_domain_filter works as an allowlist OR a denylist, not both; "
            "drop the '-' prefixes or remove the plain domains",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(domains) > 20:
        print("search_domain_filter accepts at most 20 domains", file=sys.stderr)
        sys.exit(1)
    body["search_domain_filter"] = domains

recency = env("_RECENCY")
dated = {
    "search_after_date_filter": env("_AFTER"),
    "search_before_date_filter": env("_BEFORE"),
    "last_updated_after_filter": env("_UPD_AFTER"),
    "last_updated_before_filter": env("_UPD_BEFORE"),
}
dated = {k: v for k, v in dated.items() if v}
if recency and dated:
    print("use either --recency or explicit --after/--before dates, not both", file=sys.stderr)
    sys.exit(1)
if recency:
    body["search_recency_filter"] = recency
body.update(dated)

json.dump(body, sys.stdout, ensure_ascii=False)
PY

    _bsb_i=1
    while [ "$_bsb_i" -le "$PPLX_ARG_QUERY_COUNT" ]; do
        unset "_PPLX_Q$_bsb_i"
        _bsb_i=$((_bsb_i + 1))
    done

    return "$_bsb_status"
}

# --- Response renderers -----------------------------------------------

# render_agent_response JSON_FILE MD_FILE [TITLE]
# Writes the full markdown report and prints the source count to stderr.
render_agent_response() {
    _J="$1" _M="$2" _TITLE="${3:-$PPLX_ARG_INPUT}" python3 - <<'PY'
import json, os, sys

with open(os.environ["_J"], encoding="utf-8") as fh:
    data = json.load(fh)

if data.get("status") == "failed":
    err = data.get("error") or {}
    print(f"Agent run failed: {err.get('message', err) or 'unknown error'}", file=sys.stderr)
    sys.exit(1)

text_parts, sources, queries, extras = [], [], [], []
for item in data.get("output", []) or []:
    kind = item.get("type")
    if kind == "message":
        for chunk in item.get("content", []) or []:
            if chunk.get("type") == "output_text" and chunk.get("text"):
                text_parts.append(chunk["text"])
    elif kind == "search_results":
        queries.extend(item.get("queries", []) or [])
        sources.extend(item.get("results", []) or [])
    elif kind == "fetch_url_results":
        for res in item.get("results", []) or []:
            extras.append(("fetched", res))
    elif kind in ("finance_results", "people_search_results", "sandbox_results"):
        extras.append((kind, item))

answer = "\n".join(text_parts).strip()

seen, uniq = set(), []
for src in sources:
    url = src.get("url") or ""
    if url and url in seen:
        continue
    seen.add(url)
    uniq.append(src)

usage = data.get("usage") or {}
cost = (usage.get("cost") or {}).get("total_cost")

title = " ".join(os.environ.get("_TITLE", "").split()) or "Perplexity answer"
lines = ["# " + title[:200]]
meta = [f"model: `{data.get('model', '?')}`", f"status: {data.get('status', '?')}"]
if usage.get("total_tokens"):
    meta.append(f"tokens: {usage['total_tokens']}")
if cost is not None:
    meta.append(f"cost: ${cost:.4f}")
lines += ["", " · ".join(meta), ""]
if queries:
    lines += ["_search queries: " + "; ".join(dict.fromkeys(queries)) + "_", ""]
lines += [answer or "_(model returned no text)_", ""]

if uniq:
    lines += ["## Sources", ""]
    for i, src in enumerate(uniq, 1):
        title = " ".join((src.get("title") or "untitled").split())
        date = src.get("date") or ""
        lines.append(f"{i}. [{title}]({src.get('url', '')})" + (f" — {date}" if date else ""))
    lines.append("")
    lines += ["## Snippets", ""]
    for i, src in enumerate(uniq, 1):
        snippet = (src.get("snippet") or "").strip()
        if snippet:
            lines += [f"### [{i}] {src.get('url', '')}", "", snippet, ""]

for kind, payload in extras:
    lines += [f"## {kind}", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""]

with open(os.environ["_M"], "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines).rstrip() + "\n")

print(len(uniq))
PY
}

# render_search_response JSON_FILE TSV_FILE TXT_FILE
render_search_response() {
    _J="$1" _T="$2" _X="$3" python3 - <<'PY'
import json, os, sys
from urllib.parse import urlparse

with open(os.environ["_J"], encoding="utf-8") as fh:
    data = json.load(fh)

results = data.get("results", []) or []


def flat(value):
    return " ".join(str(value or "").split())


with open(os.environ["_T"], "w", encoding="utf-8") as tsv:
    tsv.write("n\tdate\tdomain\ttitle\turl\n")
    for i, res in enumerate(results, 1):
        url = res.get("url") or ""
        tsv.write(
            "\t".join([
                str(i),
                flat(res.get("date") or res.get("last_updated") or "-"),
                urlparse(url).netloc or "-",
                flat(res.get("title") or "untitled")[:160],
                url,
            ]) + "\n"
        )

with open(os.environ["_X"], "w", encoding="utf-8") as txt:
    for i, res in enumerate(results, 1):
        txt.write(f"### [{i}] {flat(res.get('title') or 'untitled')}\n")
        txt.write(f"url: {res.get('url', '')}\n")
        if res.get("date"):
            txt.write(f"date: {res['date']}\n")
        if res.get("last_updated"):
            txt.write(f"last_updated: {res['last_updated']}\n")
        txt.write("\n" + (res.get("snippet") or "").strip() + "\n\n")

print(len(results))
PY
}

# --- Output helpers ---------------------------------------------------

# print_head FILE [N] — first N lines, then a pointer to the full file.
print_head() {
    _ph_file="$1"
    _ph_n="${2:-${PPLX_PRINT_LIMIT:-30}}"
    [ -f "$_ph_file" ] || return 0

    head -n "$_ph_n" "$_ph_file"
    _ph_total=$(wc -l < "$_ph_file" | tr -d ' ')
    if [ "$_ph_total" -gt "$_ph_n" ] 2>/dev/null; then
        echo "... $((_ph_total - _ph_n)) more lines — read or grep: $_ph_file"
    fi
}
