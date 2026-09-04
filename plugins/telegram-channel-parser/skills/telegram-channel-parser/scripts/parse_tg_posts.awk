#!/usr/bin/awk -f
# parse_tg_posts.awk — extract posts from Telegram web preview HTML
# Output: id \t date \t views \t reactions \t fwd_from \t fwd_link \t text_html \t media_url
#
# Note: t.me/s/ does NOT expose share/forward COUNTS (only available via MTProto).
# fwd_from = channel name this post was forwarded from (empty if original)
# fwd_link = link to original post (empty if original)
# media_url = first image/video thumbnail URL (empty if no media)

BEGIN { OFS = "\t"; id = ""; date = ""; views = ""; text = ""; reactions = 0; fwd_from = ""; fwd_link = ""; media_url = ""; text_depth = 0; text_done = 0; unsupported = 0 }

function has_class(tag, name,    classes) {
    if (!match(tag, /class="[^"]*"/)) return 0
    classes = substr(tag, RSTART + 7, RLENGTH - 8)
    return classes ~ ("(^|[[:space:]])" name "([[:space:]]|$)")
}

# Select the post body, not a reply preview, and track its nested divs.
function collect_text(line,    tag, prefix) {
    while (match(line, /<\/?div([[:space:]][^>]*)?>/)) {
        prefix = substr(line, 1, RSTART - 1)
        tag = substr(line, RSTART, RLENGTH)
        line = substr(line, RSTART + RLENGTH)

        if (has_class(tag, "message_media_not_supported")) unsupported = 1

        if (text_depth > 0) {
            text = text prefix
            if (tag ~ /^<\/div/) {
                if (text_depth > 1) text = text (quote_div[text_depth] ? "</blockquote>" : " ")
                delete quote_div[text_depth]
                text_depth--
                if (text_depth == 0) text_done = 1
            } else {
                text_depth++
                quote_div[text_depth] = tag ~ /class="[^"]*quote[^"]*"/
                text = text (quote_div[text_depth] ? "<blockquote>" : " ")
            }
        } else if (!text_done && has_class(tag, "tgme_widget_message_text") && !has_class(tag, "js-message_reply_text")) {
            text_depth = 1
        }
    }
    if (text_depth > 0) text = text line " "
}

function print_post(    body) {
    if (id == "") return
    body = clean_text(text)
    if (body == "" && unsupported) {
        body = "[Текст недоступен в веб-превью Telegram: https://t.me/" post_path "]"
    }
    gsub(/[[:space:]]/, "", views)
    print id, date, views, (reactions == 0 ? "" : reactions), fwd_from, fwd_link, body, media_url
}

function clean_text(t) {
    gsub(/[\t\n\r]+/, " ", t)
    # Convert tg_spoiler class
    gsub(/class="tg_spoiler"/, "class=\"tg-spoiler\"", t)
    # Preserve spoiler spans, remove all other spans
    gsub(/<span[^>]*tg-spoiler[^>]*>/, "<!SPOILER>", t)
    gsub(/<\/?span[^>]*>/, "", t)
    gsub(/<!SPOILER>/, "<span class=\"tg-spoiler\">", t)
    # Ensure spoiler spans are closed
    {
        _open = 0; _close = 0
        _tmp = t
        while (match(_tmp, /<span[^>]*>/)) { _open++; _tmp = substr(_tmp, RSTART + RLENGTH) }
        _tmp = t
        while (match(_tmp, /<\/span>/)) { _close++; _tmp = substr(_tmp, RSTART + RLENGTH) }
        while (_close < _open) { t = t "</span>"; _close++ }
    }
    # Ensure pre tags are closed
    {
        _open = 0; _close = 0
        _tmp = t
        while (match(_tmp, /<pre[^>]*>/)) { _open++; _tmp = substr(_tmp, RSTART + RLENGTH) }
        _tmp = t
        while (match(_tmp, /<\/pre>/)) { _close++; _tmp = substr(_tmp, RSTART + RLENGTH) }
        while (_close < _open) { t = t "</pre>"; _close++ }
    }
    # Clean leftover attrs except href and class
    gsub(/ style="[^"]*"/, "", t)
    gsub(/ dir="[^"]*"/, "", t)
    # Normalize whitespace
    gsub(/[[:space:]]+/, " ", t)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", t)
    return t
}

/data-post=/ {
    print_post()
    id = ""; date = ""; views = ""; text = ""; reactions = 0; fwd_from = ""; fwd_link = ""; media_url = ""; text_depth = 0; text_done = 0; unsupported = 0
    post_path = $0
    sub(/.*data-post="/, "", post_path)
    sub(/".*/, "", post_path)
    tmp = post_path
    sub(/.*\//, "", tmp)
    if (tmp ~ /^[0-9]+$/) id = tmp
}

id != "" && /datetime="/ && date == "" {
    tmp = $0
    sub(/.*datetime="/, "", tmp)
    sub(/".*/, "", tmp)
    date = tmp
}

id != "" && /tgme_widget_message_views/ {
    tmp = $0
    sub(/.*tgme_widget_message_views[^>]*>/, "", tmp)
    sub(/<.*/, "", tmp)
    gsub(/[[:space:]]/, "", tmp)
    if (tmp != "" && views == "") views = tmp
}

# Forwarded from — extract source channel name and link
id != "" && /tgme_widget_message_forwarded_from_name/ && fwd_from == "" {
    tmp = $0
    # Extract link: href="https://t.me/channel/123"
    link = tmp
    if (match(link, /href="[^"]+"/)) {
        link = substr(link, RSTART + 6, RLENGTH - 7)
        fwd_link = link
    }
    # Extract name from <span dir="auto">Name</span>
    sub(/.*<span[^>]*>/, "", tmp)
    sub(/<\/span>.*/, "", tmp)
    gsub(/[[:space:]]+/, " ", tmp)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", tmp)
    if (tmp != "") fwd_from = tmp
}

# Media — extract first image URL from background-image:url('...')
id != "" && /tgme_widget_message_photo_wrap/ && media_url == "" {
    tmp = $0
    if (match(tmp, /background-image:url\('[^']+'\)/)) {
        media_url = substr(tmp, RSTART + 22, RLENGTH - 24)
    }
}

# Video thumbnail
id != "" && /tgme_widget_message_video_thumb/ && media_url == "" {
    tmp = $0
    if (match(tmp, /background-image:url\('[^']+'\)/)) {
        media_url = substr(tmp, RSTART + 22, RLENGTH - 24)
    }
}

id != "" { collect_text($0) }

id != "" && /tgme_reaction/ {
    tmp = $0
    while (match(tmp, /<\/i>[0-9]+/)) {
        val = substr(tmp, RSTART, RLENGTH)
        sub(/<\/i>/, "", val)
        if (val + 0 > 0) reactions = reactions + val
        tmp = substr(tmp, RSTART + RLENGTH)
    }
}

END {
    print_post()
}
