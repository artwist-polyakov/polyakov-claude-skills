#!/bin/sh
# Проверки конфигурации Wordstat: только Cloud, без IAM и сетевых запросов.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
TEST_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/wordstat_config_XXXXXX")
trap 'rm -rf "$TEST_TMP_DIR"' EXIT HUP INT TERM

run_selector() {
    _test_label="$1"; _test_config="$2"; _test_backend="$3"; _test_token="$4"
    _test_file_backend="${5:-}"; _test_file_token="${6:-}"
    _test_dir="$TEST_TMP_DIR/$_test_label"
    mkdir -p "$_test_dir/config" "$_test_dir/scripts" "$_test_dir/cache"

    case "$_test_config" in
        cloud|missing_key)
            cat > "$_test_dir/config/config.json" <<'EOF'
{"yandex_cloud_folder_id":"b1g-test","auth":{"service_account_key_file":"config/sa_key.json"}}
EOF
            if [ "$_test_config" = cloud ]; then
                : > "$_test_dir/config/sa_key.json"
            fi
            ;;
        missing_folder)
            printf '%s\n' '{"auth":{"service_account_key_file":"config/sa_key.json"}}' > "$_test_dir/config/config.json"
            : > "$_test_dir/config/sa_key.json"
            ;;
        missing_key_path)
            printf '%s\n' '{"yandex_cloud_folder_id":"b1g-test"}' > "$_test_dir/config/config.json"
            ;;
        malformed)
            printf '%s\n' '{broken json' > "$_test_dir/config/config.json"
            ;;
        empty_json)
            : > "$_test_dir/config/config.json"
            ;;
        absent) ;;
        *) echo "FAIL: unknown test configuration: $_test_config" >&2; exit 1 ;;
    esac

    if [ -n "$_test_file_backend" ]; then
        printf 'YANDEX_WORDSTAT_BACKEND=%s\n' "$_test_file_backend" >> "$_test_dir/config/.env"
    fi
    if [ -n "$_test_file_token" ]; then
        printf 'YANDEX_WORDSTAT_TOKEN=%s\n' "$_test_file_token" >> "$_test_dir/config/.env"
    fi

    if _test_output=$( {
        WORDSTAT_SCRIPT_DIR="$_test_dir/scripts"
        WORDSTAT_SKILL_DIR="$_test_dir"
        WORDSTAT_CONFIG_DIR="$_test_dir/config"
        WORDSTAT_CACHE_DIR="$_test_dir/cache"
        export WORDSTAT_SCRIPT_DIR WORDSTAT_SKILL_DIR WORDSTAT_CONFIG_DIR WORDSTAT_CACHE_DIR
        export YANDEX_WORDSTAT_BACKEND="$_test_backend" YANDEX_WORDSTAT_TOKEN="$_test_token"

        # shellcheck disable=SC1091
        . "$SCRIPTS_DIR/common.sh"

        # Вызов любой из этих функций нарушает требование автономной проверки.
        curl() { : > "$_test_dir/network_called"; return 1; }
        _iam_token_get() { : > "$_test_dir/network_called"; return 1; }

        load_config || exit $?
        printf 'BACKEND=%s\n' "$WORDSTAT_BACKEND"
    } 2>&1); then
        _test_status=0
    else
        _test_status=$?
    fi

    if [ -f "$_test_dir/network_called" ]; then
        echo "FAIL: $_test_label called IAM or the network" >&2
        exit 1
    fi
    for _test_secret in "$_test_token" "$_test_file_token"; do
        [ -n "$_test_secret" ] || continue
        case "$_test_output" in
            *"$_test_secret"*) echo "FAIL: $_test_label exposed the token" >&2; exit 1 ;;
        esac
    done
    printf 'STATUS=%s\n%s\n' "$_test_status" "$_test_output"
}

assert_cloud() {
    _assert_label="$1"; _assert_result="$2"
    if printf '%s\n' "$_assert_result" | grep -q '^STATUS=0$' &&
       printf '%s\n' "$_assert_result" | grep -q '^BACKEND=cloud$'; then
        echo "  ok: $_assert_label → cloud"
    else
        printf '  FAIL: %s expected cloud, got:\n%s\n' "$_assert_label" "$_assert_result"
        exit 1
    fi
}

assert_error() {
    _assert_label="$1"; _assert_result="$2"
    if printf '%s\n' "$_assert_result" | grep -q '^STATUS=[1-9][0-9]*$' &&
       printf '%s\n' "$_assert_result" | grep -Fq 'plugins/yandex-wordstat/skills/yandex-wordstat/config/README.md'; then
        echo "  ok: $_assert_label → error with setup instructions"
    else
        printf '  FAIL: %s expected error and setup link, got:\n%s\n' "$_assert_label" "$_assert_result"
        exit 1
    fi
}

out=$(run_selector cloud_default cloud "" "")
assert_cloud "Cloud without an override" "$out"

out=$(run_selector cloud_explicit cloud cloud "")
assert_cloud "explicit Cloud from environment" "$out"

out=$(run_selector cloud_file cloud "" "" cloud)
assert_cloud "explicit Cloud from .env" "$out"

out=$(run_selector cloud_with_old_token cloud "" obsolete_secret_token)
assert_cloud "Cloud ignores an old token from environment" "$out"

out=$(run_selector cloud_with_old_file_token cloud "" "" "" obsolete_secret_token)
assert_cloud "Cloud ignores an old token from .env" "$out"

out=$(run_selector old_token_only absent "" obsolete_secret_token)
assert_error "old token without Cloud" "$out"

out=$(run_selector old_file_token_only absent "" "" "" obsolete_secret_token)
assert_error "old token from .env without Cloud" "$out"

out=$(run_selector legacy_explicit cloud legacy obsolete_secret_token)
assert_error "explicit legacy even with Cloud configured" "$out"

out=$(run_selector legacy_without_token cloud legacy "")
assert_error "explicit legacy without a token" "$out"

out=$(run_selector legacy_file cloud "" "" legacy obsolete_secret_token)
assert_error "legacy from .env even with Cloud configured" "$out"

out=$(run_selector unknown_backend cloud unknown "")
assert_error "unknown backend" "$out"

for config in absent empty_json malformed missing_folder missing_key_path missing_key; do
    out=$(run_selector "$config" "$config" "" "")
    assert_error "$config configuration" "$out"
done

out=$(run_selector malformed_with_old_token malformed "" obsolete_secret_token)
assert_error "broken Cloud configuration cannot fall back to an old token" "$out"

echo "test_selector: all passed"
