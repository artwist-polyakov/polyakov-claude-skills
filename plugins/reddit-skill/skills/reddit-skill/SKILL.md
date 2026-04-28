---
name: reddit-skill
description: |
  Reddit API: пользователи, сабреддиты, посты, комментарии, поиск.
  OAuth2 (app-only / user mode), кеш-first, опциональные write-операции
  с двойным предохранителем (REDDIT_ENABLE_WRITE=1 + --confirm).
  Triggers: reddit, reddit api, reddit subreddit, reddit user, reddit post,
  reddit search, парсинг reddit, посты reddit, комментарии reddit, реддит.
---

# reddit-skill

Прямой доступ к Reddit API через OAuth2 без PRAW. Порт MCP-сервера
[Arindam200/reddit-mcp](https://github.com/Arindam200/reddit-mcp) на shell-скрипты
по образцу yandex-metrika / yandex-search-api.

## Config

Нужно зарегистрировать **script-app** на https://www.reddit.com/prefs/apps и
заполнить `config/.env`. Подробная инструкция: [config/README.md](config/README.md).

Минимум для read-only:
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT` (обязателен по правилам Reddit)

Для write/me дополнительно:
- `REDDIT_USERNAME`, `REDDIT_PASSWORD` — владелец script-app (без 2FA)
- `REDDIT_ENABLE_WRITE=1` + `--confirm` на каждой write-команде

## Philosophy

1. **Cache-first** — токен, юзеры, сабреддиты, листинги кешируются по детерминированному ключу.
2. **Context window hygiene** — stdout ≤ 30 строк, полный JSON в `cache/listings/<hash>.json`.
3. **Auth auto-detect** — если в `.env` есть `REDDIT_USERNAME`+`REDDIT_PASSWORD`, скилл идёт в user mode (`grant_type=password`); иначе — app-only (`grant_type=client_credentials`).
4. **Двойная защита write** — два уровня:
   - Без `REDDIT_ENABLE_WRITE=1` в `.env` write-скрипты **отказывают сразу** с понятной ошибкой (никакого dry-run — это «ворота» уровня окружения).
   - С `REDDIT_ENABLE_WRITE=1`, но без `--confirm` — **dry-run**: печатают что бы сделали, но не отправляют запрос.
   - Только при `REDDIT_ENABLE_WRITE=1` *и* `--confirm` команда реально идёт на Reddit.
5. **Rate-limit от заголовков** — читаем `x-ratelimit-remaining`, `x-ratelimit-reset`, `Retry-After`. На 429 один retry если ожидание ≤ 60s.

## Workflow

### STOP! Перед любым запросом

1. **Проверь, что credentials настроены:**
   ```bash
   sh scripts/auth_check.sh
   ```
   Должен вывести `OK: app-only token works` (или `OK: user token works`).

2. **Если нужна write-операция:**
   - Убедись, что в `.env` есть `REDDIT_USERNAME`+`REDDIT_PASSWORD`+`REDDIT_ENABLE_WRITE=1`
     (без `REDDIT_ENABLE_WRITE=1` команда сразу падает с ошибкой — это намеренно)
   - Сначала запусти команду **без** `--confirm` — увидишь dry-run
   - Только после ревью пользователя — добавляй `--confirm`

3. **Имя сабреддита/юзера** можно передавать с префиксом или без: `r/python` ≡ `python`, `u/spez` ≡ `spez`.

## Scripts

### Auth & identity

| Script | Endpoint | Mode | Description |
|--------|----------|------|-------------|
| `auth_check.sh` | `GET /r/all/new?limit=1` | any | Sanity-check токена |
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
| `submission.sh --id I` или `--url U` | `/comments/{id}` | Пост + топ-комментарии |

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

## Cache layout

```
cache/
├── token.json                  # OAuth access token + expires_at
├── users/<username>.json       # /user/{u}/about
├── subreddits/<sub>.json       # /r/{sub}/about
├── subreddits/<sub>.rules.json
├── subreddits/<sub>.moderators.json
├── listings/<hash>.json        # листинги (search, top, posts, comments, popular)
└── me.json                     # /api/v1/me
```

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

# 4) Open a submission by URL (with comments)
sh scripts/submission.sh \
    --url "https://www.reddit.com/r/Python/comments/abc123/some_title/" \
    --limit-comments 50

# 5) DRY-RUN of a comment reply (requires REDDIT_ENABLE_WRITE=1; without --confirm only prints)
REDDIT_ENABLE_WRITE=1 sh scripts/comment_reply.sh \
    --parent-id t3_abc123 --content "Looks great!"

# 6) Real reply (after dry-run review)
REDDIT_ENABLE_WRITE=1 sh scripts/comment_reply.sh \
    --parent-id t3_abc123 --content "Looks great!" --confirm

# 7) Top posts WITH comments per post (1 API call per post — use sparingly)
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

## Differences from upstream

Оригинальный [reddit-mcp](https://github.com/Arindam200/reddit-mcp) использует PRAW + Python и зашит как MCP-сервер. Здесь — shell-скрипты, прямые curl-запросы, кеш в директории скилла. AI-driven анализ (engagement insights, optimal posting time) намеренно перенесён на сторону Claude — он сам интерпретирует JSON в `cache/`.

## Документация

- [Reddit API docs](https://www.reddit.com/dev/api/)
- [OAuth2 wiki](https://github.com/reddit-archive/reddit/wiki/OAuth2)
- [API rules / User-Agent](https://github.com/reddit-archive/reddit/wiki/API)
