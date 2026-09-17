---
name: reddit-skill
description: |
  Reddit: чтение постов и комментариев без ключей через веб-инструмент агента,
  RSS и публичный JSON; API для пользователей, сабреддитов и поиска. OAuth2, кеш,
  автоматический переход на RSS при отказе авторизации, опциональные write-операции
  с двойным предохранителем (REDDIT_ENABLE_WRITE=1 + --confirm).
  Triggers: reddit, reddit api, reddit subreddit, reddit user, reddit post,
  reddit search, парсинг reddit, посты reddit, комментарии reddit, реддит.
---

# reddit-skill

Чтение публичных списков через RSS, постов и комментариев через `.json` без ключей,
а также прямой доступ к Reddit API
через OAuth2 без PRAW. Порт MCP-сервера
[Arindam200/reddit-mcp](https://github.com/Arindam200/reddit-mcp) на shell-скрипты
по образцу yandex-metrika / yandex-search-api.

## Выбор способа чтения

Для публичного чтения по пользовательским подпискам, в том числе RSS-мониторинга,
бери список из [config/subscriptions.txt](config/subscriptions.txt): одно название
сабреддита на строку, без `r/`. Сабреддиты, явно заданные в запросе, имеют приоритет
над сохранённым списком. Не ищи подписки в `.env` или аккаунте и не подключай
браузерную сессию для получения этого списка. Если файл отсутствует или пуст,
уточни сабреддиты у пользователя.

Если пользователь не указал способ, для публичного чтения сначала используй
доступный встроенный инструмент чтения URL (`fetch`, `WebFetch`, `open` или аналог).
Открывай конкретный адрес; результаты поиска сами по себе не заменяют содержимое:

- **Пост и комментарии:** используй адрес с `.json`; для обычной ссылки добавь
  `.json` к пути поста перед параметрами запроса,
  например `https://www.reddit.com/r/Python/comments/abc123/title/.json?limit=100&raw_json=1`.
- **Список постов:** если инструмент поддерживает XML/Atom, открой RSS-ленту,
  например `https://www.reddit.com/r/Python/top.rss?t=day&limit=25`.

Для этого способа не запускай `auth_check.sh` и не запрашивай ключи приложения.
Список подписок хранится отдельно от `.env`; наличие или исправность ключей
не является условием публичного чтения.
При повторном мониторинге запрашивай свежие данные, если инструмент это позволяет;
иначе укажи ограничение свежести.

Если инструмента нет, он не поддерживает формат, не вернул нужное содержимое или
задача требует скрипта и локального кеша, используй команды ниже. Для чтения без ключей передавай
`REDDIT_RSS_MODE=1`. Ошибка одного способа не доказывает недоступность другого.

Веб-инструмент может возвращать сокращённый текст вместо исходного JSON или RSS.
Не выдавай такой ответ за полный файл и не обещай все комментарии: учитывай усечение,
параметр `limit` и элементы `more`. Локальный кеш описан ниже только для скриптов;
при чтении веб-инструментом не сообщай о сохранении файла, если он не был сохранён.

## Настройка скриптов

Для мониторинга постов ключи приложения не нужны. Включить RSS явно:

```bash
REDDIT_RSS_MODE=1 sh scripts/subreddit_top.sh --subreddit singularity --time day --limit 25
```

`REDDIT_RSS_MODE=1` можно сохранить в `config/.env`. При передаче через окружение
скрипт не читает `.env`. Для списка из `config/subscriptions.txt` передавай каждый
сабреддит явно через `--subreddit`: скрипты сами список подписок не загружают.
Для RSS есть стандартный User-Agent; его можно заменить
через `REDDIT_USER_AGENT`.

Для полного доступа через API зарегистрировать **script-app** на
https://www.reddit.com/prefs/apps и заполнить `config/.env`:
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT` (обязателен по правилам Reddit)

Подробная инструкция: [config/README.md](config/README.md).

Для write/me дополнительно:
- `REDDIT_USERNAME`, `REDDIT_PASSWORD` — владелец script-app (без 2FA)
- `REDDIT_ENABLE_WRITE=1` + `--confirm` на каждой write-команде

## Поведение скриптов

1. **Cache-first** — токен, юзеры, сабреддиты, листинги кешируются по детерминированному ключу.
2. **Context window hygiene** — stdout ≤ 30 строк, полный JSON в `cache/listings/<hash>.json`.
3. **Выбор режима** — `REDDIT_RSS_MODE=1` включает RSS. Без ключей поддерживаемые команды чтения также используют RSS. С ключами выбирается user mode при наличии `REDDIT_USERNAME`+`REDDIT_PASSWORD`, иначе app-only. При отказе авторизации чтение постов автоматически переходит на RSS в текущем запуске; настройки на диске не меняются.
4. **Двойная защита write** — два уровня:
   - Без `REDDIT_ENABLE_WRITE=1` в `.env` write-скрипты **отказывают сразу** с понятной ошибкой (никакого dry-run — это «ворота» уровня окружения).
   - С `REDDIT_ENABLE_WRITE=1`, но без `--confirm` — **dry-run**: печатают что бы сделали, но не отправляют запрос.
   - Только при `REDDIT_ENABLE_WRITE=1` *и* `--confirm` команда реально идёт на Reddit.
5. **Rate-limit от заголовков** — читаем `x-ratelimit-remaining`, `x-ratelimit-reset`, `Retry-After`. На 429 один retry если ожидание ≤ 60s.

## Использование скриптов

### Проверка доступа

1. **Если выбран скрипт, запускай нужную команду чтения сразу.** Если нужно проверить доступ отдельно:
   ```bash
   sh scripts/auth_check.sh
   ```
   Успех: `OK: RSS feed works`, `OK: app-only token works` или `OK: user token works`.
   Для RSS не запрашивай файл с ключами и не требуй регистрации приложения.

2. **Если нужна write-операция:**
   - Убедись, что в `.env` есть `REDDIT_USERNAME`+`REDDIT_PASSWORD`+`REDDIT_ENABLE_WRITE=1`
     (без `REDDIT_ENABLE_WRITE=1` команда сразу падает с ошибкой — это намеренно)
   - Сначала запусти команду **без** `--confirm` — увидишь dry-run
   - Только после ревью пользователя — добавляй `--confirm`

3. **Имя сабреддита/юзера** можно передавать с префиксом или без: `r/python` ≡ `python`, `u/spez` ≡ `spez`.

### Возможности RSS

RSS поддерживают `subreddit_top.sh`, `search.sh`, `user_posts.sh` и `auth_check.sh`.
Параметры поиска, сортировки, периода и количества передаются в публичную ленту.
`submission.sh` в этом же режиме читает `https://www.reddit.com/comments/{id}.json`
без токена. Можно передать обычную ссылку поста, ссылку с `.json` или ID.
`--include-comments` у топа загружает посты и комментарии через тот же публичный JSON.
Остальные команды требуют API.

Сохраняются доступные в ленте заголовок, ссылка, дата, автор, сабреддит и текст.
JSON имеет привычную структуру `Listing` и признак `source: "rss"`.
`score` и `num_comments` равны `null`, в кратком выводе — `?`: RSS этих чисел не даёт.
Не оценивай по ним популярность поста. Текст ограничен содержимым самой ленты.

Публичный `.json` сохраняется как массив `[Listing поста, Listing комментариев]`,
каждый с `source: "public-json"`; оценки и другие поля берутся из ответа Reddit.
Полный полученный текст и вложенные ответы доступны в кеше, на экран выводится
краткая сводка. Не обещай все комментарии: ответ ограничен `--limit-comments` /
`--comments-per-post` и может содержать `more`. Скилл не разворачивает `more`
дополнительными запросами; `submission.sh` сообщает, если нашёл его в дереве.

Автоматический переход срабатывает при отказе выдачи токена (HTTP 400/401/403
или OAuth-ошибка авторизации), API 403 либо повторном API 401 после обновления токена.
Сетевые ошибки, 429 и 5xx не означают нерабочий ключ и не включают RSS.
Если RSS или публичный JSON также недоступен, команда возвращает явную ошибку.
При ошибке загрузки комментариев уже полученный список постов остаётся в кеше.

## Scripts

### Auth & identity

| Script | Endpoint | Mode | Description |
|--------|----------|------|-------------|
| `auth_check.sh` | `GET /r/all/new?limit=1` или `.rss` | any | Проверка API или RSS |
| `me.sh` | `GET /api/v1/me` | user only | Личный профиль (карма, имя) |

### Read

| Script | Endpoint | Description |
|--------|----------|-------------|
| `user_info.sh --username U` | `/user/{u}/about` | Профиль юзера |
| `user_posts.sh --username U` | `/user/{u}/submitted` | Посты юзера |
| `user_comments.sh --username U` | `/user/{u}/comments` | Комментарии юзера |
| `subreddit_info.sh --subreddit S` | `/r/{s}/about` | Метаданные сабреддита |
| `subreddit_stats.sh --subreddit S` | `/r/{s}/about` + `/about/rules` + `/about/moderators` | Расширенные метрики + правила + модераторы |
| `subreddit_popular.sh` | `/subreddits/popular` | Популярные сабреддиты (Reddit «trending») |
| `subreddit_top.sh --subreddit S` | `/r/{s}/top` (+ `/comments/{id}` per-post при `--include-comments`) | Топ-посты сабреддита; опционально с комментариями к каждому |
| `search.sh --query "..."` | `/search` или `/r/{s}/search` | Поиск по Reddit (опц. `--subreddit`) |
| `submission.sh --id I` или `--url U` | `/comments/{id}` или `/comments/{id}.json` | Пост + комментарии; работает без ключей |

Общие read-флаги: `--limit N`, `--no-cache`, `--time T` (где применимо), `--sort S` (где применимо).

### Write (опасные — двойной предохранитель)

| Script | Endpoint | Description |
|--------|----------|-------------|
| `post_create.sh --subreddit S --title T (--content C \| --url U)` | `POST /api/submit` | Опубликовать пост |
| `comment_reply.sh --parent-id ID --content C` | `POST /api/comment` | Ответить на пост (`t3_*`) или комментарий (`t1_*`) |
| `subreddit_subscribe.sh --subreddit S [--unsubscribe]` | `POST /api/subscribe` | Подписка / отписка |

Все write-команды требуют `--confirm` *и* `REDDIT_ENABLE_WRITE=1` в `.env`.
Поведение:
- `REDDIT_ENABLE_WRITE` не установлен → **отказ с ошибкой** (envelope-уровень).
- `REDDIT_ENABLE_WRITE=1`, но без `--confirm` → **dry-run** (печать, без сети).
- Оба условия выполнены → реальный POST.

## Кеш скриптов

```
cache/
├── token.json                  # OAuth access token + expires_at
├── users/<username>.json       # /user/{u}/about
├── subreddits/<sub>.json       # /r/{sub}/about
├── subreddits/<sub>.rules.json
├── subreddits/<sub>.moderators.json
├── listings/<hash>.json        # листинги (search, top, posts, comments, popular)
├── top_with_comments/<hash>/   # копии постов с комментариями в подпапках api/ и rss/
└── me.json                     # /api/v1/me
```

Ключи кеша публичного чтения отделены от API. При неудачном обновлении поста или списка прежний
файл сохраняется. Для свежих публикаций при повторном мониторинге передавай `--no-cache`.

Поиск по кешу: `grep -r "term" cache/` или `rg "term" cache/`.

## Examples

```bash
# 1) Profile of a user
sh scripts/user_info.sh --username spez

# 2) Top posts in r/Python this week
sh scripts/subreddit_top.sh --subreddit python --time week --limit 25

# 3) Search "machine learning" in r/learnprogramming, sorted by new
sh scripts/search.sh --query "machine learning" --subreddit learnprogramming \
    --sort new --time month --limit 20

# 4) Пост и комментарии через публичный JSON без ключей
REDDIT_RSS_MODE=1 sh scripts/submission.sh \
    --url "https://www.reddit.com/r/Python/comments/abc123/some_title/" \
    --limit-comments 50

# 5) DRY-RUN of a comment reply (requires REDDIT_ENABLE_WRITE=1; without --confirm only prints)
REDDIT_ENABLE_WRITE=1 sh scripts/comment_reply.sh \
    --parent-id t3_abc123 --content "Looks great!"

# 6) Real reply (after dry-run review)
REDDIT_ENABLE_WRITE=1 sh scripts/comment_reply.sh \
    --parent-id t3_abc123 --content "Looks great!" --confirm

# 7) Топ с комментариями (один дополнительный API или JSON запрос на пост)
sh scripts/subreddit_top.sh --subreddit Python --time week --limit 5 \
    --include-comments --comments-per-post 25
```

## Rate limits

Reddit OAuth: ~60 req/min для app-only, ~600/10min для user-mode (зависит от истории аккаунта). Скилл читает Reddit-специфичные заголовки `x-ratelimit-*` и стандартный `Retry-After`. На 429 делает один retry если ожидание ≤ 60s, иначе fail с понятной ошибкой.

## Tests

```bash
sh scripts/tests/run.sh
```

No-network, проверяют:
- парсинг submission URL → id
- детерминированность cache_key
- корректность авто-выбора режима auth
- что write-команды без `REDDIT_ENABLE_WRITE` или без `--confirm` ничего не отправляют
- разбор Atom, публичный JSON, чтение без ключей, переход с API и разделение кеша

## Differences from upstream

Оригинальный [reddit-mcp](https://github.com/Arindam200/reddit-mcp) использует PRAW + Python и зашит как MCP-сервер. Здесь публичное чтение начинается с доступного веб-инструмента агента; shell-скрипты используют curl и кеш в директории скилла. Анализ постов и обсуждений выполняет агент по полученным данным.

## Документация

- [Reddit API docs](https://www.reddit.com/dev/api/)
- [RSS Reddit](https://www.reddit.com/wiki/rss)
- [OAuth2 wiki](https://github.com/reddit-archive/reddit/wiki/OAuth2)
- [API rules / User-Agent](https://github.com/reddit-archive/reddit/wiki/API)
