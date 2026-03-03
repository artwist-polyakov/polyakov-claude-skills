# Популярные поисковые запросы

Отчёт по поисковым запросам, с которыми пользователи приходят на сайт.

## Важно

Данные по поисковым запросам доступны **только из Яндекса**. Google не передаёт ключевые слова в Метрику.

## Dimension

```
ym:s:lastSignSearchPhrase
```

## Пример запроса

```bash
# Через API напрямую (curl)
curl -s -G "https://api-metrika.yandex.net/stat/v1/data.csv" \
  -H "Authorization: OAuth $YANDEX_METRIKA_TOKEN" \
  --data-urlencode "ids=COUNTER_ID" \
  --data-urlencode "date1=2025-01-01" \
  --data-urlencode "date2=2025-12-31" \
  --data-urlencode "metrics=ym:s:visits,ym:s:users,ym:s:bounceRate" \
  --data-urlencode "dimensions=ym:s:lastSignSearchPhrase" \
  --data-urlencode "filters=ym:s:isRobot=='No' AND ym:s:lastSignSearchPhrase!=''" \
  --data-urlencode "accuracy=1" \
  --data-urlencode "sort=-ym:s:visits" \
  --data-urlencode "limit=100" \
  -o search_queries.csv
```

## Использование с common.sh

```sh
. scripts/common.sh
load_config

metrika_get_csv "/stat/v1/data.csv" "search_queries.csv" \
  --data-urlencode "ids=$COUNTER" \
  --data-urlencode "date1=$DATE1" \
  --data-urlencode "date2=$DATE2" \
  --data-urlencode "metrics=ym:s:visits,ym:s:users,ym:s:bounceRate" \
  --data-urlencode "dimensions=ym:s:lastSignSearchPhrase" \
  --data-urlencode "filters=ym:s:isRobot=='No' AND ym:s:lastSignSearchPhrase!=''" \
  --data-urlencode "accuracy=1" \
  --data-urlencode "sort=-ym:s:visits" \
  --data-urlencode "limit=200"
```

## Фильтрация

Можно фильтровать по содержимому запроса:

```
filters=ym:s:lastSignSearchPhrase=@'купить' AND ym:s:isRobot=='No'
```

## Динамика по времени

Для отслеживания изменений популярности запросов используйте `/stat/v1/data/bytime.csv` с `group=month`.
