---
name: yandex-search-api
description: |
  Поиск в Яндексе через Yandex Cloud Search API v2 с содержательными
  выдержками найденных страниц (smart snippets) — материал для ответа,
  а не только ссылки. Синхронный и асинхронный режимы, кэш результатов.
  Triggers: yandex search api, поиск в яндексе, выдача яндекса,
  serp яндекс, парсинг выдачи, smart snippets, смарт-сниппеты,
  выдержки страниц, найди в яндексе, что пишут в рунете.
---

# yandex-search-api

Parse Yandex SERP via Yandex Cloud Search API v2 (sync + async).

## Smart snippets

Search API отдаёт не только ссылку и сниппет в пару предложений, а
подготовленный кусок найденной страницы: до 20 документов на запрос, до 2048
токенов на документ. Текст оригинальный, не пересказ модели, — его можно
цитировать и ссылаться на источник.

Это то, ради чего скилл ставят в агента: одного запроса хватает, чтобы
ответить, а не только чтобы составить список ссылок.

Включено по умолчанию, отдельного скрипта не нужно:

```bash
bash scripts/web_search_sync.sh --query "как считается НДС для УСН в 2026" --region-id 225
```

Выдержки в ответ не печатаются — они уходят в `cache/results/<hash>.md`, а в
stdout приходит индекс по строке на документ. Дальше читай пак файлом: целиком,
если нужен весь материал, или грепом, если нужен один источник. Повторный поиск
той же фразы ради текста, который уже лежит в паке, — потраченные деньги.

Два ограничения, которые меняют выбор скрипта:

- **Только синхронный режим.** `web_search_async.sh` выдержек не вернёт.
- **Максимум 20 документов.** Вместе со snippets `--results` обрезается до 20.

Когда нужны только позиции и домены — `--no-snippets` (или
`search.smart_snippets.enabled: false` в конфиге): дешевле и без потолка в 20.

Подробности, отладка и разбор ошибок:
[references/SMART_SNIPPETS.md](references/SMART_SNIPPETS.md).

## Config

Для работы нужен сервисный аккаунт Яндекс.Облака.
Пошаговая инструкция (6 шагов, ~10 минут): [config/README.md](config/README.md).

Краткий чеклист:
1. ID каталога Яндекс.Облака → в `config.json`
2. Файл ключа сервисного аккаунта → в `config/service_account_key.json`
3. Проверка: `bash scripts/iam_token_get.sh`

> macOS: может потребоваться `brew install openssl` — подробности в config/README.md.

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
   Исключение: если нужны выдержки со страниц, режим всегда `sync` —
   в асинхронном API smart snippets нет.

3. **Verify config**: `bash scripts/iam_token_get.sh`
4. **Run search** с полученным region ID
5. **Present results**: со snippets — прочитай пак `cache/results/<hash>.md`
   и отвечай по нему, ссылаясь на источники; без snippets — позиция, заголовок,
   URL, сниппет

## Scripts

### iam_token_get.sh
Generate or validate IAM token from Service Account key.
```bash
bash scripts/iam_token_get.sh
```
Token is cached in `cache/iam_token.json` and auto-refreshed when expired.

### web_search_sync.sh
Synchronous search — one query at a time, immediate results.
Единственный режим, который умеет smart snippets.
```bash
# Single query — с выдержками страниц
bash scripts/web_search_sync.sh \
  --query "купить дымоход" \
  --region-id 213

# Только позиции и ссылки, без выдержек
bash scripts/web_search_sync.sh \
  --query "купить дымоход" \
  --region-id 213 \
  --no-snippets

# Batch from file — по строке на запрос в stdout, пак на каждый запрос
bash scripts/web_search_sync.sh \
  --file queries.txt \
  --region-id 225
```

| Param | Required | Default | Values |
|-------|----------|---------|--------|
| `--query, -q` | yes* | - | Search text |
| `--file, -f` | yes* | - | File with queries (one per line) |
| `--region-id, -r` | no | from config (225) | Region ID |
| `--results, -n` | no | 20 со snippets, иначе из конфига (10) | Results per page; со snippets обрезается до 20 |
| `--page, -p` | no | 0 | Page number |
| `--search-type` | no | SEARCH_TYPE_RU | SEARCH_TYPE_RU / SEARCH_TYPE_TR / SEARCH_TYPE_COM / SEARCH_TYPE_KK / SEARCH_TYPE_BE / SEARCH_TYPE_UZ |
| `--family-mode` | no | FAMILY_MODE_MODERATE | FAMILY_MODE_NONE / FAMILY_MODE_MODERATE / FAMILY_MODE_STRICT |
| `--snippets` | no | вкл | Просить выдержки со страниц |
| `--no-snippets` | no | - | Только ссылки и короткие сниппеты |

\* Either `--query` or `--file` is required.

Results saved to `cache/results/<hash>.md` (пак с выдержками),
`cache/results/<hash>.json` (parsed) и `cache/results/<hash>.raw` (XML).

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

**Smart snippets в асинхронном режиме недоступны** — вернутся только ссылки и
короткие сниппеты. Нужны выдержки — `web_search_sync.sh --file`.

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
- `extract` — выдержка страницы (smart snippets); пустая строка, если её нет

Results cached in `cache/results/`:
- `<hash>.md` — пак с выдержками: заголовок, URL и текст по каждому документу
- `<hash>.raw` — raw XML from API
- `<hash>.json` — parsed JSON array

`<hash>` — первые 12 символов md5 от текста запроса.

**Не печатай выдержки в stdout.** 20 документов по 2048 токенов не помещаются в
буфер вывода песочницы, и скрипт упадёт молча. Скрипты печатают индекс, тексты
читаются из пака через `Read`/`grep`.

## Tests

Офлайн-тесты: ни сети, ни сервисного аккаунта.

```bash
sh scripts/tests/run.sh
```

Проверяют сборку тела запроса, разбор XML с выдержками, отрисовку пака и разбор
флагов CLI. Тело запроса можно посмотреть и вручную, ничего не оплачивая:

```bash
YSA_DRY_RUN=1 bash scripts/web_search_sync.sh --query "тест"
```

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

Yandex Search API v2 pricing (as of 2025):
- Sync requests: billed per request
- Async requests: billed per request
- Free tier available (check current limits)
- See: https://yandex.cloud/ru/docs/search-api/pricing

## Example Session

```
User: Разберись, как в 2026 считается НДС для УСН, и дай ответ со ссылками

Claude: [Регион из контекста не следует, но вопрос общероссийский → 225]

        [Проверяет токен]
        bash scripts/iam_token_get.sh

        [Ищет с выдержками — они включены по умолчанию]
        bash scripts/web_search_sync.sh --query "НДС для УСН 2026 порядок расчёта" --region-id 225

        === Results for: НДС для УСН 2026 порядок расчёта ===
        Region: 225 | Page: 0 | Snippets: true

            #  символов  домен                     заголовок
            1      1840  nalog.gov.ru              Об изменениях в главе 26.2 НК РФ
            2      1512  consultant.ru             НДС при УСН с 2026 года
            3         0  example-blog.ru           Что изменится для упрощенцев
            ...

          Всего: 20, с выдержками: 17
          Пак:  cache/results/a1b2c3d4e5f6.md (48 KB) — читай его вместо повторного поиска

        [Читает пак и отвечает по нему, а не по заголовкам]
        Read cache/results/a1b2c3d4e5f6.md

        По порогу выручки: ...  (nalog.gov.ru)
        По ставкам: ...          (consultant.ru)
```

```
User: Найди топ выдачи яндекса по запросу "купить сэндвич дымоход" в Москве

Claude: Уточню: Москва-город или Москва и область?

User: Москва город

Claude: [Находит ID региона]
        bash scripts/search_region.sh --name "Москва"
        → Москва = 213

        [Проверяет токен]
        bash scripts/iam_token_get.sh

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

        [Проверяет токен]
        bash scripts/iam_token_get.sh

        [Режим sync из конфига — запускает батч по одному]
        bash scripts/web_search_sync.sh --file queries.txt --region-id 43
```
