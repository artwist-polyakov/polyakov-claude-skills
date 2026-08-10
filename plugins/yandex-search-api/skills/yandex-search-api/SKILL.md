---
name: yandex-search-api
description: |
  Парсинг выдачи Яндекса через Yandex Cloud Search API v2.
  Синхронный и асинхронный режимы. Кэширование результатов.
  Triggers: yandex search api, поиск в яндексе, выдача яндекса,
  serp яндекс, парсинг выдачи.
---

# yandex-search-api

Parse Yandex SERP via Yandex Cloud Search API v2 (sync + async).

## Config

Рекомендуемый способ авторизации — API-ключ Yandex AI Studio. IAM через
сервисный аккаунт также поддерживается.
Пошаговая инструкция (6 шагов, ~10 минут): [config/README.md](config/README.md).

Краткий чеклист:
1. ID каталога Яндекс.Облака → в `config.json`
2. `auth.mode: "api_key"` → в `config.json`
3. `YANDEX_AI_API_KEY` → в `config/.env`

> IAM fallback: сервисный аккаунт и OpenSSL — подробности в config/README.md.

## Workflow

### STOP! Before any search:

1. **Определи регион:**
   - Если пользователь указал город/регион — найди ID автоматически:
     ```bash
     bash scripts/search_region.sh --name "Казань"
     ```
   - Если регион не понятен из контекста — **СПРОСИ и ЖДИ ответа:**
     ```
     "Для какого региона искать?
     - Вся Россия (по умолчанию)
     - Москва
     - Конкретный город (какой?)"
     ```
     **НЕ ПРОДОЛЖАЙ пока пользователь не ответит!**
   - Полученное название → `search_region.sh --name "..."` → получаешь ID
   - Для неоднозначных случаев (Москва город vs область) — уточни у пользователя

2. **Режим поиска** берётся из `config.json` → `search.mode` (по умолчанию `sync`).
   Не спрашивай — используй то, что в конфиге.

3. **Verify config**: убедись, что заданы `yandex_cloud_folder_id`,
   `auth.mode` и соответствующий credential. Не печатай ключ или токен.
4. **Run search** с полученным region ID
5. **Present results** with position, title, URL, snippet

## Scripts

### iam_token_get.sh
Generate or validate IAM token from Service Account key. Needed only when
`auth.mode` is `iam`.
```bash
bash scripts/iam_token_get.sh
```
Token is cached in `cache/iam_token.json` and auto-refreshed when expired.

### web_search_sync.sh
Synchronous search — one query at a time, immediate results.
```bash
# Single query
bash scripts/web_search_sync.sh \
  --query "купить дымоход" \
  --region-id 213

# Batch from file
bash scripts/web_search_sync.sh \
  --file queries.txt \
  --region-id 225 \
  --results 20
```

| Param | Required | Default | Values |
|-------|----------|---------|--------|
| `--query, -q` | yes* | - | Search text |
| `--file, -f` | yes* | - | File with queries (one per line) |
| `--region-id, -r` | no | from config (225) | Region ID |
| `--results, -n` | no | 10 | Results per page (1-100) |
| `--page, -p` | no | 0 | Page number |
| `--search-type` | no | SEARCH_TYPE_RU | SEARCH_TYPE_RU / SEARCH_TYPE_TR / SEARCH_TYPE_COM / SEARCH_TYPE_KK / SEARCH_TYPE_BE / SEARCH_TYPE_UZ |
| `--family-mode` | no | FAMILY_MODE_MODERATE | FAMILY_MODE_NONE / FAMILY_MODE_MODERATE / FAMILY_MODE_STRICT |

\* Either `--query` or `--file` is required.

Results saved to `cache/results/<hash>.json` (parsed) and `cache/results/<hash>.raw` (XML).

### web_search_async.sh
Asynchronous batch search — submit many queries, poll for results.
```bash
# Submit batch and wait
bash scripts/web_search_async.sh \
  --file queries.txt \
  --region-id 213

# Resume after timeout/interrupt
bash scripts/web_search_async.sh --resume
```

| Param | Required | Default | Values |
|-------|----------|---------|--------|
| `--file, -f` | yes | - | File with queries |
| `--region-id, -r` | no | from config (225) | Region ID |
| `--poll-interval` | no | 10 | Poll interval (minutes) |
| `--max-wait` | no | 120 | Max wait before timeout (minutes) |
| `--resume` | no | - | Continue polling pending ops |

**Async workflow:**
1. Script submits all queries as async operations
2. Polls every `poll_interval` minutes for completion
3. Downloads and parses results as they complete
4. If `max_wait` exceeded: prints summary + resume command
5. On restart with `--resume`: continues from `cache/ops/`, no duplicates

**NOTE for agent:** Async execution can take minutes to hours.
The script handles polling automatically. If it times out,
re-run with `--resume` to continue.

### regions_tree.sh
Show common region IDs.
```bash
bash scripts/regions_tree.sh
```

### search_region.sh
Find region ID by name.
```bash
bash scripts/search_region.sh --name "Казань"
```

## Output Format

Each search result contains:
- `position` — rank in SERP
- `title` — page title
- `url` — page URL
- `snippet` — text snippet (up to 300 chars)
- `domain` — site domain

Results cached in `cache/results/`:
- `<hash>.raw` — raw XML from API
- `<hash>.json` — parsed JSON array

## Popular Region IDs

| Region | ID |
|--------|-----|
| Россия | 225 |
| Москва | 213 |
| Москва и область | 1 |
| Санкт-Петербург | 2 |
| Екатеринбург | 54 |
| Новосибирск | 65 |
| Казань | 43 |

Run `bash scripts/regions_tree.sh` for full list.

## Pricing

Search requests may be billable. Check current terms before running a large
batch: https://yandex.cloud/ru/docs/search-api/pricing

## Example Session

```
User: Найди топ выдачи яндекса по запросу "купить сэндвич дымоход" в Москве

Claude: Уточню: Москва-город или Москва и область?

User: Москва город

Claude: [Находит ID региона]
        bash scripts/search_region.sh --name "Москва"
        → Москва = 213

        [Проверяет конфигурацию авторизации без вывода секрета]

        [Выполняет поиск — 1 запрос, автоматически sync]
        bash scripts/web_search_sync.sh --query "купить сэндвич дымоход" --region-id 213

        Результаты поиска (топ-10):

        1. Сэндвич-дымоходы купить в Москве — Леруа Мерлен
           https://leroymerlin.ru/...
           Широкий ассортимент сэндвич-дымоходов...

        2. Дымоходы сэндвич — купить в интернет-магазине
           https://...
           ...
```

```
User: Проверь выдачу по запросам из файла queries.txt в Казани

Claude: [Находит ID региона]
        bash scripts/search_region.sh --name "Казань"
        → Казань = 43

        [Проверяет конфигурацию авторизации без вывода секрета]

        [Режим sync из конфига — запускает батч по одному]
        bash scripts/web_search_sync.sh --file queries.txt --region-id 43
```
