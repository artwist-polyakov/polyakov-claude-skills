---
name: telegram-channel-parser
description: |
  Парсинг публичных Telegram-каналов через веб-превью t.me/s/.
  Посты, метрики, аналитика, дайджесты, конкурентный анализ.
  Cache-first подход для гигиены контекстного окна.
  Triggers: telegram канал, telegram channel, парсинг телеграм,
  дайджест каналов, telegram digest, анализ канала, шер-парад,
  telegram analytics, мониторинг каналов.
---

# telegram-channel-parser

Парсинг публичных Telegram-каналов через веб-превью (t.me/s/). Без API-ключей, без MTProto, zero config.

## Перед запуском

Перед любым запуском скриптов создай рабочую копию скилла в `/home/claude/telegram-channel-parser` и работай уже из неё:

```bash
bash scripts/prepare_runtime.sh
cd /home/claude/telegram-channel-parser
```

`prepare_runtime.sh` копирует скилл и, если есть `config/.env`, нормализует его через `sed -i`: некавыченные значения с пробелами или кириллицей оборачиваются в кавычки в рабочей копии.

## Config

Никаких токенов не требуется. Для дайджестов — скопировать `.env.example`:
```bash
cp config/.env.example config/.env
```

**Без `.env`:** скилл работает, но каналы нужно передавать явно через `--channel` / `--channels`.

**С `.env`:** дайджест AI-каналов готов из коробки. Пользователь может добавить свои категории.

**Структура `.env` (категории дайджестов):**
```bash
TG_CATEGORIES=ai,crypto         # реестр доступных категорий
TG_DEFAULT_CATEGORY=ai           # дефолтная при запросе "дайджест"

TG_CHANNELS_AI_LABEL="AI и технологии"
TG_CHANNELS_AI=countwithsasha,evilfreelancer,...

TG_CHANNELS_CRYPTO_LABEL="Криптовалюты"
TG_CHANNELS_CRYPTO=channel1,channel2
```

**Алгоритм агента при запросе дайджеста:**
1. Прочитать `config/.env` (если есть)
2. Распарсить `TG_CATEGORIES` — получить список доступных категорий
3. Для каждой категории: `TG_CHANNELS_<ID>` = каналы, `TG_CHANNELS_<ID>_LABEL` = название
4. Определить нужную категорию:
   - Пользователь назвал тему → сопоставить с `_LABEL`
   - Не уточнил → использовать `TG_DEFAULT_CATEGORY`
   - Несколько категорий подходят → предложить выбор
5. Передать каналы нужной категории через `--channels`

**Если `.env` нет** → спросить какие каналы парсить, предложить `cp config/.env.example config/.env`.

**Приоритет:** `--channels` явно > категория из `.env` > агент спрашивает.

Подробности: [config/README.md](config/README.md).

## Philosophy

1. **Always fresh** — данные запрашиваются в реальном времени при каждом вызове. Никогда не пропустишь свежий пост.
2. **Context window hygiene** — stdout ограничен 30 строками. Полные данные в TSV/CSV. LLM работает с компактным форматом, а не с сырым HTML.
3. **Rate limit** — между запросами к t.me пауза 1.5с. Не жадничаем.
4. **Чистый POSIX sh** — никаких зависимостей кроме curl, sed, awk, grep.

## Workflow

### Парсинг одного канала

1. **Получи посты:**
   ```bash
   bash scripts/fetch_posts.sh --channel countwithsasha --limit 50
   ```
   Выведет последние 50 постов в TSV (id, date, views, reactions, fwd_from, fwd_link, text, media_url).

2. **Инфо о канале:**
   ```bash
   bash scripts/channel_info.sh --channel countwithsasha
   ```

3. **Поиск по постам:**
   ```bash
   bash scripts/search_posts.sh --channel countwithsasha --query "скилл"
   ```

4. **Топ постов (шер-парад):**
   ```bash
   bash scripts/top_posts.sh --channel countwithsasha --limit 50 --sort reactions
   ```

5. **Расписание публикаций:**
   ```bash
   bash scripts/posting_schedule.sh --channel countwithsasha --limit 100
   ```

6. **Экспорт:**
   ```bash
   bash scripts/export_csv.sh --channel countwithsasha --limit 100 --csv cache/export.csv
   ```

### Дайджест по нескольким каналам

```bash
# Явный список каналов
bash scripts/digest.sh --channels "countwithsasha,evilfreelancer,aostrikov_ai_agents" --period today

# Дефолтный набор (без --channels)
bash scripts/digest.sh --period today
```

Периоды: `today`, `yesterday`, `week`, `N` (последние N дней).

### Сравнение каналов

```bash
bash scripts/compare_channels.sh --channels "channel1,channel2,channel3" --limit 30
```

Таблица: подписчики, средние просмотры, частота публикаций, engagement.

## React-артифакт для дайджеста

При запросе дайджеста — **отображай результаты как React-артифакт** (лента карточек).

**Алгоритм (4 шага):**
1. Запусти `digest_json.sh` — он **пишет JSON в файл** (не stdout!) и возвращает путь:
   ```bash
   bash scripts/digest_json.sh --period today
   # → prints: cache/digest_today.json
   ```
2. Прочитай JSON-файл по выведенному пути
3. Прочитай шаблон `assets/digest-feed.tsx`
4. Замени `__DIGEST_DATA__` в шаблоне на содержимое JSON, отрендери как React-артифакт

**Важно:** скрипт пишет в файл, а не в stdout, чтобы обойти лимит буфера sandbox (~200KB). Для 30-дневного дайджеста 15 каналов JSON может быть 500KB+.

Посты автоматически сортируются по дате (новые сверху), перемешаны между каналами. Пользователь фильтрует по периоду и каналу через UI.

## Scripts

Общий паттерн вызова:
```bash
bash scripts/<script>.sh --channel <username> [--limit N] [--before <post_id>] [--csv path]
```

| Script | Description | Special params |
|--------|-------------|----------------|
| `fetch_posts.sh` | Посты канала → TSV | `--limit`, `--before`, `--after-date YYYY-MM-DD` |
| `channel_info.sh` | Название, описание, подписчики | — |
| `search_posts.sh` | Полнотекстовый поиск | `--query "text"` |
| `top_posts.sh` | Ранжирование постов | `--sort views\|reactions`, `--limit` |
| `posting_schedule.sh` | Анализ времени публикаций | `--limit` |
| `export_csv.sh` | Экспорт в CSV | `--csv path` |
| `digest.sh` | Дайджест нескольких каналов | `--channels "a,b,c"`, `--period today\|yesterday\|week\|N` |
| `digest_json.sh` | Дайджест → JSON файл (для React-артифакта) | `--channels "a,b,c"`, `--period today\|yesterday\|week\|N` |
| `compare_channels.sh` | Сравнительная таблица | `--channels "a,b,c"` |

## Общие параметры

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `--channel` | да* | — | Username канала (без @) |
| `--channels` | нет | из .env | Несколько каналов через запятую |
| `--limit` | нет | 20 | Сколько постов загрузить |
| `--before` | нет | — | ID поста для пагинации |
| `--after-date` | нет | — | Не загружать посты старше даты (YYYY-MM-DD) |
| `--csv` | нет | — | Путь для экспорта |

*`--channel` для одного канала, `--channels` для мультиканальных команд.

## Ввод канала

Скилл принимает канал в любом формате:
- `countwithsasha` — просто username
- `@countwithsasha` — с собакой
- `https://t.me/countwithsasha` — прямая ссылка
- `https://t.me/s/countwithsasha` — ссылка на веб-превью
- `t.me/countwithsasha?before=500` — с параметрами

Всё автоматически нормализуется до голого username.

## Ограничения

- Только **публичные** каналы (у которых есть t.me/s/ превью)
- **Счётчик пересылок (shares) недоступен** — t.me/s/ его не отдаёт, только MTProto API
- Зато парсится **откуда переслан пост** (fwd_from + ссылка на оригинал)
- Реакции парсятся суммарно (общее количество по всем эмодзи)
- Пагинация: ~20 постов на страницу, для 100 постов = 5 запросов
- Rate limit: 1.5с между запросами к t.me
