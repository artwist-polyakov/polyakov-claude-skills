# Perplexity API — reference

Что скилл реально отправляет и как разбирает ответы. Источник: docs.perplexity.ai
(разбор от 2026-08). Актуальные цифры всегда сверяй с документацией.

## Endpoints

| Endpoint | Метод | Используется в |
|---|---|---|
| `https://api.perplexity.ai/search` | POST | `search.sh` |
| `https://api.perplexity.ai/v1/agent` | POST | `ask.sh`, `research.sh`, `fetch_url.sh` |
| `https://api.perplexity.ai/v1/agent/{id}` | GET | `research.sh` (polling) |
| `https://api.perplexity.ai/v1/agent/{id}/cancel` | POST | не используется |

Авторизация — `Authorization: Bearer $PERPLEXITY_API_KEY`. Скрипты передают
ключ в curl через `--config -` (stdin), чтобы он не появлялся в `ps` и не
ложился на диск.

`POST /v1/responses` — алиас `/v1/agent` для совместимости с OpenAI SDK.
Старый `POST /chat/completions` (Sonar) помечен как legacy; скилл его не трогает.

## Search API — `POST /search`

### Запрос

```json
{
  "query": "single string OR array of up to 5 strings",
  "max_results": 10,
  "search_context_size": "low | medium | high",
  "country": "RU",
  "search_language_filter": ["en", "ru"],
  "search_domain_filter": ["nature.com", "science.org"],
  "search_recency_filter": "hour | day | week | month | year",
  "search_after_date_filter": "01/15/2026",
  "search_before_date_filter": "05/01/2026",
  "last_updated_after_filter": "01/01/2026",
  "last_updated_before_filter": "12/31/2026",
  "max_tokens": 100000,
  "max_tokens_per_page": 2000
}
```

- `max_results` — 1..20, дефолт 10.
- `search_context_size` нельзя комбинировать с `max_tokens` / `max_tokens_per_page`.
  Скилл всегда шлёт `search_context_size` и токенные бюджеты не использует.
- Даты — строго `MM/DD/YYYY`. `normalize_date` в `common.sh` принимает и
  `YYYY-MM-DD`, и переводит сам.
- `search_domain_filter` — до 20 записей, allowlist **или** denylist (`-domain.com`).
  Поддерживаются корневые домены (с поддоменами), TLD (`.gov`), пути
  (`nature.com/articles`, матчится по границам сегментов).
- Мультизапрос: один биллинговый запрос, но rate limit списывает по единице на строку.

### Ответ

```json
{
  "id": "uuid",
  "server_time": null,
  "results": [
    {"title": "…", "url": "https://…", "snippet": "…", "date": "2026-05-01", "last_updated": "2026-06-02"}
  ]
}
```

`render_search_response` раскладывает это в `.tsv` (n, date, domain, title, url)
и `.txt` (блоки со сниппетами). Табы и переводы строк в заголовках схлопываются,
иначе они ломают TSV.

## Agent API — `POST /v1/agent`

### Запрос

```json
{
  "input": "вопрос или бриф",
  "preset": "medium",
  "model": "openai/gpt-5.6-sol",
  "instructions": "системная инструкция",
  "language_preference": "ru",
  "max_output_tokens": 8192,
  "background": true,
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "medium",
      "max_results": 10,
      "user_location": {"country": "RU"},
      "filters": {
        "search_domain_filter": ["-reddit.com"],
        "search_recency_filter": "day",
        "search_after_date_filter": "01/15/2026",
        "search_before_date_filter": "05/01/2026",
        "last_updated_after_filter": "01/01/2026",
        "last_updated_before_filter": "12/31/2026"
      }
    },
    {"type": "fetch_url"}
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {"name": "result", "schema": {"type": "object"}}
  }
}
```

Что важно знать:

- Обязателен либо `model`, либо `preset`. Их можно сочетать: `model` перекрывает
  модель пресета, остальные настройки пресета остаются.
- Инструменты **мержатся** с инструментами пресета по типу, а не заменяют список
  целиком: передав настроенный `web_search`, ты не теряешь `fetch_url` из пресета.
- `max_output_tokens` обязателен для моделей `anthropic/*`. `build_agent_body`
  подставляет 8192, если пользователь не задал своё.
- `web_search.max_results` — до 50 (у Search API потолок 20).
- `background: true` возвращает `id` и `status` сразу; результат забирается
  через `GET /v1/agent/{id}`.

### Пресеты

| Preset | Модель | Инструменты | Для чего |
|---|---|---|---|
| `fast` | openai/gpt-5.4-mini | web_search | быстрые фактические справки |
| `low` | openai/gpt-5.6-luna | web_search, fetch_url | повседневный ресёрч |
| `medium` | openai/gpt-5.6-luna | web_search, fetch_url | многошаговый обход источников |
| `high` | openai/gpt-5.6-sol | web_search, fetch_url | экспертный разбор, широкое покрытие |
| `xhigh` | openai/gpt-5.6-sol | web_search, finance_search, sandbox | агентная работа с кодом |
| `wide-research` | openai/gpt-5.6-sol | web_search, finance_search, sandbox | большие подборки с доказательствами |

Пресеты не версионируются: Perplexity обновляет их состав, сохраняя профиль
цены и задержки. Нужна фиксация — задавай `model` и `tools` явно.

### Модели

Perplexity: `perplexity/sonar`, `perplexity/glm-5.2`, `perplexity/kimi-k3`,
`perplexity/deepseek-v4-flash-0731`, `perplexity/nemotron-*`.
Сторонние: `openai/gpt-5.6-{sol,terra,luna}`, `openai/gpt-5.4[-mini|-nano]`,
`anthropic/claude-{opus-5,sonnet-5,haiku-4-5,…}`, `google/gemini-3.*`,
`xai/grok-4.*`. Полный список — `docs.perplexity.ai/docs/agent-api/models`.

### Ответ

```json
{
  "id": "resp_xxx",
  "object": "response",
  "created_at": 1771891464,
  "status": "completed",
  "model": "openai/gpt-5.4-mini",
  "output": [
    {"type": "search_results", "queries": ["…"], "results": [{"id": 1, "title": "…", "url": "…", "snippet": "…", "date": "…", "source": "web"}]},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "…"}]}
  ],
  "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300,
            "cost": {"currency": "USD", "total_cost": 0.0007}}
}
```

Типы элементов `output`: `message`, `search_results`, `fetch_url_results`,
`finance_results`, `people_search_results`, `sandbox_results`, `function_call`,
`mcp_list_tools`, `mcp_call`, `tool_search_output`.

`render_agent_response` собирает текст из всех `message`, дедуплицирует источники
по URL, добавляет запросы, которые модель реально задавала, и падает с ошибкой,
если `status == "failed"` — чтобы пустой ответ не выглядел как успешный.

### Background mode

```
POST /v1/agent  {"background": true, …}        → {"id": "resp_…", "status": "queued"}
GET  /v1/agent/{id}                            → пока non-terminal: queued | in_progress
                                                 terminal: completed | failed | cancelled | incomplete
POST /v1/agent/{id}/cancel                     → status: "cancelling"
```

`research.sh` опрашивает раз в `--poll` секунд (дефолт 15) до `--timeout`
(дефолт 1800). По таймауту скрипт выходит с кодом 0 и печатает id: run на
стороне Perplexity продолжается, `--resume <id>` подхватывает его позже.

Есть ещё стриминговый реконнект (`GET /v1/agent/{id}?stream=true&starting_after=N`) —
скилл его не использует, поллинга достаточно.

## Rate limits

| API | Лимит |
|---|---|
| Search | 50 query units/s на всех тирах, burst 50; мультизапрос — 1 unit на строку |
| Agent | Tier 0: 1 QPS / 50 rpm → Tier 4–5: 33 QPS / 2000 rpm |

429 возвращает `Retry-After` и **не тарифицируется**. Алгоритм — leaky bucket,
токены восстанавливаются непрерывно.

## Цены (2026-08)

| Что | Цена |
|---|---|
| Search API | $5 / 1000 запросов, токены не считаются |
| `web_search` (Agent) | $2.50 / 1000 вызовов |
| `fetch_url` | $0.25 / 1000 вызовов |
| `people_search`, `finance_search` | $5 / 1000 вызовов |
| Токены моделей | по прайсу провайдера, без наценки |

Токены сверх инструментов считаются отдельно, поэтому `ask.sh --preset fast`
на простом вопросе стоит центы, а `research.sh --preset xhigh` — доллары.

## Рецепты

### Только свежие новости за неделю, без агрегаторов

```bash
sh scripts/search.sh --query "нейросети регулирование" \
  --recency week --domains "-news.google.com,-dzen.ru" --max-results 15
```

### Структурированный ответ по схеме

```bash
cat > /tmp/schema.json <<'EOF'
{
  "type": "object",
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "price_rub": {"type": "number"},
          "source": {"type": "string"}
        },
        "required": ["name", "source"]
      }
    }
  },
  "required": ["items"]
}
EOF
sh scripts/ask.sh --query "тарифы российских облаков на 2026" --schema /tmp/schema.json
```

Ответ приходит валидным JSON в теле сообщения; он же лежит в `cache/ask/<key>.md`
и в сыром виде в `.json`.

### Сравнение двух статей

```bash
sh scripts/fetch_url.sh \
  --url "https://a.example/post" \
  --url "https://b.example/post" \
  --query "в чём авторы расходятся и какие цифры приводят"
```

### Ресёрч, переживающий обрыв сессии

```bash
sh scripts/research.sh --query "рынок X: игроки, доли, барьеры входа" --no-wait
# → response id: resp_abc123
sh scripts/research.sh --resume resp_abc123
```

### Дешёвая проверка факта

```bash
sh scripts/search.sh --query "точная формулировка" --max-results 3 --context-size low
```

Три результата с короткими сниппетами — этого обычно хватает, чтобы подтвердить
или опровергнуть утверждение, и это в разы дешевле вызова модели.
