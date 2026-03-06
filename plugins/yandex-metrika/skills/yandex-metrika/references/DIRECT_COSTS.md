# Расходы Яндекс Директа и PnL-анализ

## Принцип работы

Метрики расходов (`ym:ad:*`) требуют параметр `direct_client_logins` — логин(ы) Директа, привязанные к счётчику. Без этого параметра API возвращает 403.

### Как получить логины

Management API endpoint:
```
GET /management/v1/clients?counters=<counterId>
```

Ответ содержит:
- `chief_login` — логин клиента Директа
- `all_clients_accessible_to_user` — `true`/`false`, доступны ли все клиенты

Скрипт `direct_clients.sh` автоматизирует этот вызов и кеширует результат.

```bash
bash scripts/direct_clients.sh --counter 12345
```

**Важно**: endpoint `/management/v1/clients` помечен как deprecated в OpenAPI-спецификации. Если он перестанет работать или вернёт пустой ответ, укажите логины вручную через `--direct-client-logins`:
```bash
bash scripts/direct_costs.sh --counter 12345 --date1 2025-01-01 \
  --direct-client-logins "my-direct-login"
```
Логин Директа можно найти в интерфейсе Яндекс Директа (Настройки → Логин) или спросить у владельца рекламного кабинета.

## Быстрый старт

```bash
# 1. Получить логины Директа (кешируется)
bash scripts/direct_clients.sh --counter 12345

# 2. Отчёт по расходам
bash scripts/direct_costs.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --date2 2025-12-31

# Экспорт в CSV для агрегации по неделям/месяцам
bash scripts/direct_costs.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --csv /tmp/direct_costs.csv
```

`direct_costs.sh` автоматически берёт логины из кеша. Если кеша нет — вызывает `direct_clients.sh`.
Данные всегда по дням (`ym:ad:date`); агрегация по неделям/месяцам — в CSV/Excel.

## Доступные ym:ad:* метрики

| Metric | Description |
|--------|-------------|
| `ym:ad:visits` | Визиты с рекламы Директа |
| `ym:ad:clicks` | Клики в Директе |
| `ym:ad:RUBConvertedAdCost` | Расход в рублях |
| `ym:ad:USDConvertedAdCost` | Расход в долларах |
| `ym:ad:EURConvertedAdCost` | Расход в евро |

## Доступные ym:ad:* dimensions

| Dimension | Description |
|-----------|-------------|
| `ym:ad:date` | Дата |
| `ym:ad:directOrder` | Рекламная кампания Директа |
| `ym:ad:directBanner` | Объявление |
| `ym:ad:directBannerGroup` | Группа объявлений |
| `ym:ad:directPhraseOrCond` | Ключевая фраза / условие |
| `ym:ad:directPlatformType` | Тип площадки (поиск / сети) |
| `ym:ad:directPlatform` | Конкретная площадка |

## PnL-анализ: расходы vs выручка

Для полного PnL нужны два отдельных отчёта (нельзя смешивать `ym:ad:*` и `ym:s:*` в одном запросе):

### 1. Расходы Директа

```bash
bash scripts/direct_costs.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --date2 2025-01-31 \
  --csv /tmp/costs.csv
```

### 2. Выручка e-commerce (трафик из ad)

```bash
bash scripts/ecommerce.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --date2 2025-01-31 \
  --source ad \
  --csv /tmp/revenue.csv
```

### 3. Сопоставление

Джойн по дате или вручную: сравните `RUBConvertedAdCost` с `ecommerceRUBConvertedRevenue` за тот же период.

Важно: клики Директа (`ym:ad:clicks`) и визиты Метрики (`ym:ad:visits`) — разные числа. Один клик может не привести к визиту (bounce до загрузки счётчика), а один визит может быть результатом нескольких кликов.

## Нюансы

### Несколько логинов Директа

Один счётчик может быть привязан к нескольким аккаунтам Директа. `direct_clients.sh` получает все логины, `direct_costs.sh` передаёт их через запятую в `direct_client_logins`.

### Неполный доступ

Если `all_clients_accessible_to_user: false`, токен не имеет доступа ко всем привязанным клиентам. Данные по расходам будут неполными. Скрипты выводят предупреждение в этом случае.

### Scope ym:ad:*

Метрики `ym:ad:*` — отдельный scope. Нельзя смешивать с `ym:s:*` (visit) или `ym:pv:*` (pageview) в одном запросе.

### Фильтры

Для `ym:ad:*` запросов не применяется стандартный фильтр `ym:s:isRobot=='No'` — это другой scope. Фильтры по устройству/источнику тоже не работают в ad scope.
