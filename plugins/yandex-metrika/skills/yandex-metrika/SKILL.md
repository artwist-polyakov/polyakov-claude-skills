---
name: yandex-metrika
description: |
  Аналитика Yandex Metrika: трафик, конверсии, UTM, поисковые системы.
  Cache-first подход для гигиены контекстного окна.
  Triggers: яндекс метрика, yandex metrika, metrika analytics,
  метрика трафик, метрика конверсии, метрика отчёт.
---

# yandex-metrika

Работа с Yandex Metrika Reporting API v1. Отчёты по трафику, конверсиям, UTM-меткам, поисковым системам.

## Config

Требуется `YANDEX_METRIKA_TOKEN` в `config/.env`.
Инструкция: `config/README.md`.

## Philosophy

1. **Cache-first** — конфигурационные данные (счётчики, цели, инфо) кешируются надолго. Отчёты кешируются по ключу counter+dates+params. Перед API-запросом всегда проверяем кеш.
2. **Context window hygiene** — stdout ограничен 30 строками. Полные данные в CSV/файл. Кеш доступен через grep/rg для поиска без загрузки в контекст.
3. **Точные данные** — accuracy=1 (без сэмплирования), фильтр isRobot по умолчанию.
4. **Атрибуция** — дефолт `lastsign` (последний значимый источник). Спрашиваем пользователя при первом запуске.

## Workflow

### STOP! Перед любым анализом:

1. **Получи список счётчиков:**
   ```bash
   bash scripts/counters.sh
   ```

2. **Спроси пользователя** (если счётчик не очевиден из контекста):
   ```
   "О каком счётчике/сайте идёт речь?
   Укажите ID, название или домен."
   ```
   Если пользователь назвал сайт/домен — ищи через `--search`:
   ```bash
   bash scripts/counters.sh --search "metallik"
   ```
   Это grep по TSV (id + name + site), поэтому находит и по домену.

3. **Получи инфо о счётчике и его цели:**
   ```bash
   bash scripts/counter_info.sh --counter <ID>
   bash scripts/goals.sh --counter <ID>
   ```

4. **Спроси про конверсионные цели:**
   ```
   "Какие из этих целей являются конверсионными для вашего бизнеса?
   [список целей из goals.sh]
   Сохраню выбранные для будущих отчётов."
   ```

5. **Сохрани конфигурацию** в `cache/counter_<id>/config.json`:
   ```json
   {
     "attribution": "lastsign",
     "conversion_goals": [
       {"id": 12345, "name": "Заказ оформлен"},
       {"id": 67890, "name": "Заявка отправлена"}
     ]
   }
   ```

6. **Запускай отчёты** по задаче пользователя.

## Scripts

Общий паттерн вызова:
```bash
bash scripts/<script>.sh --counter <ID> --date1 YYYY-MM-DD [--date2 ...] [--group month] [--csv path]
```

| Script | Description | Special params |
|--------|-------------|----------------|
| `counters.sh` | Список счётчиков | `--search "query"` |
| `goals.sh` | Цели счётчика | — |
| `counter_info.sh` | Метаданные счётчика | — |
| `traffic_summary.sh` | Трафик по источникам | — |
| `conversions.sh` | Достижение целей | `--goals "ID,ID"` / `--all-goals`; по умолчанию из `config.json` |
| `utm_report.sh` | UTM-разбивка | — |
| `search_engines.sh` | Поисковые системы (organic) | — |
| `ecommerce.sh` | Покупки, выручка, средний чек | `--currency RUB\|USD\|EUR`; авто из counter_info |
| `direct_clients.sh` | Логины Директа | — |
| `direct_costs.sh` | Расходы Директа (`ym:ad:*`) | `--direct-client-logins "login"`; нет `--group`/`--device`/`--source` |
| `comparison.sh` | Сравнение двух периодов | `--date1a/--date2a/--date1b/--date2b`; `--dimension`, `--metrics` |

Не все скрипты поддерживают все общие параметры — см. **Special params**.

## Общие параметры отчётных скриптов

| Param | Required | Default | Values |
|-------|----------|---------|--------|
| `--counter` | yes | - | ID счётчика |
| `--date1` | yes | - | YYYY-MM-DD |
| `--date2` | no | today | YYYY-MM-DD |
| `--group` | no | - | day, week, month |
| `--device` | no | all | desktop, mobile, tablet |
| `--source` | no | all | organic, ad, referral, direct, social |
| `--attribution` | no | lastsign | lastsign, last, first |
| `--limit` | no | API default | число строк |
| `--csv` | no | - | путь для экспорта |
| `--no-cache` | no | - | пропустить кеш |

## Кеш-стратегия

Кеш хранится в `cache/`:
- `counters.json` + `counters.tsv` — все счётчики
- `counter_<id>/info.json` — метаданные (permanent)
- `counter_<id>/goals.json` + `goals.tsv` — цели
- `counter_<id>/config.json` — атрибуция, конверсионные цели
- `counter_<id>/direct_clients.json` — логины Директа
- `counter_<id>/reports/*.csv` — результаты отчётов

Для поиска по кешу: `grep "text" cache/counters.tsv` или `rg "text" cache/`.

## Расширенные сценарии

- [Популярные поисковые запросы](references/SEARCH_QUERIES.md)
- [Произвольные отчёты и JSON-запросы](references/CUSTOM_REPORTS.md) (drilldown, metrika_get и др.)
- [Справочник dimensions/metrics](references/API_REFERENCE.md)
- [Сравнение периодов год-к-году](references/PERIOD_COMPARISON.md)
- [Расходы Директа и PnL](references/DIRECT_COSTS.md)
- [Ограничения API](references/API_REFERENCE.md#known-api-limitations) (bytime, scope mixing, drilldown CSV)

## Лимиты API

- **Reporting API**: ~200 запросов / 5 минут (при превышении — ждите ~5 минут)
- Скрипты автоматически обрабатывают 429 (Retry-After ≤ 60s → retry, иначе fail с сообщением)
