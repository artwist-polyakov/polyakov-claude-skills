#!/usr/bin/env bash

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$(cd "$TEST_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tg_latest_test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

# Изолируем и конфигурацию, и сохранённые страницы; подменяем только сеть.
mkdir -p "$TMP_DIR/scripts" "$TMP_DIR/fixtures"
cp "$SCRIPT_DIR/common.sh" "$SCRIPT_DIR/parse_tg_posts.awk" \
    "$SCRIPT_DIR/digest_json.sh" "$SCRIPT_DIR/compare_channels.sh" "$TMP_DIR/scripts/"
export TG_TEST_FIXTURES="$TMP_DIR/fixtures"
export TG_TEST_REQUESTS="$TMP_DIR/requests"
cat >> "$TMP_DIR/scripts/common.sh" <<'EOF'

tg_fetch() {
    local page="${1#"$TG_BASE_URL/"}"
    printf '%s\n' "$page" >> "$TG_TEST_REQUESTS"
    [ -f "$TG_TEST_FIXTURES/$page" ] || return 22
    cat "$TG_TEST_FIXTURES/$page"
}
EOF

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

page() {
    printf '<span class="tgme_header_counter">%s</span>\n' "$2" > "$TG_TEST_FIXTURES/$1"
}

post() {
    cat >> "$TG_TEST_FIXTURES/$1" <<EOF
<div data-post="channel/$2">
<time datetime="$3"></time>
<span class="tgme_widget_message_views">$4</span>
<div class="tgme_widget_message_text">$5</div>
</div>
EOF
}

run_command() {
    : > "$TG_TEST_REQUESTS"
    bash "$TMP_DIR/scripts/$command.sh" --channels "$1" --limit "${2:-1}" \
        --period today --csv "$TMP_DIR/digest.json" > "$TMP_DIR/result" 2> "$TMP_DIR/stderr" || {
        cat "$TMP_DIR/stderr" >&2
        fail "$command завершился с ошибкой"
    }
    if [ "$command" = digest_json ]; then
        cp "$TMP_DIR/digest.json" "$TMP_DIR/result"
    fi
}

expect_requests() {
    printf '%s\n' "$@" > "$TMP_DIR/expected_requests"
    diff -u "$TMP_DIR/expected_requests" "$TG_TEST_REQUESTS" || fail "$command: лишние или пропущенные запросы"
}

expect_subscribers() {
    local pattern
    if [ "$command" = digest_json ]; then
        pattern="\"$1\":\{[^}]*\"subscribers\":\"$2\""
    else
        pattern="^@$1[[:space:]]+$2[[:space:]]"
    fi
    grep -Eq "$pattern" "$TMP_DIR/result" || fail "$command: неверное число подписчиков @$1"
}

today="$(date +%Y-%m-%d)T12:00:00+00:00"
old="2000-01-01T12:00:00+00:00"

for command in digest_json compare_channels; do
    # Два канала, достаточно первой страницы: по одному запросу на каждый.
    for channel in alpha beta; do
        page "$channel" 100
        post "$channel" 30 "$today" 300 first
        post "$channel" 29 "$old" 100 old
    done
    run_command alpha,beta
    expect_requests alpha beta
    expect_subscribers alpha 100
    expect_subscribers beta 100

    # Следующий запуск получает обновлённые сведения и посты.
    page alpha 101
    post alpha 30 "$today" 321 refreshed
    post alpha 29 "$old" 100 old
    run_command alpha
    expect_requests alpha
    expect_subscribers alpha 101
    if [ "$command" = digest_json ]; then
        grep -Fq '"text":"refreshed"' "$TMP_DIR/result" || fail "дайджест использовал прежние посты"
    else
        grep -Eq '^@alpha[[:space:]]+101[[:space:]]+321[[:space:]]+0[[:space:]]+1$' "$TMP_DIR/result" || fail "сравнение использовало прежние посты"
    fi

    # Для двух постов нужна вторая страница; сведения берутся с первой.
    page alpha 102
    post alpha 30 "$today" 300 latest
    page 'alpha?before=30' 999
    post 'alpha?before=30' 20 "$today" 100 previous
    post 'alpha?before=30' 19 "$old" 50 old
    run_command alpha 2
    expect_requests alpha 'alpha?before=30'
    expect_subscribers alpha 102
    if [ "$command" = digest_json ]; then
        grep -Fq '"id":"20"' "$TMP_DIR/result" || fail "дайджест потерял вторую страницу"
        ! grep -Fq '"id":"19"' "$TMP_DIR/result" || fail "дайджест включил старый пост"
    else
        grep -Eq '^@alpha[[:space:]]+102[[:space:]]+200[[:space:]]+0[[:space:]]+2$' "$TMP_DIR/result" || fail "неверные показатели сравнения после пагинации"
    fi

    # После неудачного запроса прежние сведения и посты не возвращаются.
    rm "$TG_TEST_FIXTURES/alpha"
    run_command alpha
    expect_requests alpha
    if [ "$command" = digest_json ]; then
        grep -Fxq '{"posts":[],"channels":{}}' "$TMP_DIR/result" || fail "дайджест вернул прежние данные после ошибки загрузки"
    else
        grep -Eq '^@alpha[[:space:]]+\?[[:space:]]+\?[[:space:]]+\?[[:space:]]+0$' "$TMP_DIR/result" || fail "сравнение вернуло прежние данные после ошибки загрузки"
    fi
done

# Нулевой лимит отклоняется до чтения сохранённой страницы и запросов.
: > "$TG_TEST_REQUESTS"
if bash "$TMP_DIR/scripts/compare_channels.sh" --channels alpha --limit 0 \
    > "$TMP_DIR/result" 2> "$TMP_DIR/stderr"; then
    fail "сравнение приняло нулевой лимит"
fi
grep -Fq -- '--limit must be a positive integer' "$TMP_DIR/stderr" || fail "нет понятной ошибки для нулевого лимита"
[ ! -s "$TG_TEST_REQUESTS" ] || fail "сравнение отправило запрос при нулевом лимите"

# У канала нет постов за выбранный день, но сведения о нём остаются.
command=digest_json
page alpha 700
post alpha 30 "$old" 300 old
run_command alpha
expect_requests alpha
expect_subscribers alpha 700
grep -Fq '"posts":[]' "$TMP_DIR/result" || fail "дайджест включил пост за другой день"
