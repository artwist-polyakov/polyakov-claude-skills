#!/usr/bin/env bash

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$(cd "$TEST_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tg_parser_test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# Минимальная разметка веб-превью; все тексты синтетические.
cat > "$TMP_DIR/posts.html" <<'EOF'
<div class="tgme_widget_message" data-post="channel/101">
<a class="tgme_widget_message_reply" href="https://t.me/channel/100">
<div class="tgme_widget_message_text js-message_reply_text" dir="auto">Чужой текст ответа</div>
</a><div class="tgme_widget_message_text js-message_text" dir="auto">Собственный <b>текст</b></div>
</div>
<div class="tgme_widget_message" data-post="channel/102">
<div class="tgme_widget_message_text js-message_reply_text">Чужой текст</div><div class="tgme_widget_message_text js-message_text">Основной текст в той же строке</div><div class="tgme_widget_message_footer">Не текст поста</div>
</div>
<div class="tgme_widget_message" data-post="channel/103">
<div class="tgme_widget_message_text" dir="auto">Текст без js-класса</div>
</div>
<div class="tgme_widget_message" data-post="channel/104">
<div class="tgme_widget_message_text js-message_text">Начало <div class="tgme_widget_message_text_quote">Цитата <div><b>внутри</b></div> конец цитаты</div> хвост</div><div class="tgme_widget_message_footer">Не текст поста</div>
</div>
<div class="tgme_widget_message" data-post="channel/105">
<div class="tgme_widget_message_text js-message_text" dir="auto">
Начало
<div class="tgme_widget_message_text_quote">Цитата
<div><i>внутри</i></div>
конец цитаты</div>
хвост <a href="https://example.com/">ссылка</a>
</div>
</div>
<div class="tgme_widget_message" data-post="channel/106">
<div class="tgme_widget_message_text js-message_text">Начало <blockquote>Цитата</blockquote> хвост</div>
</div>
<div class="tgme_widget_message" data-post="countwithsasha/734">
<div class="message_media_not_supported_wrap">
  <div class="message_media_not_supported">
    <div class="message_media_not_supported_label">Please open Telegram to view this post</div>
  </div>
</div>
</div>
<div class="tgme_widget_message" data-post="channel/108">
<a class="tgme_widget_message_photo_wrap" style="background-image:url('https://example.com/photo.jpg')"></a>
</div>
<div class="tgme_widget_message" data-post="channel/109">
<div class="message_media_not_supported"><div class="message_media_not_supported_label">Please open Telegram to view this post</div></div>
<div class="tgme_widget_message_text js-message_text">Подпись доступна</div>
</div>
<div class="tgme_widget_message" data-post="channel/110">
<div class="tgme_widget_message_text js-message_text">Следующий пост</div>
<time datetime="2026-07-28T12:00:00+00:00"></time>
<span class="tgme_widget_message_views">1 234</span>
<span class="tgme_reaction"><i>👍</i>3</span>
</div>
<div class="tgme_widget_message" data-post="channel/111">
<div class="tgme_widget_message_text js-message_reply_text">Только превью ответа</div>
<div class="message_media_not_supported"><div class="message_media_not_supported_label">Please open Telegram to view this post</div></div>
</div>
<div class="tgme_widget_message" data-post="channel/112">
<div class="tgme_widget_message_text js-message_text">Длинный текст <div class="tgme_widget_message_text_quote">Цитата</div>
EOF

# Проверяем полный длинный хвост, а не только наличие последних слов.
awk 'BEGIN { for (i = 1; i <= 1000; i++) printf "Часть %d. ", i; print "Конец." }' > "$TMP_DIR/long_text"
cat "$TMP_DIR/long_text" >> "$TMP_DIR/posts.html"
printf '%s\n' '</div></div>' >> "$TMP_DIR/posts.html"

awk -f "$SCRIPT_DIR/parse_tg_posts.awk" "$TMP_DIR/posts.html" > "$TMP_DIR/posts.tsv"
awk -F '\t' 'NF != 8 { exit 1 } END { if (NR != 12) exit 1 }' "$TMP_DIR/posts.tsv" \
    || fail "изменилось число постов или TSV-полей"

expect_text() {
    printf '%s\n' "$2" > "$TMP_DIR/expected_text"
    awk -F '\t' -v id="$1" '$1 == id { print $7 }' "$TMP_DIR/posts.tsv" > "$TMP_DIR/actual_text"
    cmp -s "$TMP_DIR/expected_text" "$TMP_DIR/actual_text" || fail "пост $1: $3"
}

expect_text 101 'Собственный <b>текст</b>' 'превью ответа заменило собственный текст'
expect_text 102 'Основной текст в той же строке' 'не выделен собственный блок в общей строке'
expect_text 103 'Текст без js-класса' 'потерян текст со старым набором классов'
expect_text 104 'Начало <blockquote>Цитата <b>внутри</b> конец цитаты</blockquote> хвост' 'потерян хвост или неверно закрыта вложенная цитата'
expect_text 105 'Начало <blockquote>Цитата <i>внутри</i> конец цитаты</blockquote> хвост <a href="https://example.com/">ссылка</a>' 'повреждён многострочный текст с цитатой'
expect_text 106 'Начало <blockquote>Цитата</blockquote> хвост' 'повреждён исходный blockquote'
expect_text 734 '[Текст недоступен в веб-превью Telegram: https://t.me/countwithsasha/734]' 'не сообщено о недоступном тексте'
expect_text 108 '' 'медийный пост без подписи ошибочно признан недоступным'
expect_text 109 'Подпись доступна' 'подпись заменена сообщением о неподдерживаемом медиа'
expect_text 110 'Следующий пост' 'состояние предыдущего поста не сброшено'
expect_text 111 '[Текст недоступен в веб-превью Telegram: https://t.me/channel/111]' 'превью ответа скрывает недоступность собственного текста'
expect_text 112 "Длинный текст <blockquote>Цитата</blockquote> $(cat "$TMP_DIR/long_text")" 'обрезан длинный текст после цитаты'

awk -F '\t' '
    $1 == 108 && $8 == "https://example.com/photo.jpg" { media_ok = 1 }
    $1 == 110 && $2 == "2026-07-28T12:00:00+00:00" && $3 == "1234" && $4 == 3 && $8 == "" { metadata_ok = 1 }
    END { exit !(media_ok && metadata_ok) }
' "$TMP_DIR/posts.tsv" || fail "потеряны метаданные или адрес медиа перешёл в следующий пост"
