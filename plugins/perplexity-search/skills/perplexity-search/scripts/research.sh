#!/bin/sh
# research.sh — long-running deep research via the Agent API in background mode.
# Submits POST /v1/agent with background=true, then polls GET /v1/agent/{id}
# until the run reaches a terminal status. Survives disconnects: the response id
# is stored in cache/research/, so --resume picks a run back up.
#
# Expect minutes and real money per run — prefer ask.sh for ordinary questions.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: sh scripts/research.sh --query "research brief" [options]
       sh scripts/research.sh --resume resp_abc123

  --query, -q TEXT       what to research (required unless --resume)
  --preset NAME          high | xhigh | wide-research (default high)
  --model ID             override the preset model
  --tools LIST           default web_search,fetch_url
  --instructions TEXT    system-level guidance (e.g. report structure)
  --schema FILE          JSON Schema file → structured JSON report
  --max-output-tokens N  response cap (auto-set to 8192 for anthropic/* models)
  --recency VALUE        hour | day | week | month | year
  --after DATE           published on/after (YYYY-MM-DD or MM/DD/YYYY)
  --before DATE          published on/before
  --domains LIST         allowlist "sec.gov,nature.com" OR denylist "-reddit.com"
  --country CC           ISO 3166-1 alpha-2
  --language CODE        ISO 639-1 report language
  --context-size SIZE    low | medium | high
  --max-results N        web_search results per call (1..50)
  --profile NAME         apply PPLX_PROFILE_<NAME>_* defaults from config/.env
  --poll SECONDS         polling interval (default 15)
  --timeout SECONDS      give up waiting (default 1800); the run keeps going
  --no-wait              submit and print the response id, then exit
  --resume ID            poll an already-submitted response id
  --limit N              stdout lines of the report (default PPLX_PRINT_LIMIT=30)
  --cache-ttl SECONDS    reuse an identical finished run (default 86400)
  --no-cache             always start a new run
EOF
}

handle_help_flag "$@"
load_config

LIMIT=""
NO_CACHE=""
CACHE_TTL=""
PROFILE=""
POLL="15"
TIMEOUT="1800"
NO_WAIT=""
RESUME_ID=""
RESPONSE_ID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --query|-q)          require_value "$1" $#; PPLX_ARG_INPUT="$2"; shift 2 ;;
        --preset)            require_value "$1" $#; PPLX_ARG_PRESET="$2"; shift 2 ;;
        --model)             require_value "$1" $#; PPLX_ARG_MODEL="$2"; shift 2 ;;
        --tools)             require_value "$1" $#; PPLX_ARG_TOOLS="$2"; shift 2 ;;
        --instructions)      require_value "$1" $#; PPLX_ARG_INSTRUCTIONS="$2"; shift 2 ;;
        --schema)            require_value "$1" $#; PPLX_ARG_SCHEMA_FILE="$2"; shift 2 ;;
        --max-output-tokens) require_value "$1" $#; PPLX_ARG_MAX_OUTPUT_TOKENS="$2"; shift 2 ;;
        --recency)           require_value "$1" $#; PPLX_ARG_RECENCY="$2"; shift 2 ;;
        --after)             require_value "$1" $#; PPLX_ARG_AFTER="$2"; shift 2 ;;
        --before)            require_value "$1" $#; PPLX_ARG_BEFORE="$2"; shift 2 ;;
        --updated-after)     require_value "$1" $#; PPLX_ARG_UPDATED_AFTER="$2"; shift 2 ;;
        --updated-before)    require_value "$1" $#; PPLX_ARG_UPDATED_BEFORE="$2"; shift 2 ;;
        --domains)           require_value "$1" $#; PPLX_ARG_DOMAINS="$2"; shift 2 ;;
        --country)           require_value "$1" $#; PPLX_ARG_COUNTRY="$2"; shift 2 ;;
        --language)          require_value "$1" $#; PPLX_ARG_LANGUAGE="$2"; shift 2 ;;
        --context-size)      require_value "$1" $#; PPLX_ARG_CONTEXT_SIZE="$2"; shift 2 ;;
        --max-results)       require_value "$1" $#; PPLX_ARG_MAX_RESULTS="$2"; shift 2 ;;
        --profile)           require_value "$1" $#; PROFILE="$2"; shift 2 ;;
        --poll)              require_value "$1" $#; POLL="$2"; shift 2 ;;
        --timeout)           require_value "$1" $#; TIMEOUT="$2"; shift 2 ;;
        --resume)            require_value "$1" $#; RESUME_ID="$2"; shift 2 ;;
        --limit)             require_value "$1" $#; LIMIT="$2"; shift 2 ;;
        --cache-ttl)         require_value "$1" $#; CACHE_TTL="$2"; shift 2 ;;
        --no-wait)           NO_WAIT=1; shift ;;
        --no-cache)          NO_CACHE=1; shift ;;
        -h|--help)           usage; exit 0 ;;
        *)                   usage >&2; die "Unknown option: $1" ;;
    esac
done

LIMIT="${LIMIT:-$PPLX_PRINT_LIMIT}"
require_uint "--poll" "$POLL" 1 3600
require_uint "--timeout" "$TIMEOUT" 0
require_uint "--limit" "$LIMIT" 1
[ -z "$CACHE_TTL" ] || require_uint "--cache-ttl" "$CACHE_TTL"
[ -z "$RESUME_ID" ] || validate_response_id "$RESUME_ID"

OUT_DIR="$PPLX_CACHE_DIR/research"
mkdir -p "$OUT_DIR"

# json_str FILE KEY — print a top-level string field, empty if absent
json_str() {
    _J="$1" _K="$2" python3 - <<'PY'
import json, os
try:
    with open(os.environ["_J"], encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    data = {}
value = data.get(os.environ["_K"], "") if isinstance(data, dict) else ""
print(value if isinstance(value, str) else "")
PY
}

# poll_until_terminal RESPONSE_ID JSON_FILE — returns the terminal status
poll_until_terminal() {
    _pt_id="$1"
    _pt_out="$2"
    _pt_waited=0
    _pt_last=""

    while :; do
        pplx_get "/v1/agent/$_pt_id" "$_pt_out"
        _pt_status=$(json_str "$_pt_out" status)
        [ -n "$_pt_status" ] || _pt_status="unknown"

        if [ "$_pt_status" != "$_pt_last" ]; then
            echo "status: $_pt_status (${_pt_waited}s elapsed)" >&2
            _pt_last="$_pt_status"
        fi

        case "$_pt_status" in
            completed|failed|cancelled|incomplete)
                printf '%s' "$_pt_status"
                return 0
                ;;
        esac

        if [ "$_pt_waited" -ge "$TIMEOUT" ]; then
            printf '%s' "timeout"
            return 0
        fi
        sleep "$POLL"
        _pt_waited=$((_pt_waited + POLL))
    done
}

if [ -n "$RESUME_ID" ]; then
    RESPONSE_ID="$RESUME_ID"
    QUERY_LABEL="resume $RESUME_ID"
    # Reuse the cache slot of the original submission when we still have its
    # <key>.id marker, so a resumed run fills the same entry a waiting run would.
    KEY=""
    for _id_file in "$OUT_DIR"/*.id; do
        [ -f "$_id_file" ] || continue
        if [ "$(cat "$_id_file")" = "$RESUME_ID" ]; then
            KEY=$(basename "$_id_file" .id)
            break
        fi
    done
    [ -n "$KEY" ] || KEY=$(cache_key "research-resume|$RESUME_ID")
else
    [ -n "$PPLX_ARG_INPUT" ] || { usage >&2; die "--query is required (or use --resume ID)"; }
    if [ -n "$PPLX_ARG_SCHEMA_FILE" ] && [ ! -f "$PPLX_ARG_SCHEMA_FILE" ]; then
        die "Schema file not found: $PPLX_ARG_SCHEMA_FILE"
    fi

    resolve_profile "$PROFILE"

    resolve_model_defaults "high"
    PPLX_ARG_TOOLS="${PPLX_ARG_TOOLS:-web_search,fetch_url}"
    PPLX_ARG_CONTEXT_SIZE="${PPLX_ARG_CONTEXT_SIZE:-$PPLX_CONTEXT_SIZE}"
    PPLX_ARG_COUNTRY="${PPLX_ARG_COUNTRY:-$PPLX_COUNTRY}"
    PPLX_ARG_LANGUAGE="${PPLX_ARG_LANGUAGE:-$PPLX_LANGUAGE}"
    PPLX_ARG_BACKGROUND="1"

    validate_tools "$PPLX_ARG_TOOLS"
    validate_recency "$PPLX_ARG_RECENCY"
    validate_context_size "$PPLX_ARG_CONTEXT_SIZE"
    PPLX_ARG_AFTER=$(normalize_date "$PPLX_ARG_AFTER")
    PPLX_ARG_BEFORE=$(normalize_date "$PPLX_ARG_BEFORE")
    PPLX_ARG_UPDATED_AFTER=$(normalize_date "$PPLX_ARG_UPDATED_AFTER")
    PPLX_ARG_UPDATED_BEFORE=$(normalize_date "$PPLX_ARG_UPDATED_BEFORE")

    BODY_FILE="$PPLX_TMPDIR/pplx_research_body.$$.json"
    LOCK_DIR=""
    trap 'rm -f "$BODY_FILE"; lock_release "$LOCK_DIR"' EXIT INT TERM
    build_agent_body "$BODY_FILE"

    KEY=$(cache_key "research|$(cat "$BODY_FILE")")
    QUERY_LABEL="$PPLX_ARG_INPUT"
    JSON_FILE="$OUT_DIR/$KEY.json"

    if [ -n "$CACHE_TTL" ]; then
        EFFECTIVE_TTL="$CACHE_TTL"
    else
        EFFECTIVE_TTL=$(effective_cache_ttl "$PPLX_RESEARCH_CACHE_TTL" "$PPLX_ARG_RECENCY")
    fi

    # A finished identical run is worth reusing — these are the expensive calls.
    if [ -z "$NO_CACHE" ] && cache_fresh "$JSON_FILE" "$EFFECTIVE_TTL" &&
       [ "$(json_str "$JSON_FILE" status)" = "completed" ]; then
        MD_FILE="$OUT_DIR/$KEY.md"
        SOURCES=$(render_agent_response "$JSON_FILE" "$MD_FILE" "$QUERY_LABEL")
        echo "=== Perplexity Research (cache, $(format_age "$(cache_age_seconds "$JSON_FILE")") old) ==="
        echo "sources: $SOURCES"
        echo "re-run live with --no-cache if the facts may have moved"
        print_head "$MD_FILE" "$LIMIT"
        echo ""
        echo "full report: $MD_FILE"
        echo "raw json:    $JSON_FILE"
        exit 0
    fi

    # Everything from here to the marker write is one critical section: probing
    # for a reusable run and then submitting one is check-then-act, and two
    # overlapping invocations of the same query would otherwise both find
    # nothing and both pay. The lock is released before polling starts, so a
    # waiting sibling gets in quickly and adopts the run we just recorded.
    LOCK_DIR="$OUT_DIR/$KEY.lock"
    if ! lock_acquire "$LOCK_DIR" 120; then
        LOCK_DIR=""
        die "Another invocation is already starting this query and did not finish in time" \
            "Re-run in a moment: it will adopt that run instead of paying for a second one."
    fi

    # An identical run may still be in flight — from an earlier --no-wait, or
    # from an invocation that stopped waiting while the job kept going. Without
    # this the cache misses (no completed .json yet) and we would pay for a
    # second minutes-long run and orphan the first by overwriting its marker.
    #
    # The probe writes to its own scratch file, never to JSON_FILE: refreshing
    # the cached report's timestamp here would silently restart its TTL.
    ID_FILE="$OUT_DIR/$KEY.id"
    PROBE_FILE="$OUT_DIR/$KEY.probe.json"
    if [ -z "$NO_CACHE" ] && [ -s "$ID_FILE" ]; then
        PREV_ID=$(cat "$ID_FILE")
        if ( validate_response_id "$PREV_ID" ) >/dev/null 2>&1; then
            PROBE_HTTP=$(pplx_probe_get "/v1/agent/$PREV_ID" "$PROBE_FILE")
            case "$PROBE_HTTP" in
                2[0-9][0-9])
                    PREV_STATUS=$(json_str "$PROBE_FILE" status)
                    if adoptable_status "$PREV_STATUS"; then
                        if [ "$PREV_STATUS" = "completed" ]; then
                            # A finished run is still just a cached report, so it
                            # has to satisfy the same TTL as one — otherwise an
                            # expired report would be re-blessed forever.
                            PREV_AGE=$(response_age_seconds "$PROBE_FILE" 2>/dev/null || true)
                            if [ -z "$PREV_AGE" ]; then
                                # No timestamp from the API: fall back to when we
                                # submitted it, which is what the marker records.
                                PREV_AGE=$(cache_age_seconds "$ID_FILE" 2>/dev/null || echo "")
                            fi
                            if [ -n "$PREV_AGE" ] && [ "$PREV_AGE" -lt "$EFFECTIVE_TTL" ] 2>/dev/null; then
                                RESPONSE_ID="$PREV_ID"
                            fi
                        else
                            RESPONSE_ID="$PREV_ID"
                        fi
                    fi
                    ;;
                404|410)
                    : # The run is definitively gone; a fresh submission is right.
                    ;;
                *)
                    rm -f "$PROBE_FILE"
                    die "Cannot tell whether the run already submitted for this query is still active (probe returned HTTP $PROBE_HTTP)" \
                        "Refusing to start a second billable run on a guess. Retry in a moment, poll the existing one with --resume $PREV_ID, or force a new run with --no-cache."
                    ;;
            esac
            rm -f "$PROBE_FILE"
            if [ -n "$RESPONSE_ID" ]; then
                echo "reusing the run already submitted for this query: $RESPONSE_ID" >&2
            fi
        fi
    fi

    if [ -z "$RESPONSE_ID" ]; then
        SUBMIT_FILE="$OUT_DIR/$KEY.submit.json"
        pplx_post "/v1/agent" "$BODY_FILE" "$SUBMIT_FILE"
        RESPONSE_ID=$(json_str "$SUBMIT_FILE" id)
        [ -n "$RESPONSE_ID" ] || die "Agent API did not return a response id" \
            "$(head -c 500 "$SUBMIT_FILE" 2>/dev/null)"
        validate_response_id "$RESPONSE_ID"

        printf '%s\n' "$RESPONSE_ID" > "$ID_FILE"
        index_append "research" "$KEY" "$QUERY_LABEL" "$JSON_FILE"
        echo "submitted: $RESPONSE_ID" >&2
    fi

    # The id is recorded, so anyone waiting can adopt it now. Polling can take
    # half an hour and must not hold the lock.
    lock_release "$LOCK_DIR"
    LOCK_DIR=""

    if [ -n "$NO_WAIT" ]; then
        echo "=== Perplexity Research (submitted) ==="
        echo "response id: $RESPONSE_ID"
        echo "resume with: sh scripts/research.sh --resume $RESPONSE_ID"
        exit 0
    fi
fi

JSON_FILE="$OUT_DIR/$KEY.json"
MD_FILE="$OUT_DIR/$KEY.md"

STATUS=$(poll_until_terminal "$RESPONSE_ID" "$JSON_FILE")

case "$STATUS" in
    timeout)
        echo "=== Perplexity Research (still running) ==="
        echo "response id: $RESPONSE_ID"
        echo "gave up waiting after ${TIMEOUT}s — the run continues on Perplexity's side."
        echo "resume with: sh scripts/research.sh --resume $RESPONSE_ID"
        echo "last poll:   $JSON_FILE"
        exit 0
        ;;
    failed|cancelled)
        echo "=== Perplexity Research ($STATUS) ==="
        echo "response id: $RESPONSE_ID"
        echo "raw json:    $JSON_FILE"
        _ERR=$(_J="$JSON_FILE" python3 -c '
import json, os
with open(os.environ["_J"], encoding="utf-8") as fh:
    err = json.load(fh).get("error") or {}
print(err.get("message", "") if isinstance(err, dict) else str(err))
')
        [ -z "$_ERR" ] || echo "error: $_ERR"
        exit 1
        ;;
esac

SOURCES=$(render_agent_response "$JSON_FILE" "$MD_FILE" "$QUERY_LABEL")

echo "=== Perplexity Research ($STATUS) ==="
echo "response id: $RESPONSE_ID"
echo "sources: $SOURCES"
print_head "$MD_FILE" "$LIMIT"
echo ""
echo "full report: $MD_FILE"
echo "raw json:    $JSON_FILE"
