#!/bin/sh
# Asynchronous web search via Yandex Cloud Search API v2
# Creates batch of operations, polls for completion, resumes on restart
#
# Usage:
#   web_search_async.sh --file queries.txt [--region-id 225]
#   web_search_async.sh --resume  (continue polling pending operations)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

check_prerequisites
load_config

# Defaults from config
REGION_ID=$(cfg_get "search.region_id" "225")
SEARCH_TYPE=$(cfg_get "search.search_type" "SEARCH_TYPE_RU")
FAMILY_MODE=$(cfg_get "search.family_mode" "FAMILY_MODE_MODERATE")
FIX_TYPO=$(cfg_get "search.fix_typo_mode" "FIX_TYPO_MODE_ON")
RESULTS_PER_PAGE=$(cfg_get "search.results_per_page" "10")
POLL_INTERVAL=$(cfg_get "async.poll_interval_minutes" "10")
MAX_WAIT=$(cfg_get "async.max_wait_minutes" "120")
BATCH_SIZE=$(cfg_get "async.batch_size" "10")

QUERIES_FILE=""
RESUME_MODE=0

while [ $# -gt 0 ]; do
    case $1 in
        --file|-f) QUERIES_FILE="$2"; shift 2 ;;
        --region-id|-r) REGION_ID="$2"; shift 2 ;;
        --results|-n) RESULTS_PER_PAGE="$2"; shift 2 ;;
        --search-type) SEARCH_TYPE="$2"; shift 2 ;;
        --poll-interval) POLL_INTERVAL="$2"; shift 2 ;;
        --max-wait) MAX_WAIT="$2"; shift 2 ;;
        --resume) RESUME_MODE=1; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Create directories
mkdir -p "$CACHE_DIR/ops"
mkdir -p "$CACHE_DIR/results"

# --- Helper: count pending operations ---
count_pending() {
    _pending=0
    for _op_file in "$CACHE_DIR/ops"/*.json; do
        [ -f "$_op_file" ] || continue
        _st=$(json_file_get "$_op_file" "status")
        if [ "$_st" = "pending" ] || [ "$_st" = "running" ]; then
            _pending=$((_pending + 1))
        fi
    done
    echo "$_pending"
}

# --- Helper: count completed operations ---
count_done() {
    _done=0
    for _op_file in "$CACHE_DIR/ops"/*.json; do
        [ -f "$_op_file" ] || continue
        _st=$(json_file_get "$_op_file" "status")
        if [ "$_st" = "done" ]; then
            _done=$((_done + 1))
        fi
    done
    echo "$_done"
}

# --- Helper: count total operations ---
count_total() {
    _total=0
    for _op_file in "$CACHE_DIR/ops"/*.json; do
        [ -f "$_op_file" ] || continue
        _total=$((_total + 1))
    done
    echo "$_total"
}

# --- Helper: submit a single async query ---
submit_query() {
    _aq_query="$1"
    _aq_hash=$(file_hash "$_aq_query")

    # Smart snippets живут только в синхронном API, поэтому здесь всегда 0.
    _body=$(build_search_body "$_aq_query" "$REGION_ID" "$RESULTS_PER_PAGE" "0" "0")

    _response=$(auth_request "POST" "$SEARCH_API_URL/v2/web/searchAsync" "$_body") || {
        echo "Error: Failed to submit async query: $_aq_query" >&2
        return 1
    }

    _op_id=$(echo "$_response" | json_stdin_get "id")
    if [ -z "$_op_id" ]; then
        echo "Error: No operation ID in response for: $_aq_query" >&2
        return 1
    fi

    # Save operation state (query via env to avoid injection)
    _now=$(python3 -c "import time; print(int(time.time()))")
    _YSA_QUERY="$_aq_query" python3 -c "
import json, os
op = {
    'operation_id': '$_op_id',
    'query': os.environ['_YSA_QUERY'],
    'region_id': '$REGION_ID',
    'created_at': $_now,
    'last_checked_at': $_now,
    'status': 'pending',
    'error': None,
    'result_hash': '$_aq_hash'
}
with open('$CACHE_DIR/ops/${_op_id}.json', 'w') as f:
    json.dump(op, f, indent=2, ensure_ascii=False)
"

    echo "  Submitted: $_aq_query -> op=$_op_id" >&2
}

# --- Helper: check and process a single operation ---
check_operation() {
    _co_file="$1"
    _co_op_id=$(json_file_get "$_co_file" "operation_id")

    _poll_resp=$(auth_request "GET" "$OPERATION_API_URL/$_co_op_id" "") || {
        echo "  Warning: Failed to poll operation $_co_op_id" >&2
        return 1
    }

    _co_done=$(echo "$_poll_resp" | json_stdin_get "done")
    _now=$(python3 -c "import time; print(int(time.time()))")

    if [ "$_co_done" = "True" ] || [ "$_co_done" = "true" ]; then
        # Extract rawData from response
        _raw_b64=$(echo "$_poll_resp" | python3 -c "
import json, sys
d = json.load(sys.stdin)
resp = d.get('response', {})
print(resp.get('rawData', ''))
")
        _result_hash=$(json_file_get "$_co_file" "result_hash")
        _query=$(json_file_get "$_co_file" "query")

        if [ -n "$_raw_b64" ]; then
            # Decode and parse
            echo "$_raw_b64" | b64_decode > "$CACHE_DIR/results/${_result_hash}.raw"
            parse_search_response "$CACHE_DIR/results/${_result_hash}.raw" > "$CACHE_DIR/results/${_result_hash}.json"
            # Асинхронный ответ инфоконтекстов не несёт, а пак от прошлого
            # синхронного поиска той же фразы лежит под тем же хэшем.
            drop_stale_pack "$_result_hash"

            # Update operation status
            python3 -c "
import json
with open('$_co_file') as f:
    op = json.load(f)
op['status'] = 'done'
op['last_checked_at'] = $_now
with open('$_co_file', 'w') as f:
    json.dump(op, f, indent=2, ensure_ascii=False)
"
            echo "  Done: $_query -> results/${_result_hash}.json" >&2
        else
            # Check for error in operation
            _error=$(echo "$_poll_resp" | python3 -c "
import json, sys
d = json.load(sys.stdin)
err = d.get('error', {})
print(err.get('message', 'Unknown error'))
" 2>/dev/null || echo "Unknown error")

            python3 -c "
import json
with open('$_co_file') as f:
    op = json.load(f)
op['status'] = 'error'
op['last_checked_at'] = $_now
op['error'] = '$_error'
with open('$_co_file', 'w') as f:
    json.dump(op, f, indent=2, ensure_ascii=False)
"
            echo "  Error: $_query -> $_error" >&2
        fi
    else
        # Still running, update last_checked_at
        python3 -c "
import json
with open('$_co_file') as f:
    op = json.load(f)
op['status'] = 'running'
op['last_checked_at'] = $_now
with open('$_co_file', 'w') as f:
    json.dump(op, f, indent=2, ensure_ascii=False)
"
    fi
}

# --- Main logic ---

# Check for pending operations (resume mode)
_existing_pending=$(count_pending)

if [ "$RESUME_MODE" -eq 1 ]; then
    if [ "$_existing_pending" -eq 0 ]; then
        echo "No pending operations to resume."
        echo "Done: $(count_done)/$(count_total)"
        exit 0
    fi
    echo "Resuming: $_existing_pending pending operations found."
elif [ -n "$QUERIES_FILE" ]; then
    # Check for existing pending (don't create duplicates)
    if [ "$_existing_pending" -gt 0 ]; then
        echo "WARNING: $_existing_pending pending operations already exist in cache/ops/" >&2
        echo "Use --resume to continue polling them, or delete cache/ops/ to start fresh." >&2
        echo "" >&2
        echo "Existing queries:" >&2
        for _ef in "$CACHE_DIR/ops"/*.json; do
            [ -f "$_ef" ] || continue
            _est=$(json_file_get "$_ef" "status")
            _eq=$(json_file_get "$_ef" "query")
            if [ "$_est" = "pending" ] || [ "$_est" = "running" ]; then
                echo "  [$_est] $_eq" >&2
            fi
        done
        exit 1
    fi

    if [ ! -f "$QUERIES_FILE" ]; then
        echo "Error: Queries file not found: $QUERIES_FILE" >&2
        exit 1
    fi

    # Ensure IAM token
    _token=$(get_cached_iam_token)
    if [ -z "$_token" ]; then
        echo "No valid IAM token. Generating..." >&2
        sh "$SCRIPT_DIR/iam_token_get.sh"
    fi

    # Smart snippets есть только в синхронном API — предупреждаем ровно там,
    # где человек мог их ждать: при отправке нового батча. На --resume и на
    # подсказке по использованию это была бы пустая ругань.
    if [ "$(cfg_get "search.smart_snippets.enabled" "true")" = "true" ]; then
        echo "Note: smart snippets are sync-only — this batch returns links and short snippets." >&2
        echo "      Need page extracts? Use web_search_sync.sh --file instead." >&2
    fi

    echo "=== Async Search: Submitting queries ==="
    echo "NOTE: This may take minutes to hours. The script will poll every $POLL_INTERVAL minutes."
    echo "Max wait: $MAX_WAIT minutes. Use --resume to continue if interrupted."
    echo ""

    # Submit queries in batches
    _submitted=0
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(echo "$_line" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
        if [ -n "$_line" ]; then
            submit_query "$_line" || true
            _submitted=$((_submitted + 1))

            # Batch pause to respect rate limits
            if [ $((_submitted % BATCH_SIZE)) -eq 0 ]; then
                echo "  Batch of $BATCH_SIZE submitted, pausing 2s..." >&2
                sleep 2
            fi
        fi
    done < "$QUERIES_FILE"

    echo ""
    echo "Submitted $_submitted queries. Starting polling..."
    echo ""
else
    echo "Usage: web_search_async.sh --file queries.txt [options]"
    echo "       web_search_async.sh --resume"
    echo ""
    echo "Options:"
    echo "  --file, -f         File with queries (one per line)"
    echo "  --region-id, -r    Region ID (default: $REGION_ID)"
    echo "  --results, -n      Results per page (default: $RESULTS_PER_PAGE)"
    echo "  --poll-interval    Poll interval in minutes (default: $POLL_INTERVAL)"
    echo "  --max-wait         Max wait in minutes (default: $MAX_WAIT)"
    echo "  --resume           Resume polling pending operations"
    exit 1
fi

# --- Polling loop ---

_poll_seconds=$((POLL_INTERVAL * 60))
_max_seconds=$((MAX_WAIT * 60))
_start_time=$(python3 -c "import time; print(int(time.time()))")
_poll_count=0

while true; do
    _pending=$(count_pending)
    _done_count=$(count_done)
    _total_count=$(count_total)

    if [ "$_pending" -eq 0 ]; then
        echo ""
        echo "=== All operations complete ==="
        echo "Done: $_done_count/$_total_count"
        echo ""

        # Print summary
        echo "Results:"
        for _rf in "$CACHE_DIR/ops"/*.json; do
            [ -f "$_rf" ] || continue
            _rq=$(json_file_get "$_rf" "query")
            _rs=$(json_file_get "$_rf" "status")
            _rh=$(json_file_get "$_rf" "result_hash")
            if [ "$_rs" = "done" ]; then
                echo "  OK: $_rq -> cache/results/${_rh}.json"
            else
                _re=$(json_file_get "$_rf" "error")
                echo "  FAIL: $_rq -> $_re"
            fi
        done
        exit 0
    fi

    # Check timeout
    _now=$(python3 -c "import time; print(int(time.time()))")
    _elapsed=$((_now - _start_time))
    if [ "$_elapsed" -ge "$_max_seconds" ]; then
        echo ""
        echo "=== Timeout: max_wait_minutes ($MAX_WAIT) exceeded ==="
        echo "Done: $_done_count/$_total_count | Pending: $_pending"
        echo ""
        echo "Pending queries:"
        for _pf in "$CACHE_DIR/ops"/*.json; do
            [ -f "$_pf" ] || continue
            _ps=$(json_file_get "$_pf" "status")
            _pq=$(json_file_get "$_pf" "query")
            if [ "$_ps" = "pending" ] || [ "$_ps" = "running" ]; then
                echo "  $_pq"
            fi
        done
        echo ""
        echo "To continue polling, run:"
        echo "  bash scripts/web_search_async.sh --resume"
        exit 0
    fi

    _poll_count=$((_poll_count + 1))
    _remaining_min=$(( (_max_seconds - _elapsed) / 60 ))
    echo "--- Poll #$_poll_count | Done: $_done_count/$_total_count | Pending: $_pending | Timeout in: ${_remaining_min}min ---"

    # Check each pending operation
    for _cf in "$CACHE_DIR/ops"/*.json; do
        [ -f "$_cf" ] || continue
        _cs=$(json_file_get "$_cf" "status")
        if [ "$_cs" = "pending" ] || [ "$_cs" = "running" ]; then
            check_operation "$_cf" || true
        fi
    done

    # Check if all done after this poll
    _pending_after=$(count_pending)
    if [ "$_pending_after" -eq 0 ]; then
        continue
    fi

    echo "Sleeping ${POLL_INTERVAL} minutes until next poll..." >&2
    sleep "$_poll_seconds"
done
