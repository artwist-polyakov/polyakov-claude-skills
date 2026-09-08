#!/bin/sh
# Проверка общего ограничения облачного Wordstat: phrase не длиннее 400 символов.
# Тест автономный: IAM и транспорт заменяются заглушками, сеть и ключи не нужны.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"

TEST_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/wordstat_phrase_length_XXXXXX")
trap 'rm -rf "$TEST_TMP_DIR"' EXIT HUP INT TERM

WORDSTAT_SCRIPT_DIR="$SCRIPTS_DIR"
WORDSTAT_SKILL_DIR="$SKILL_DIR"
export WORDSTAT_SCRIPT_DIR WORDSTAT_SKILL_DIR
# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

WORDSTAT_BACKEND="cloud"
WORDSTAT_CLOUD_FOLDER_ID="b1g-test-folder"

fail() {
    printf '  FAIL: %s\n' "$1" >&2
    exit 1
}

repeat_ya() {
    _tcpl_target="$1"
    _tcpl_result=""
    _tcpl_index=0
    while [ "$_tcpl_index" -lt "$_tcpl_target" ]; do
        _tcpl_result="${_tcpl_result}я"
        _tcpl_index=$((_tcpl_index + 1))
    done
    printf '%s' "$_tcpl_result"
}

params_for() {
    _tcpl_params_method="$1"
    _tcpl_params_phrase="$2"
    case "$_tcpl_params_method" in
        topRequests|regions)
            printf '{"phrase":"%s"}' "$_tcpl_params_phrase"
            ;;
        dynamics)
            printf '{"phrase":"%s","period":"daily","fromDate":"2025-01-01"}' \
                "$_tcpl_params_phrase"
            ;;
        *)
            fail "неизвестный метод в тесте: $_tcpl_params_method"
            ;;
    esac
}

assert_translation_accepts_400() {
    _tcpl_method="$1"
    _tcpl_params=$(params_for "$_tcpl_method" "$PHRASE_400")
    _tcpl_error_file="$TEST_TMP_DIR/${_tcpl_method}_400.err"

    if _xlate_request "$_tcpl_method" "$_tcpl_params" \
        >/dev/null 2>"$_tcpl_error_file"; then
        printf '  ok: %s принимает 400 кириллических символов\n' "$_tcpl_method"
        return 0
    fi

    _tcpl_error=$(cat "$_tcpl_error_file")
    fail "$_tcpl_method отклонил 400 символов: $_tcpl_error"
}

assert_translation_rejects_401() {
    _tcpl_method="$1"
    _tcpl_params=$(params_for "$_tcpl_method" "$PHRASE_401")

    if _tcpl_output=$(_xlate_request "$_tcpl_method" "$_tcpl_params" 2>&1); then
        fail "$_tcpl_method принял 401 символ"
    fi

    case "$_tcpl_output" in
        *PHRASE_TOO_LONG:401:400*) ;;
        *) fail "$_tcpl_method вернул неожиданный маркер: $_tcpl_output" ;;
    esac
    printf '  ok: %s отклоняет 401 кириллический символ\n' "$_tcpl_method"
}

assert_cloud_rejects_before_transport() {
    _tcpl_method="$1"
    _tcpl_params=$(params_for "$_tcpl_method" "$PHRASE_401")
    _tcpl_iam_marker="$TEST_TMP_DIR/${_tcpl_method}_iam_called"
    _tcpl_transport_marker="$TEST_TMP_DIR/${_tcpl_method}_transport_called"
    rm -f "$_tcpl_iam_marker" "$_tcpl_transport_marker"

    if _tcpl_output=$(
        _iam_token_get() {
            : > "$_tcpl_iam_marker"
            printf 'test-token'
        }
        curl() {
            : > "$_tcpl_transport_marker"
            return 1
        }
        _cloud_request "$_tcpl_method" "$_tcpl_params" 2>&1
    ); then
        fail "_cloud_request принял 401 символ для $_tcpl_method"
    fi

    [ ! -e "$_tcpl_iam_marker" ] || \
        fail "_cloud_request вызвал _iam_token_get для $_tcpl_method"
    [ ! -e "$_tcpl_transport_marker" ] || \
        fail "_cloud_request вызвал curl для $_tcpl_method"

    case "$_tcpl_output" in
        *401*) ;;
        *) fail "в сообщении $_tcpl_method нет фактической длины 401: $_tcpl_output" ;;
    esac
    case "$_tcpl_output" in
        *400*) ;;
        *) fail "в сообщении $_tcpl_method нет предела 400: $_tcpl_output" ;;
    esac

    printf '  ok: %s отклоняется до IAM и транспорта с понятным сообщением\n' \
        "$_tcpl_method"
}

PHRASE_400=$(repeat_ya 400)
PHRASE_401=$(repeat_ya 401)

for method in topRequests dynamics regions; do
    assert_translation_accepts_400 "$method"
    assert_translation_rejects_401 "$method"
    assert_cloud_rejects_before_transport "$method"
done

echo "test_cloud_phrase_length: all passed"
