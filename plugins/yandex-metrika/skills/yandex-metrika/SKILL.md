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

2. **Спроси пользователя:**
   ```
   "О каком счётчике идёт речь?
   Вот ваши счётчики: [top-5 из кеша]
   Укажите ID или название."
   ```

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

### counters.sh
Список всех счётчиков с кешем.
```bash
bash scripts/counters.sh
bash scripts/counters.sh --search "mysite"
bash scripts/counters.sh --no-cache
```

### goals.sh
Цели счётчика с кешем.
```bash
bash scripts/goals.sh --counter 12345
```

### counter_info.sh
Метаданные счётчика (дата создания, сайт, статус кода).
```bash
bash scripts/counter_info.sh --counter 12345
```

### traffic_summary.sh
Распределение трафика по источникам.
```bash
bash scripts/traffic_summary.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --date2 2025-12-31 \
  --group month
```

### conversions.sh
Достижение целей. По умолчанию — только конверсионные цели из конфига.
```bash
# Конверсионные цели из конфига
bash scripts/conversions.sh \
  --counter 12345 \
  --date1 2025-01-01

# Все цели
bash scripts/conversions.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --all-goals

# Конкретные цели
bash scripts/conversions.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --goals "111,222"
```

### utm_report.sh
Разбивка по UTM-меткам (source + medium + campaign).
```bash
bash scripts/utm_report.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --group month
```

### search_engines.sh
Трафик из поисковых систем (только organic).
```bash
bash scripts/search_engines.sh \
  --counter 12345 \
  --date1 2025-01-01
```

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
- `counter_<id>/reports/*.csv` — результаты отчётов

Для поиска по кешу: `grep "text" cache/counters.tsv` или `rg "text" cache/`.

## Расширенные сценарии

- [Популярные поисковые запросы](references/SEARCH_QUERIES.md)
- [Произвольные отчёты](references/CUSTOM_REPORTS.md)
- [Справочник dimensions/metrics](references/API_REFERENCE.md)
- [Сравнение периодов год-к-году](references/PERIOD_COMPARISON.md)

## Лимиты API

- **Reporting API**: ~200 запросов / 5 минут (при превышении — ждите ~5 минут)
- Скрипты автоматически обрабатывают 429 (Retry-After ≤ 60s → retry, иначе fail с сообщением)
