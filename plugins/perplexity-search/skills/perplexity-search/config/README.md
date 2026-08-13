# perplexity-search — Config Setup

## 1. Получить API-ключ

1. Открыть [perplexity.ai/account/api](https://www.perplexity.ai/account/api)
2. Пополнить credit balance (API оплачивается отдельно от подписки Pro)
3. **Generate API key** → ключ вида `pplx-...`

## 2. Создать .env

```bash
cp config/.env.example config/.env
```

Вставить ключ в `PERPLEXITY_API_KEY`.

Файл должен оставаться shell-совместимым: значения с пробелами — в кавычках.
`scripts/sanitize_env.sh` дочинит незакавыченные значения с пробелами, но лучше
писать их правильно сразу. Ключ читается только из `.env` и никогда не печатается
в вывод — при разборе ошибок называйте имена переменных, а не значения.

Перед запуском скриптов из установленного скилла сначала создайте рабочую копию
(директория плагина в облачной песочнице обычно read-only, а скрипты пишут в `cache/`):

```bash
sh scripts/prepare_runtime.sh
cd /home/claude/perplexity-search
```

## 3. Переменные

| Переменная | Дефолт | Что делает |
|---|---|---|
| `PERPLEXITY_API_KEY` | — | обязательный ключ |
| `PPLX_PRESET` | `medium` | пресет Agent API: `fast`, `low`, `medium`, `high`, `xhigh`, `wide-research` |
| `PPLX_MODEL` | — | явная модель, перебивает модель пресета |
| `PPLX_CONTEXT_SIZE` | `medium` | глубина извлечения текста страницы: `low`/`medium`/`high` |
| `PPLX_MAX_RESULTS` | `10` | результатов на запрос Search API (1..20) |
| `PPLX_COUNTRY` | — | ISO 3166-1 alpha-2, например `RU` |
| `PPLX_LANGUAGE` | — | ISO 639-1: язык ответа для `ask.sh`, фильтр выдачи для `search.sh` |
| `PPLX_CACHE_TTL` | `900` | сколько секунд переиспользовать идентичный запрос; `0` — всегда живой |
| `PPLX_RESEARCH_CACHE_TTL` | `86400` | то же для `research.sh` |
| `PPLX_PRINT_LIMIT` | `30` | строк в stdout, остальное остаётся в `cache/` |
| `PPLX_HTTP_TIMEOUT` | `300` | `curl --max-time`, секунды |
| `PPLX_MAX_RETRIES` | `3` | попыток на 429/5xx |

### Про TTL и свежесть

Ключ кеша (тело запроса) отвечает на вопрос «какая запись подходит», TTL — на
вопрос «можно ли её ещё отдавать». Записи старше TTL не переиспользуются
никогда, поэтому годовалый ответ на повторный запрос физически не вернётся.

Три предохранителя против устаревшей фактуры:

1. Возраст печатается в шапке: `=== Perplexity Search (cache, 12m old): ... ===`.
2. `--recency` сокращает окно переиспользования, потому что это прямой сигнал
   «мне нужно свежее»: `hour` → не старше 5 минут, `day` → не старше часа,
   `week` → не старше суток. Явный `--cache-ttl` не урезается — он считается
   осознанным решением вызывающего.
3. `--no-cache` всегда идёт в API.

`PPLX_CACHE_TTL=0` полностью отключает переиспользование: каждый вызов живой.
Файлы в `cache/` при этом всё равно пишутся и остаются доступны для
`cache_grep.sh` и `find_latest.sh` — истекает только право отдать их вместо
нового запроса.

## 4. Профили источников

Профиль — именованный набор дефолтов (домены + свежесть + глубина извлечения),
который подставляется флагом `--profile`:

```bash
PPLX_PROFILES=news,science

PPLX_PROFILE_NEWS_LABEL="Tech news"
PPLX_PROFILE_NEWS_DOMAINS=techcrunch.com,theverge.com
PPLX_PROFILE_NEWS_RECENCY=week
PPLX_PROFILE_NEWS_CONTEXT=low
```

```bash
sh scripts/search.sh --profile news --query "AI regulation"
```

Правила:

- Явный флаг всегда сильнее профиля: `--profile news --recency day` → `day`.
- `PPLX_PROFILES` — реестр для агента: по нему он понимает, какие профили есть.
- Id профиля — только `[A-Za-z0-9_]`, потому что раскрывается через `eval`.
- `search_domain_filter` работает либо как allowlist, либо как denylist
  (домены с префиксом `-`), смешивать нельзя. Максимум 20 доменов.

## 5. Проверка

```bash
sh scripts/tests/run.sh                       # оффлайн-тесты, без сети и ключа
sh scripts/search.sh --query "test" --max-results 3   # первый живой запрос
```

Ошибка `PERPLEXITY_API_KEY not set` — нет `config/.env`.
Ошибка `HTTP 401` — ключ неверный или на балансе нет средств.

## Цены (на момент написания)

- Search API — **$5 за 1000 запросов**, токены не тарифицируются.
  Мультизапрос (до 5 строк в одном вызове) считается одним запросом.
- Agent API — токены по прайсу провайдера модели плюс вызовы инструментов:
  `web_search` $0.0025, `fetch_url` $0.00025, `people_search`/`finance_search` $0.005.

Актуальное: [docs.perplexity.ai/docs/getting-started/pricing](https://docs.perplexity.ai/docs/getting-started/pricing)
