---
name: yandex-search-api
description: |
  Поиск в Яндексе через Yandex Cloud Search API v2 с содержательными
  выдержками найденных страниц (smart snippets) — материал для ответа,
  а не только ссылки. Синхронный и асинхронный режимы, кэш результатов.
  Triggers: yandex search api, поиск в яндексе, выдача яндекса,
  serp яндекс, парсинг выдачи, smart snippets, смарт-сниппеты,
  инфоконтексты, выдержки страниц, найди в яндексе, что пишут в рунете.
---

# yandex-search-api

Parse Yandex SERP via Yandex Cloud Search API v2 (sync + async).

## Инфоконтексты (Smart Snippets)

Search API отдаёт не только ссылку и сниппет в пару предложений, а фрагмент
найденной страницы примерно на 500 токенов — релевантный запросу и с цитатами
из документа. Информации уже достаточно, чтобы ответить, а обрабатывать
страницу самому не нужно.

Это то, ради чего скилл ставят в агента: одного запроса хватает на ответ, а не
только на список ссылок.

Включено по умолчанию, отдельного скрипта не нужно:

```bash
bash scripts/web_search_sync.sh --query "как считается НДС для УСН в 2026" --region-id 225
```

Тексты в ответ не печатаются — они уходят в `cache/results/<hash>.md`, а в
stdout приходит индекс по строке на документ. Дальше читай пак файлом: целиком,
если нужен весь материал, или грепом, если нужен один источник. Повторный поиск
той же фразы ради текста, который уже лежит в паке, — потраченные деньги.

Три ограничения, которые меняют выбор команды:

- **Только синхронный режим.** `web_search_async.sh` инфоконтекстов не вернёт.
- **Только `SEARCH_TYPE_RU`.** С другим типом поиска скилл выполнит запрос, но
  без инфоконтекстов, и скажет об этом.
- **Не больше 20 документов.** Попросить больше нельзя: при `--results` выше 20
  API отдаёт не 20, а вдвое меньше — по той же цене. Скилл клампит сам.
- **Дороже.** 1 500 ₽ за 1000 запросов против 488 ₽ у обычного синхронного.

Когда нужны только позиции и домены — `--no-snippets` (или
`search.smart_snippets.enabled: false` в конфиге).

Подробности, формат ответа, отладка и разбор ошибок:
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
   Исключение: если нужны инфоконтексты, режим всегда `sync`, а тип поиска —
   `SEARCH_TYPE_RU`; в асинхронном API и в других поисковых базах их нет.

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
| `--results, -n` | no | 20 со snippets, иначе из конфига (10) | Со snippets клампится до 20; без них 1-100 |
| `--page, -p` | no | 0 | Page number |
| `--search-type` | no | SEARCH_TYPE_RU | SEARCH_TYPE_RU / SEARCH_TYPE_TR / SEARCH_TYPE_COM / SEARCH_TYPE_KK / SEARCH_TYPE_BE / SEARCH_TYPE_UZ |
| `--family-mode` | no | FAMILY_MODE_MODERATE | FAMILY_MODE_NONE / FAMILY_MODE_MODERATE / FAMILY_MODE_STRICT |
| `--snippets` | no | вкл | Просить выдержки со страниц |
| `--no-snippets` | no | - | Только ссылки и короткие сниппеты |

\* Either `--query` or `--file` is required.

Results saved to `cache/results/<hash>.md` (пак с выдержками),
`cache/results/<hash>.json` (parsed) и `cache/results/<hash>.raw` — исходное
тело ответа: JSON с инфоконтекстами, XML без них.

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

**Инфоконтексты в асинхронном режиме недоступны** — вернутся только ссылки и
короткие сниппеты. Нужны фрагменты страниц — `web_search_sync.sh --file`.
Зато async в ~16 раз дешевле синхронного и в ~50 раз дешевле инфоконтекстов.

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
- `extract` — инфоконтекст: фрагмент страницы; пустая строка, если его нет

Results cached in `cache/results/`:
- `<hash>.md` — пак: заголовок, URL и инфоконтекст по каждому документу
- `<hash>.raw` — исходное тело ответа: XML для обычной выдачи, **JSON** для
  инфоконтекстов (формат выбирает сервер)
- `<hash>.json` — parsed JSON array, единый формат для обоих случаев

`<hash>` — первые 12 символов md5 от текста запроса.

**Не печатай инфоконтексты в stdout.** 20 документов по ~500 токенов не
помещаются в буфер вывода песочницы, и скрипт упадёт молча. Скрипты печатают
индекс, тексты читаются из пака через `Read`/`grep`.

## Tests

Офлайн-тесты: ни сети, ни сервисного аккаунта.

```bash
sh scripts/tests/run.sh
```

Проверяют сборку тела запроса, разбор обоих форматов ответа (XML и JSON),
отрисовку пака и разбор флагов CLI. Тело запроса можно посмотреть и вручную,
ничего не оплачивая:

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

Тарификация — за один запрос. Цены за 1000 запросов, с НДС:

| Тип запроса | ₽ |
|---|---|
| Дневные синхронные | 488 |
| Ночные синхронные (00:00–07:59 UTC+3) | 366 |
| Дневные отложенные (async) | 30,5 |
| Запросы инфоконтекста поиска в регионе RU | 1 500 |

Инфоконтексты дороже обычного синхронного запроса примерно втрое. Если нужны
только позиции по большому списку запросов — async или `--no-snippets`.

Актуальный прайс:
https://aistudio.yandex.ru/docs/ru/search-api/pricing

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

        [Задача про позиции в выдаче, содержание страниц не нужно —
         отключает инфоконтексты: дешевле втрое]
        bash scripts/web_search_sync.sh --query "купить сэндвич дымоход" \
          --region-id 213 --no-snippets

        === Results for: купить сэндвич дымоход ===
        Region: 213 | Page: 0 | Snippets: false

          1. Сэндвич-дымоходы купить в Москве — Леруа Мерлен
             https://leroymerlin.ru/...
             Широкий ассортимент сэндвич-дымоходов...

          2. Дымоходы сэндвич — купить в интернет-магазине
             https://...
             ...

          Всего: 10, с выдержками: 0
          JSON: cache/results/7f3a91c2e5d8.json
          Raw:  cache/results/7f3a91c2e5d8.raw
```

```
User: Проверь выдачу по запросам из файла queries.txt в Казани

Claude: [Находит ID региона]
        bash scripts/search_region.sh --name "Казань"
        → Казань = 43

        [Проверяет токен]
        bash scripts/iam_token_get.sh

        [Батч по одному запросу; в stdout — строка на запрос,
         тексты и разбор лежат в файлах]
        bash scripts/web_search_sync.sh --file queries.txt --region-id 43

          запрос                                     результаты            пак
          дымоход сэндвич цена                        20 док., выдержек 18  cache/results/a1b2c3d4e5f6.md
          купить дымоход казань                       20 док., выдержек 15  cache/results/b2c3d4e5f6a1.md
          дымоход нержавейка                          20 док., выдержек 19  cache/results/c3d4e5f6a1b2.md

        === Batch complete: 3/3 queries processed ===
```
