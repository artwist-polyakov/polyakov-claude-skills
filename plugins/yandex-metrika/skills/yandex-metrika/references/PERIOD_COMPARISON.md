# Сравнение периодов год-к-году

Как анализировать долгосрочную динамику и сравнивать периоды.

## Данные доступны с 2009 года

Metrika API хранит данные с 2009 года (ym:s:visits). Можно запрашивать за любой период.

## Подход 1: bytime с group=month

Для длинной динамики (год+) — запрос по месяцам через `/stat/v1/data/bytime.csv`:

```bash
bash scripts/traffic_summary.sh \
  --counter 12345 \
  --date1 2023-01-01 \
  --date2 2025-12-31 \
  --group month
```

Результат: CSV с колонками по месяцам, удобно для графиков и сравнений.

## Подход 2: два отдельных запроса

Для прямого сравнения "этот год vs прошлый":

```bash
# Этот год
bash scripts/traffic_summary.sh \
  --counter 12345 \
  --date1 2025-01-01 \
  --date2 2025-12-31 \
  --csv traffic_2025.csv

# Прошлый год
bash scripts/traffic_summary.sh \
  --counter 12345 \
  --date1 2024-01-01 \
  --date2 2024-12-31 \
  --csv traffic_2024.csv
```

Далее агент может проанализировать оба CSV и вычислить delta.

## Подход 3: Comparison API

Metrika имеет эндпойнт `/stat/v1/data/comparison.csv` для автоматического сравнения двух сегментов.

Пример использования через common.sh:

```sh
. scripts/common.sh
load_config

metrika_get_csv "/stat/v1/data/comparison.csv" "comparison.csv" \
  --data-urlencode "ids=12345" \
  --data-urlencode "date1_a=2025-01-01" \
  --data-urlencode "date2_a=2025-06-30" \
  --data-urlencode "date1_b=2024-01-01" \
  --data-urlencode "date2_b=2024-06-30" \
  --data-urlencode "metrics=ym:s:visits,ym:s:users,ym:s:bounceRate" \
  --data-urlencode "dimensions=ym:s:lastSignTrafficSource" \
  --data-urlencode "accuracy=1" \
  --data-urlencode "filters=ym:s:isRobot=='No'"
```

## Рекомендации

- **group=month** — оптимален для 1-3 лет, компактный CSV
- **group=week** — для анализа сезонности в рамках года
- **group=day** — для детального анализа коротких периодов (до 3 месяцев)
- При больших периодах CSV может быть объёмным — используйте `--csv` для экспорта в файл
