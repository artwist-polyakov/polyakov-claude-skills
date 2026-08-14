#!/bin/sh
# Signal handling: a POSIX sh trap returns to where the script was interrupted
# unless it exits. `trap CMD EXIT INT TERM` therefore cleans up and then keeps
# going — which, in research.sh, means releasing the per-key lock and carrying
# on to submit, letting a waiting sibling start a second billable run.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
SCRIPTS="$SKILL_DIR/scripts"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_signal_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- the semantics themselves, demonstrated on a throwaway script ---
cat > "$TMP_DIR/handled.sh" <<'EOF'
#!/bin/sh
trap 'echo CLEANUP' EXIT
trap 'exit 143' TERM
sleep 5
echo RESUMED
EOF

sh "$TMP_DIR/handled.sh" > "$TMP_DIR/handled.out" 2>&1 &
VICTIM=$!
sleep 1
kill -TERM "$VICTIM" 2>/dev/null || true
RC=0
wait "$VICTIM" 2>/dev/null || RC=$?

grep -q CLEANUP "$TMP_DIR/handled.out" || { echo "the EXIT trap did not run"; exit 1; }
if grep -q RESUMED "$TMP_DIR/handled.out"; then
    echo "the script resumed after TERM instead of exiting"
    exit 1
fi
[ "$RC" != "0" ] || { echo "a signalled script must not exit 0 (got $RC)"; exit 1; }

# --- and that no script has slipped back to the resuming form ---
# Every entry point holds either a temp file or a lock when a signal can arrive.
# This file names the offending form in its own prose, so exclude it from the scan.
OFFENDERS=$(grep -lE "trap .* EXIT[[:space:]]+INT[[:space:]]+TERM" \
    "$SCRIPTS"/*.sh "$TESTS_DIR"/*.sh 2>/dev/null \
    | grep -v '/test_signals\.sh$' || true)
if [ -n "$OFFENDERS" ]; then
    echo "these still clean up and then resume on a signal:"
    printf '%s\n' "$OFFENDERS"
    echo "use: trap CLEANUP EXIT; trap 'exit 130' INT; trap 'exit 143' TERM"
    exit 1
fi

for s in search ask research fetch_url; do
    grep -q "trap 'exit 130' INT" "$SCRIPTS/$s.sh" || { echo "$s.sh has no exiting INT trap"; exit 1; }
    grep -q "trap 'exit 143' TERM" "$SCRIPTS/$s.sh" || { echo "$s.sh has no exiting TERM trap"; exit 1; }
done

echo PASS
