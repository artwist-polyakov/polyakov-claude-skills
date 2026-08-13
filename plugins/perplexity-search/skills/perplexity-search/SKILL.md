---
name: perplexity-search
description: |
  Поиск и ресёрч через Perplexity API: сырая выдача (Search API),
  ответ с цитатами (Agent API), deep research в фоне, чтение страниц.
  Cache-first: крупные результаты уходят в cache/ и читаются грепом.
  Triggers: perplexity, перплексити, perplexity search, sonar api,
  найди в интернете, поищи в сети, web search, свежая информация,
  deep research, глубокое исследование, ответ с источниками,
  прочитай страницу, fetch url, что пишут про.
---

# perplexity-search

Работа с [Perplexity API](https://docs.perplexity.ai) на POSIX-shell. Четыре режима:
сырая выдача, ответ с цитатами, длинный ресёрч в фоне, извлечение страниц.

## Перед запуском

Директория установленного плагина обычно read-only, а скрипты пишут в `cache/`.
Создай рабочую копию и работай из неё:

```bash
sh scripts/prepare_runtime.sh
cd /home/claude/perplexity-search
```

`prepare_runtime.sh` копирует скилл и нормализует `config/.env` (закавычивает
значения с пробелами). Содержимое `.env` не выводи в ответ — при ошибках
настройки называй только имена переменных.

## Config

Нужен `PERPLEXITY_API_KEY` в `config/.env`:

```bash
cp config/.env.example config/.env
```

Ключ — на [perplexity.ai/account/api](https://www.perplexity.ai/account/api).
Все остальные переменные опциональны. Подробности: [config/README.md](config/README.md).

## Philosophy

1. **Кеш ограничен по возрасту, а не только по ключу** — ключ (тело запроса)
   решает, *какая* запись подходит; TTL решает, можно ли её ещё отдавать.
   По умолчанию 900 c, для `research.sh` — сутки. Ничего старше не переиспользуется.
   Возраст всегда печатается в шапке (`cache, 12m old`), чтобы несвежий ответ
   было видно. `--no-cache` — живой запрос, `--cache-ttl` — своя граница.
2. **Context window hygiene** — stdout ограничен `PPLX_PRINT_LIMIT` (30 строк).
   Полные сниппеты, ответы и сырой JSON лежат в `cache/` и читаются через
   `cache_grep.sh` или Read по нужному смещению.
3. **Правильный инструмент под задачу** — сначала дешёвый `search.sh`
   ($5/1000 запросов), синтез моделью только когда он реально нужен.
4. **Проверяемые ответы** — у каждого ответа `ask.sh`/`research.sh` в отчёте
   есть список источников с датами и сниппетами.

## Выбор скрипта

| Нужно | Скрипт | Цена |
|---|---|---|
| ссылки и цитаты со страниц, дальше думаю сам | `search.sh` | $5 / 1000 запросов |
| готовый ответ с источниками | `ask.sh` | токены модели + $0.0025 за поиск |
| многошаговое исследование, отчёт на страницы | `research.sh` | минуты и заметные деньги |
| содержимое конкретных URL | `fetch_url.sh` | токены + $0.00025 за URL |
| «я это уже искал?» | `find_latest.sh`, `cache_grep.sh` | бесплатно |

По умолчанию бери `search.sh`. `research.sh` запускай только когда пользователь
явно просит глубокое исследование.

## Workflow

### Сырая выдача

```bash
# один запрос
sh scripts/search.sh --query "изменения в NDS 2026" --max-results 10

# до 5 углов темы в одном оплаченном запросе
sh scripts/search.sh \
  --query "AI regulation EU 2026" \
  --query "AI Act enforcement timeline" \
  --query "AI Act penalties companies"

# только свежее и только из нужных доменов
sh scripts/search.sh --query "postgres 19 release" --recency week \
  --domains "postgresql.org,news.ycombinator.com"

# без форумов
sh scripts/search.sh --query "лучший ноутбук для разработки" --domains "-reddit.com,-quora.com"
```

stdout — таблица `n / date / domain / title / url`. Сниппеты целиком лежат в
`cache/search/<key>.txt`; читай их грепом, а не целиком.

### Ответ с цитатами

```bash
sh scripts/ask.sh --query "что изменилось в Claude Code за последний месяц"

# дешевле и быстрее
sh scripts/ask.sh --query "курс ЦБ на сегодня" --preset fast

# ответ строго по схеме
sh scripts/ask.sh --query "топ-5 CRM для малого бизнеса" --schema schema.json
```

Полный отчёт (ответ + источники + сниппеты) — в `cache/ask/<key>.md`.

### Глубокий ресёрч

```bash
# фоновый запуск с ожиданием
sh scripts/research.sh --query "рынок EV-зарядок в РФ: игроки, объёмы, барьеры" \
  --preset high --timeout 1800

# отправить и не ждать
sh scripts/research.sh --query "..." --no-wait
sh scripts/research.sh --resume resp_abc123
```

Run продолжается на стороне Perplexity даже если скрипт перестал ждать —
`--resume` подхватывает его по id.

### Чтение страниц

```bash
sh scripts/fetch_url.sh --url "https://example.com/pricing"
sh scripts/fetch_url.sh --url "https://a.com/post" --url "https://b.com/post" \
  --query "сравни выводы двух статей"
```

### Работа с кешем

```bash
# что я уже спрашивал
sh scripts/find_latest.sh --script ask --match "claude"

# поиск по накопленным результатам вместо нового запроса
sh scripts/cache_grep.sh "rate limit" --type search --context 2
sh scripts/cache_grep.sh "GDPR" --files
```

## Scripts

```bash
sh scripts/<script>.sh [params]     # у каждого есть --help
```

| Script | Endpoint | Описание |
|---|---|---|
| `search.sh` | `POST /search` | ранжированная выдача со сниппетами |
| `ask.sh` | `POST /v1/agent` | синтезированный ответ с источниками |
| `research.sh` | `POST /v1/agent` + polling | длинный ресёрч в фоне, `--resume` |
| `fetch_url.sh` | `POST /v1/agent` | содержимое конкретных URL (`fetch_url`) |
| `find_latest.sh` | — | прошлые запуски из `cache/index.tsv` |
| `cache_grep.sh` | — | греп по накопленным результатам |
| `prepare_runtime.sh` | — | рабочая копия скилла в writable-директории |

## Общие параметры

| Param | Где | Default | Значения |
|---|---|---|---|
| `--query`, `-q` | все | — | запрос; в `search.sh` повторяется до 5 раз |
| `--recency` | поиск | — | `hour`, `day`, `week`, `month`, `year` |
| `--after` / `--before` | поиск | — | `YYYY-MM-DD` или `MM/DD/YYYY` |
| `--updated-after` / `--updated-before` | поиск | — | по дате обновления страницы |
| `--domains` | поиск | — | allowlist `a.com,b.com` **или** denylist `-a.com` |
| `--country` | поиск | — | ISO 3166-1 alpha-2 |
| `--language` | поиск | — | ISO 639-1 |
| `--context-size` | поиск | `medium` | `low`, `medium`, `high` |
| `--max-results` | поиск | `10` | Search: 1–20, Agent: 1–50 |
| `--profile` | поиск | — | набор дефолтов из `.env` |
| `--preset` | agent | см. ниже | `fast`, `low`, `medium`, `high`, `xhigh`, `wide-research` |
| `--model` | agent | из `.env` | `perplexity/sonar`, `openai/gpt-5.6-sol`, `anthropic/claude-sonnet-5`, … |
| `--tools` | agent | `web_search` | `web_search`, `fetch_url`, `finance_search`, `people_search`, `sandbox` |
| `--instructions` | agent | — | системная инструкция |
| `--schema` | agent | — | файл JSON Schema → структурированный ответ |
| `--limit` | все | `30` | строк в stdout |
| `--cache-ttl`, `--no-cache` | все | `900` | управление кешем |

Взаимоисключающие комбинации (`--recency` вместе с явными датами, смешанные
allow/deny домены) отклоняются до похода в API — деньги не тратятся.

### Кто исполняет запрос

Приоритет: **флаг > `.env` > собственный дефолт скрипта**.

- задан `--preset` или `--model` — берётся он;
- иначе, если в `config/.env` заданы `PPLX_PRESET` / `PPLX_MODEL`, — берутся они;
- иначе работает дефолт скрипта: `ask.sh` → `medium`, `research.sh` → `high`,
  `fetch_url.sh` → `low`.

Скрипт со своим дефолтом никогда не перебивает то, что вы указали в `.env`, —
иначе `research.sh` тихо считал бы деньги по другой модели.

## Кеш

```
cache/
├── index.tsv              # created_at, script, key, query, path — для grep
├── search/<key>.json      # сырой ответ
├── search/<key>.tsv       # таблица результатов
├── search/<key>.txt       # сниппеты целиком
├── ask/<key>.{json,md}    # ответ + источники
├── research/<key>.{json,md,id}
└── fetch/<key>.{json,md}
```

Никогда не читай `.json` целиком — там весь ответ API. Работай через
`cache_grep.sh`, а точечно — Read по `file:line` из его вывода.

### Свежесть

Кеш нужен, чтобы не платить дважды за один и тот же вопрос внутри сессии, а не
чтобы законсервировать ответ. Поэтому:

- переиспользуется только запись моложе TTL; шапка всегда показывает возраст
  (`=== Perplexity Search (cache, 12m old): ... ===`);
- `--recency` — это сигнал «вопрос про свежее», и он **сокращает** окно
  переиспользования: `hour` → максимум 5 минут, `day` → максимум час,
  `week` → максимум сутки. Явный `--cache-ttl` сильнее — он не урезается;
- если фактура могла измениться (релиз, курс, новость, цены) — `--no-cache`;
- `research.sh` держит отчёты сутки, потому что каждый прогон стоит минуты и
  доллары. Для быстро меняющейся темы ставь `--no-cache` или `--recency`.

Артефакты в `cache/` живут вечно — истекает только право отдать их вместо
нового запроса. Прошлые прогоны всегда доступны через `find_latest.sh` и
`cache_grep.sh`.

## Ограничения

- Search API: максимум 5 запросов в массиве, 20 результатов, 20 доменов в фильтре,
  50 query units/s. Мультизапрос тарифицируется как один запрос, но лимит
  расходует по единице на строку.
- `search_domain_filter` — allowlist **или** denylist, смешивать нельзя.
- `--recency` и явные даты одновременно не работают.
- Модели `anthropic/*` требуют `max_output_tokens` — скрипты подставляют 8192.
- 429 не тарифицируется; скрипты уважают `Retry-After` и делают до
  `PPLX_MAX_RETRIES` попыток.
- Sonar Chat Completions (`/chat/completions`) считается legacy — скилл ходит
  в Agent API (`/v1/agent`).

Детали API, схемы ответов и рецепты: [references/API_REFERENCE.md](references/API_REFERENCE.md).

## Тесты

```bash
sh scripts/tests/run.sh
```

Оффлайн, без сети и ключа: разбор `.env`, сборка тел запросов, рендер ответов из
фикстур, CLI-контракт. Тест HTTP-слоя поднимает mock на loopback и сам себя
пропускает там, где песочница не даёт открыть сокет.
