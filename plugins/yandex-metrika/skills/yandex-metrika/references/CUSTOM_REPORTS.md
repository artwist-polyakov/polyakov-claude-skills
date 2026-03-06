# Произвольные отчёты

Как построить произвольный отчёт с любыми dimensions и metrics.

## Принцип

Все скрипты используют `common.sh` для API-вызовов. Можно легко собрать запрос вручную:

```sh
. scripts/common.sh
load_config

metrika_get_csv "/stat/v1/data.csv" "output.csv" \
  --data-urlencode "ids=COUNTER_ID" \
  --data-urlencode "date1=2025-01-01" \
  --data-urlencode "date2=2025-12-31" \
  --data-urlencode "metrics=METRICS" \
  --data-urlencode "dimensions=DIMENSIONS" \
  --data-urlencode "filters=FILTERS" \
  --data-urlencode "accuracy=1" \
  --data-urlencode "sort=-SORT_FIELD" \
  --data-urlencode "limit=100"
```

## Примеры

### Страницы входа с метриками

```
dimensions=ym:s:startURL
metrics=ym:s:visits,ym:s:bounceRate,ym:s:avgVisitDurationSeconds
sort=-ym:s:visits
```

### География: города

```
dimensions=ym:s:regionCity
metrics=ym:s:visits,ym:s:users,ym:s:bounceRate
filters=ym:s:regionCountry=='Россия' AND ym:s:isRobot=='No'
```

### Устройства: ОС + браузер

```
dimensions=ym:s:operatingSystem,ym:s:browser
metrics=ym:s:visits,ym:s:users
```

### Реферальные источники

```
dimensions=ym:s:lastsignReferalSource
metrics=ym:s:visits,ym:s:users,ym:s:bounceRate
filters=ym:s:lastsignTrafficSource=='referral' AND ym:s:isRobot=='No'
```

### Рекламные системы

```
dimensions=ym:s:lastsignAdvEngine
metrics=ym:s:visits,ym:s:users,ym:s:goal<ID>conversionRate
filters=ym:s:lastsignTrafficSource=='ad' AND ym:s:isRobot=='No'
```

## JSON-запросы (drilldown и др.)

Для endpoints, не поддерживающих CSV (drilldown), используйте `metrika_get` напрямую:

```sh
. scripts/common.sh
load_config

RESULT=$(metrika_get "/stat/v1/data/drilldown" \
  --data-urlencode "ids=COUNTER_ID" \
  --data-urlencode "date1=2025-01-01" \
  --data-urlencode "date2=2025-12-31" \
  --data-urlencode "metrics=ym:s:visits" \
  --data-urlencode "dimensions=ym:s:startURLPathLevel1" \
  --data-urlencode "accuracy=1")

echo "$RESULT"
```

Результат — JSON. Парсите через grep/sed или сохраняйте в файл.

## Правила

- Нельзя смешивать visit (ym:s:) и pageview (ym:pv:) префиксы в одном запросе
- Максимум ~10 dimensions и ~20 metrics в одном запросе
- Для больших выгрузок используйте `limit` + `offset` для пагинации
- bytime возвращает максимум ~7 уникальных значений dimension в колонках — для полного анализа используйте `/stat/v1/data` с фильтрами по каждому периоду отдельно
- Полный справочник: [API_REFERENCE.md](API_REFERENCE.md)
