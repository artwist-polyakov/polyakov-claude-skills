#!/bin/sh
# Common functions for X Research skill
# Dependencies: curl, python3 (stdlib only)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SKILL_DIR/config/.env"
CACHE_DIR="$SKILL_DIR/cache"
RUNS_DIR="$CACHE_DIR/runs"
INDEX_FILE="$CACHE_DIR/index.jsonl"

XAI_API_URL="https://api.x.ai/v1/responses"
XAI_MODEL_FALLBACK="grok-4.20-reasoning"
MAX_HANDLES_PER_BATCH=10

# --------------- Prerequisites ---------------

check_python3() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: python3 is required but not found." >&2
        echo "Install Python 3.7+ and ensure python3 is in PATH." >&2
        exit 1
    fi
}

# --------------- Config ---------------

load_config() {
    check_python3
    if [ -f "$CONFIG_FILE" ]; then
        if [ -f "$SCRIPT_DIR/sanitize_env.sh" ]; then
            sh "$SCRIPT_DIR/sanitize_env.sh" "$CONFIG_FILE"
        fi
        _exports_file=$(mktemp "${TMPDIR:-/tmp}/xr_env.XXXXXX")
        if ! python3 - "$CONFIG_FILE" > "$_exports_file" <<'PY'
import pathlib
import re
import shlex
import sys

path = pathlib.Path(sys.argv[1])
key_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if not raw_line.strip() or raw_line.lstrip().startswith("#") or "=" not in raw_line:
        continue

    key, value = raw_line.split("=", 1)
    key = key.strip()
    if not key_pattern.fullmatch(key):
        continue

    value = value.strip()
    quoted = False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
        quoted = True

    print(f"export {key}={shlex.quote(value)}")

    if not quoted and any(ch.isspace() for ch in value):
        print(
            f"Warning: {path.name}:{lineno} {key} contains spaces without quotes. "
            "Quote the value to keep config/.env shell-compatible.",
            file=sys.stderr,
        )
PY
        then
            rm -f "$_exports_file"
            echo "Error: failed to parse $CONFIG_FILE." >&2
            exit 1
        fi
        # Source a sanitized export file instead of eval'ing raw .env content.
        # This keeps the loader tolerant of unquoted spaces and avoids executing config text.
        # shellcheck disable=SC1090
        . "$_exports_file"
        rm -f "$_exports_file"
    fi
    if [ -z "$XAI_API_KEY" ]; then
        echo "Error: XAI_API_KEY not set. Copy config/.env.example to config/.env and add your key." >&2
        exit 1
    fi
    XAI_MODEL="${XAI_MODEL:-grok-4-1-fast-reasoning}"
    X_DEFAULT_CATEGORY="${X_DEFAULT_CATEGORY:-ai}"
    mkdir -p "$RUNS_DIR"
}

# --------------- Input normalization ---------------

normalize_handle() {
    printf '%s' "$1" | sed 's/^@//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

normalize_handles() {
    _result=""
    _old_ifs="$IFS"
    IFS=','
    for _h in $1; do
        _h=$(normalize_handle "$_h")
        if [ -n "$_h" ]; then
            if [ -n "$_result" ]; then
                _result="$_result,$_h"
            else
                _result="$_h"
            fi
        fi
    done
    IFS="$_old_ifs"
    printf '%s' "$_result"
}

# --------------- Parameter parsing ---------------

parse_common_params() {
    ACCOUNTS=""
    CATEGORY=""
    PERIOD=""
    QUERY=""
    TOPICS=""
    LIMIT="30"
    PREFER_CACHE=""
    REFRESH=""
    URL=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --accounts)      ACCOUNTS="$2"; shift 2 ;;
            --category)      CATEGORY="$2"; shift 2 ;;
            --period)        PERIOD="$2"; shift 2 ;;
            --query)         QUERY="$2"; shift 2 ;;
            --topics)        TOPICS="$2"; shift 2 ;;
            --limit)         LIMIT="$2"; shift 2 ;;
            --url)           URL="$2"; shift 2 ;;
            --prefer-cache)  PREFER_CACHE="1"; shift ;;
            --refresh)       REFRESH="1"; shift ;;
            *)               shift ;;
        esac
    done
}

# --------------- Category resolution ---------------

# Validate category ID: only [A-Za-z0-9_] allowed (prevents shell injection via eval)
validate_category() {
    _input="$1"
    _clean=$(printf '%s' "$_input" | sed 's/[^A-Za-z0-9_]//g')
    if [ "$_input" != "$_clean" ] || [ -z "$_input" ]; then
        echo "Error: invalid category '$_input'. Only letters, digits, and underscores allowed." >&2
        exit 1
    fi
}

resolve_accounts() {
    if [ -n "$ACCOUNTS" ]; then
        ACCOUNTS=$(normalize_handles "$ACCOUNTS")
        return
    fi
    _cat="${CATEGORY:-$X_DEFAULT_CATEGORY}"
    validate_category "$_cat"
    _cat_upper=$(printf '%s' "$_cat" | tr '[:lower:]' '[:upper:]')
    eval "_accts=\${X_ACCOUNTS_${_cat_upper}:-}"
    if [ -n "$_accts" ]; then
        ACCOUNTS=$(normalize_handles "$_accts")
        CATEGORY="$_cat"
    else
        echo "Error: no accounts for category '$_cat'. Pass --accounts or configure X_ACCOUNTS_${_cat_upper} in .env." >&2
        exit 1
    fi
}

resolve_topics() {
    if [ -n "$TOPICS" ]; then
        return
    fi
    _cat="${CATEGORY:-$X_DEFAULT_CATEGORY}"
    validate_category "$_cat"
    _cat_upper=$(printf '%s' "$_cat" | tr '[:lower:]' '[:upper:]')
    eval "_topics=\${X_TOPICS_${_cat_upper}:-}"
    if [ -n "$_topics" ]; then
        TOPICS="$_topics"
        CATEGORY="$_cat"
    else
        echo "Error: no topics for category '$_cat'. Pass --topics or configure X_TOPICS_${_cat_upper} in .env." >&2
        exit 1
    fi
}

# --------------- Period handling ---------------

period_to_dates() {
    _period="${1:-1d}"
    _now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    _today=$(date -u +%Y-%m-%d)

    case "$_period" in
        1h)
            # 1 hour ago
            _from=$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "1 hour ago" +%Y-%m-%dT%H:%M:%SZ)
            ;;
        today)
            _from="${_today}T00:00:00Z"
            ;;
        yesterday)
            _yd=$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d "yesterday" +%Y-%m-%d)
            _from="${_yd}T00:00:00Z"
            _now_iso="${_today}T00:00:00Z"
            ;;
        week|7d)
            _wd=$(date -u -v-7d +%Y-%m-%d 2>/dev/null || date -u -d "7 days ago" +%Y-%m-%d)
            _from="${_wd}T00:00:00Z"
            ;;
        *d)
            _n=$(printf '%s' "$_period" | sed 's/d$//')
            _nd=$(date -u -v-"${_n}d" +%Y-%m-%d 2>/dev/null || date -u -d "$_n days ago" +%Y-%m-%d)
            _from="${_nd}T00:00:00Z"
            ;;
        *)
            _from="${_today}T00:00:00Z"
            ;;
    esac

    FROM_DATE="$_from"
    TO_DATE="$_now_iso"
}

# --------------- Grok API ---------------

# grok_search <prompt> [handles] [from_date] [to_date] [system_prompt]
# Writes response text to stdout, saves artifact.
# Sets: _RESPONSE_FILE, _ARTIFACT_FILE
grok_search() {
    _prompt="$1"
    _handles="${2:-}"
    _from_date="${3:-}"
    _to_date="${4:-}"
    _system_prompt="${5:-}"

    _body_file="${TMPDIR:-/tmp}/xr_body_$$.json"
    _response_file="${TMPDIR:-/tmp}/xr_response_$$.json"
    _headers_file="${TMPDIR:-/tmp}/xr_headers_$$.txt"
    trap 'rm -f "$_body_file" "$_response_file" "$_headers_file"' EXIT

    # Build request body via python3
    _XR_PROMPT="$_prompt" \
    _XR_MODEL="$XAI_MODEL" \
    _XR_HANDLES="$_handles" \
    _XR_FROM="$_from_date" \
    _XR_TO="$_to_date" \
    _XR_SYSTEM="$_system_prompt" \
    python3 -c "
import json, os
tool = {'type': 'x_search'}
h = os.environ.get('_XR_HANDLES', '')
if h:
    tool['allowed_x_handles'] = [x.strip() for x in h.split(',') if x.strip()]
fd = os.environ.get('_XR_FROM', '')
if fd:
    tool['from_date'] = fd
td = os.environ.get('_XR_TO', '')
if td:
    tool['to_date'] = td
messages = []
sys_p = os.environ.get('_XR_SYSTEM', '')
if sys_p:
    messages.append({'role': 'developer', 'content': sys_p})
messages.append({'role': 'user', 'content': os.environ['_XR_PROMPT']})
body = {
    'model': os.environ['_XR_MODEL'],
    'input': messages,
    'tools': [tool]
}
print(json.dumps(body, ensure_ascii=False))
" > "$_body_file"

    # API call
    _http_code=$(curl -s -o "$_response_file" -w '%{http_code}' \
        -X POST "$XAI_API_URL" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $XAI_API_KEY" \
        -d @"$_body_file")

    # Model fallback on 422/400
    if [ "$_http_code" = "422" ] || [ "$_http_code" = "400" ]; then
        _err_text=$(cat "$_response_file" 2>/dev/null || true)
        case "$_err_text" in
            *"model"*|*"not found"*|*"unavailable"*|*"not_found"*)
                echo "Warning: model '$XAI_MODEL' unavailable ($_http_code), trying fallback '$XAI_MODEL_FALLBACK'..." >&2
                XAI_MODEL="$XAI_MODEL_FALLBACK"
                _XR_PROMPT="$_prompt" \
                _XR_MODEL="$XAI_MODEL" \
                _XR_HANDLES="$_handles" \
                _XR_FROM="$_from_date" \
                _XR_TO="$_to_date" \
                _XR_SYSTEM="$_system_prompt" \
                python3 -c "
import json, os
tool = {'type': 'x_search'}
h = os.environ.get('_XR_HANDLES', '')
if h:
    tool['allowed_x_handles'] = [x.strip() for x in h.split(',') if x.strip()]
fd = os.environ.get('_XR_FROM', '')
if fd:
    tool['from_date'] = fd
td = os.environ.get('_XR_TO', '')
if td:
    tool['to_date'] = td
messages = []
sys_p = os.environ.get('_XR_SYSTEM', '')
if sys_p:
    messages.append({'role': 'developer', 'content': sys_p})
messages.append({'role': 'user', 'content': os.environ['_XR_PROMPT']})
body = {
    'model': os.environ['_XR_MODEL'],
    'input': messages,
    'tools': [tool]
}
print(json.dumps(body, ensure_ascii=False))
" > "$_body_file"
                _http_code=$(curl -s -o "$_response_file" -w '%{http_code}' \
                    -X POST "$XAI_API_URL" \
                    -H "Content-Type: application/json" \
                    -H "Authorization: Bearer $XAI_API_KEY" \
                    -d @"$_body_file")
                ;;
        esac
    fi

    # Check final status
    if [ "$_http_code" -lt 200 ] || [ "$_http_code" -ge 300 ]; then
        echo "Error: API returned HTTP $_http_code" >&2
        cat "$_response_file" >&2
        exit 1
    fi

    _RESPONSE_FILE="$_response_file"
}

# Extract text + citations from response JSON
# Usage: parse_response < response.json
parse_response() {
    python3 -c "
import json, sys
data = json.load(sys.stdin)
text_parts = []
citations = []
for item in data.get('output', []):
    if item.get('type') == 'message':
        for c in item.get('content', []):
            if c.get('type') == 'output_text':
                text_parts.append(c['text'])
            for ann in c.get('annotations', []):
                if ann.get('type') == 'url_citation':
                    citations.append(ann.get('url', ''))
text = '\\n'.join(text_parts)
print(text)
if citations:
    print('\\n--- Sources ---')
    for u in sorted(set(citations)):
        if u:
            print(u)
"
}

# Parse structured JSON items from response text
# Usage: parse_items < response.json → writes JSON to stdout
# Returns: {"items": [...], "ideas": [...], "summary": "...", "text": "...", "citations": [...]}
parse_structured_response() {
    python3 -c "
import json, sys
data = json.load(sys.stdin)
text_parts = []
citations = []
for item in data.get('output', []):
    if item.get('type') == 'message':
        for c in item.get('content', []):
            if c.get('type') == 'output_text':
                text_parts.append(c['text'])
            for ann in c.get('annotations', []):
                if ann.get('type') == 'url_citation':
                    citations.append(ann.get('url', ''))
raw_text = '\\n'.join(text_parts)
# Try to parse as structured JSON
try:
    structured = json.loads(raw_text)
    items = structured.get('items', [])
    ideas = structured.get('ideas', [])
    summary = structured.get('summary', '')
except (json.JSONDecodeError, AttributeError):
    # Fallback: plain text
    items = []
    ideas = []
    summary = ''
    print('Warning: could not parse structured JSON from model response, using plain text', file=sys.stderr)
result = {
    'items': items,
    'ideas': ideas,
    'summary': summary,
    'text': raw_text,
    'citations': sorted(set(c for c in citations if c))
}
print(json.dumps(result, ensure_ascii=False, indent=2))
"
}

# --------------- Artifact store ---------------

# generate short run ID (6 hex chars from PID + timestamp)
gen_run_id() {
    printf '%s%s' "$$" "$(date +%s)" | cksum | awk '{printf "%06x", $1 % 16777216}'
}

# save_artifact <script_name> <parsed_json> [extra_meta_json]
# Writes artifact to cache/runs/ and appends to index.jsonl
# Prints artifact path to stdout
save_artifact() {
    _script_name="$1"
    _parsed_json="$2"
    _extra_meta="${3:-\{\}}"
    _run_id=$(gen_run_id)
    _ts=$(date -u +%Y%m%d_%H%M%S)
    _ts_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    _artifact_file="$RUNS_DIR/${_script_name}_${_ts}_${_run_id}.json"

    # Merge meta + parsed data into artifact
    _XR_SCRIPT="$_script_name" \
    _XR_RUN_ID="$_run_id" \
    _XR_TS="$_ts_iso" \
    _XR_MODEL_USED="$XAI_MODEL" \
    _XR_EXTRA="$_extra_meta" \
    _XR_PARSED="$_parsed_json" \
    python3 -c "
import json, os, sys
parsed = json.loads(os.environ['_XR_PARSED'])
extra = json.loads(os.environ['_XR_EXTRA'])
meta = {
    'script': os.environ['_XR_SCRIPT'],
    'run_id': os.environ['_XR_RUN_ID'],
    'created_at': os.environ['_XR_TS'],
    'model': os.environ['_XR_MODEL_USED'],
}
meta.update(extra)
artifact = {'meta': meta}
artifact.update(parsed)
print(json.dumps(artifact, ensure_ascii=False, indent=2))
" > "$_artifact_file"

    # Append to index
    _XR_PATH="runs/${_script_name}_${_ts}_${_run_id}.json" \
    _XR_EXTRA="$_extra_meta" \
    _XR_SCRIPT="$_script_name" \
    _XR_RUN_ID="$_run_id" \
    _XR_TS="$_ts_iso" \
    _XR_MODEL_USED="$XAI_MODEL" \
    python3 -c "
import json, os
extra = json.loads(os.environ['_XR_EXTRA'])
entry = {
    'run_id': os.environ['_XR_RUN_ID'],
    'script': os.environ['_XR_SCRIPT'],
    'created_at': os.environ['_XR_TS'],
    'model': os.environ['_XR_MODEL_USED'],
    'path': os.environ['_XR_PATH'],
}
entry.update(extra)
print(json.dumps(entry, ensure_ascii=False))
" >> "$INDEX_FILE"

    printf '%s' "$_artifact_file"
}

# --------------- Output ---------------

print_head() {
    _n="${1:-30}"
    _total=0
    _printed=0
    _tmpcount="${TMPDIR:-/tmp}/xr_count_$$.txt"
    while IFS= read -r _line; do
        _total=$(( _total + 1 ))
        if [ "$_total" -le "$_n" ]; then
            printf '%s\n' "$_line"
            _printed=$(( _printed + 1 ))
        fi
    done
    if [ "$_total" -gt "$_n" ]; then
        echo "... $(( _total - _n )) more lines in artifact file"
    fi
    rm -f "$_tmpcount"
}
