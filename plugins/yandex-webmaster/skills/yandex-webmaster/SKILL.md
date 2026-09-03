---
name: yandex-webmaster
description: |
  Управление сайтами через Yandex Webmaster API: индексация, поисковые запросы,
  сайтмапы, переобход, ссылки, фиды, диагностика. Плюс scraping раздела
  Alice / Share of Voice (нет публичного API).
  Triggers: яндекс вебмастер, yandex webmaster, webmaster api,
  вебмастер индексация, вебмастер запросы, вебмастер переобход,
  share of voice, sov, алиса, alice efficiency, конкуренты в алисе.
---

# yandex-webmaster

Работа с Yandex Webmaster API v4. Управление сайтами, анализ индексации, поисковые запросы, переобход страниц, ссылки, фиды.

## Config

Требуется `YANDEX_WEBMASTER_TOKEN` в `config/.env`.
Scope: `webmaster:hostinfo` + `webmaster:verify`.
Инструкция: `config/README.md`.

Для `alice.sh` (Share of Voice, без публичного API) дополнительно нужен
`SESSION_ID` — cookie из браузера. Подробности в `config/README.md` и
[references/ALICE_EFFICIENCY.md](references/ALICE_EFFICIENCY.md).

## Philosophy

1. **Cache-first** — список сайтов, user_id кешируются надолго. Отчёты кешируются по ключу host+params. Диагностика, квоты, статусы — всегда live.
2. **Context window hygiene** — stdout ограничен 30 строками. Полные данные в TSV/файл. Кеш доступен через grep/rg.
3. **Host resolution** — все скрипты принимают `--host <domain>` (поиск по кешу hosts.tsv) или `--host-id <id>` (прямой ID). Первый вызов hosts.sh автоматически кеширует список.
4. **No destructive ops** — скилл не удаляет сайты, сайтмапы и фиды. Только чтение и добавление.

## Workflow

### STOP! Перед любым анализом:

1. **Получи список сайтов:**
   ```bash
   bash scripts/hosts.sh
   ```

2. **Спроси пользователя** (если сайт не очевиден из контекста):
   ```
   "О каком сайте идёт речь?
   Укажите домен или host_id из списка."
   ```
   Для поиска по кешу:
   ```bash
   bash scripts/hosts.sh --search "example"
   ```

3. **Получи сводку по сайту:**
   ```bash
   bash scripts/summary.sh --host example.com
   ```

4. **Запускай нужные отчёты** по задаче пользователя.

## Scripts

Общий паттерн вызова:
```bash
bash scripts/<script>.sh --host <domain> [--action <action>] [params...]
```

### Управление сайтами

| Script | Description | Key params |
|--------|-------------|------------|
| `hosts.sh` | Список сайтов | `--search "text"`, `--no-cache` |
| `host_info.sh` | Инфо о сайте + владельцы | — |
| `add_site.sh` | Добавить сайт | `--url <url>` |
| `verify.sh` | Верификация сайта | `--action get\|start`, `--method DNS\|HTML_FILE\|META_TAG` |

### Статистика сайта

| Script | Description | Key params |
|--------|-------------|------------|
| `summary.sh` | Сводка: SQI, страницы, проблемы | — |
| `sqi_history.sh` | История SQI | `--date-from`, `--date-to` |
| `diagnostics.sh` | Проблемы сайта (live) | — |

### Поисковые запросы

| Script | Description | Key params |
|--------|-------------|------------|
| `popular_queries.sh` | Топ запросов | `--order-by`, `--device`, `--limit` |
| `queries_history.sh` | История запросов | `--query-id` (опц.), `--device` |
| `query_analytics.sh` | Расширенная аналитика (POST) | `--text-indicator`, `--filter-*`, `--region-ids` |

### Индексация

| Script | Description | Key params |
|--------|-------------|------------|
| `indexing.sh` | История/сэмплы индексации | `--action history\|samples` |
| `important_urls.sh` | Важные URL | `--action list\|history`, `--url` |
| `archive_export.sh` | Экспорт всех страниц | `--action start\|status`, `--task-id` |

### Страницы в поиске

| Script | Description | Key params |
|--------|-------------|------------|
| `insearch.sh` | Страницы в выдаче | `--action history\|samples` |
| `search_events.sh` | Появление/исчезновение | `--action history\|samples` |

### Переобход

| Script | Description | Key params |
|--------|-------------|------------|
| `recrawl.sh` | Переобход URL | `--action submit\|status\|list\|quota`, `--url`, `--task-id` |

### Ссылки

| Script | Description | Key params |
|--------|-------------|------------|
| `internal_links.sh` | Битые внутренние ссылки | `--action samples\|history`, `--indicator` |
| `external_links.sh` | Внешние ссылки | `--action samples\|history` |

### Сайтмапы

| Script | Description | Key params |
|--------|-------------|------------|
| `sitemaps.sh` | Управление сайтмапами | `--action list\|user-list\|info\|add\|recrawl-limit\|recrawl` |

### Alice / Share of Voice (SSR scraping)

| Script | Description | Key params |
|--------|-------------|------------|
| `alice.sh` | Эффективность в Алисе: SoV timeline, конкуренты, запросы где сайт есть/нет | `--action summary\|sov\|competitors\|with-site\|without-site\|fetch`, `--no-cache` |

> ⚠ Нет публичного API — данные парсятся из `window._initData` HTML-страницы.
> Для загрузки нужен `SESSION_ID` cookie в `config/.env`. Корректный кеш используется
> 24 часа без загрузки страницы; `--no-cache` и `--action fetch` обновляют его принудительно.
> Если обновление не удалось, команда завершается с ошибкой, сохраняя прежний JSON.
> Диагностика и повторы: [references/ALICE_EFFICIENCY.md](references/ALICE_EFFICIENCY.md).

### Фиды и PRO

| Script | Description | Key params |
|--------|-------------|------------|
| `feeds.sh` | YML-фиды | `--action list\|add\|change\|add-status`, `--type`, `--region-ids` |
| `search_export.sh` | PRO SERP экспорт | `--action dates\|limits\|start\|status` |
| `regions.sh` | Справочник регионов | `--filter "москва"` |

## Общие параметры

| Param | Description |
|-------|-------------|
| `--host <domain>` | Домен/URL сайта (поиск по hosts.tsv) |
| `--host-id <id>` | Прямой host_id (формат: `http:example.com:80`) |
| `--action <act>` | Подкоманда скрипта |
| `--date-from` | Начало периода YYYY-MM-DD (history: default 90 дней назад) |
| `--date-to` | Конец периода YYYY-MM-DD |
| `--limit N` | Число записей |
| `--offset N` | Смещение |
| `--no-cache` | Пропустить кеш |

## Кеш-стратегия

Кеш в `cache/`:
- `user_id.txt` — ID пользователя (permanent)
- `hosts.json` + `hosts.tsv` — список сайтов (permanent, инвалидируется при add/verify)
- `host_*/queries/*.tsv` — результаты запросов (session, hash-keyed)
- `host_*/indexing/*.tsv` — данные индексации (session)
- `host_*/insearch/*.tsv` — данные о поиске (session)
- `host_*/links/*.tsv` — данные о ссылках (session)
- `host_*/alice/init.json` — проверенный объект Alice, общий для всех действий `alice.sh`, срок кеша — 24 часа
- `host_*/alice/raw.html` — последний полностью полученный HTML для диагностики, без истории версий
- Диагностика, квоты, статусы переобхода — **не кешируются** (always live)

## Расширенные сценарии

- [Добавление и верификация сайтов](references/SITE_MANAGEMENT.md)
- [Поисковые запросы и аналитика](references/SEARCH_QUERIES.md)
- [Управление сайтмапами](references/SITEMAPS.md)
- [Статистика сайта и индексация](references/SITE_STATS.md)
- [Страницы в поиске](references/PAGES_IN_SEARCH.md)
- [Переобход страниц](references/RECRAWL.md)
- [Битые внутренние ссылки](references/INTERNAL_LINKS.md)
- [Внешние ссылки](references/EXTERNAL_LINKS.md)
- [YML-фиды](references/FEEDS.md)
- [PRO SERP экспорт](references/SEARCH_EXPORT.md)
- [Alice / Share of Voice (scraping)](references/ALICE_EFFICIENCY.md)
- [Расписание врачей (спецификация)](references/DOCTORS_SCHEDULE.md)

## Лимиты API

- **Query Analytics**: 10 000 запросов/час на домен
- Скрипты автоматически обрабатывают 429 (Retry-After ≤ 60s → retry, иначе fail)
- Лимит сайтов: 1703 на аккаунт
- Лимит фидов: 5000 на сайт, 50 в batch
- Лимит export: макс. 100 комбинаций дат+URL
