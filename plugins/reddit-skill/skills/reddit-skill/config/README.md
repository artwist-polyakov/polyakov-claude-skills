# Настройка скилла reddit-skill

Скилл общается с Reddit API напрямую через OAuth2 — никакого PRAW. Поддерживает два режима, скилл выбирает их автоматически:

| Режим      | Grant                | Что нужно                                      | Что доступно           |
|------------|----------------------|------------------------------------------------|------------------------|
| app-only   | `client_credentials` | `CLIENT_ID` + `CLIENT_SECRET`                  | Все read-операции      |
| user       | `password`           | + `REDDIT_USERNAME` + `REDDIT_PASSWORD`        | Read + write + `me`    |

Для write-команд — двойной предохранитель: `REDDIT_ENABLE_WRITE=1` в `.env` и явный `--confirm` в командной строке.

---

## Шаг 1: Создайте Reddit-приложение

1. Откройте https://www.reddit.com/prefs/apps (или https://old.reddit.com/prefs/apps — иногда работает стабильнее)
2. Внизу нажмите **«create another app...»** (или create app)
3. Заполните:
   - **Name**: `reddit-skill` (или любое)
   - **Type**: см. таблицу ниже
   - **Description**: пусто или короткое описание
   - **About URL**: можно пусто
   - **Redirect URI**: `http://localhost:8080` (формально требуется; для read-only не используется)
4. Пройдите капчу и **сразу же** жмите **«create app»** (токен капчи живёт ~2 минуты)
5. Запомните:
   - **`client_id`** — короткая строка под названием приложения (под надписью типа «personal use script» или «web app»)
   - **`secret`** — поле «secret»

### Какой тип выбрать?

| Тип            | Что выдаёт         | client_credentials (read) | password grant (write) | Итого функций скилла |
|----------------|--------------------|---------------------------|------------------------|----------------------|
| **`script`**   | client_id + secret | ✅                        | ✅                     | **все 14**           |
| **`web app`**  | client_id + secret | ✅                        | ❌                     | 10 read-операций     |
| `installed app`| только client_id   | ❌ (нет secret)           | ❌                     | не подходит          |

**Рекомендация:** ставьте `script`. Если форма не сабмитится с `script` (Reddit anti-fraud иногда так реагирует на новые/малоактивные аккаунты) — попробуйте `web app`. Скилл автоматически работает в read-only режиме без `REDDIT_USERNAME`/`REDDIT_PASSWORD`.

### Если форма не сабмитится

Симптом: жмёшь `create app` — ничего не происходит, в URL ничего не меняется, страница не редиректит.

Чек-лист:
1. **Hard reload** (`⌘+Shift+R` / `Ctrl+Shift+R`) — заполни заново и пройди капчу ровно перед кликом
2. **Инкогнито-режим** — расширения вроде uBlock Origin, Privacy Badger, AdGuard ломают Reddit anti-bot. В инкогнито они выключены.
3. **Старый Reddit** явно — `https://old.reddit.com/prefs/apps`
4. **Без VPN** — Reddit банит часть VPN-IP с определённых регионов
5. **Проверь, что приложение случайно не создалось** — иди в `/prefs/apps` без `/create`, посмотри список. Бывает, что один из «молчаливых» сабмитов сработал
6. **DevTools → Network → XHR** — нажми create, посмотри, был ли запрос. 4xx-ответ покажет реальную причину

---

## Шаг 2: Заполните `.env`

```bash
cp config/.env.example config/.env
```

Откройте `config/.env` и подставьте значения:

```ini
REDDIT_CLIENT_ID=abcDEF123_xyz
REDDIT_CLIENT_SECRET=ваш_secret
REDDIT_USER_AGENT="claude-skill:reddit-skill:1.0.0 (by /u/yourname)"
```

`REDDIT_USER_AGENT` обязателен по [правилам Reddit API](https://github.com/reddit-archive/reddit/wiki/API). Шаблон: `<platform>:<app-id>:<version> (by /u/<username>)`. Не используйте дефолтные строки браузеров — Reddit может банить.

### Опционально: write/me

Чтобы получить доступ к `me.sh`, `post_create.sh`, `comment_reply.sh`, `subreddit_subscribe.sh`, добавьте:

```ini
REDDIT_USERNAME=yourname
REDDIT_PASSWORD=your_password
REDDIT_ENABLE_WRITE=1
```

Аккаунт должен быть **владельцем** script-app (того, чьи `client_id`/`secret` указаны выше). Если у аккаунта включена 2FA, password grant не сработает — отключите 2FA либо используйте read-only режим.

> Поведение write-скриптов:
> - Без `REDDIT_ENABLE_WRITE=1` — **сразу падают с ошибкой** (envelope-уровень, защита от случайной активации write).
> - С `REDDIT_ENABLE_WRITE=1`, но без `--confirm` — **dry-run**: печатают что бы отправили, но не идут в сеть.
> - Только с обоими — реальный POST.

---

## Шаг 3: Проверка

App-only режим:
```bash
sh scripts/auth_check.sh
```
Должен вывести `OK: app-only token works`.

User режим:
```bash
sh scripts/me.sh
```
Должен вывести имя пользователя, карму и т. п.

---

## Лимиты

Reddit OAuth API:
- Базовый: ~60 запросов/минуту
- При наличии user-токена: до ~600/10мин (зависит от истории аккаунта)

Скилл читает `x-ratelimit-remaining`, `x-ratelimit-reset`, `Retry-After` из заголовков и при `429` ждёт `Retry-After` (≤ 60s) и делает один retry.

Подробнее: https://www.reddit.com/wiki/api

---

## Срок жизни токена

Access token живёт ~3600 секунд. Скрипты автоматически рефрешат его через тот же grant, как только до истечения остаётся <300 секунд. Кеш — `cache/token.json`.

---

## Troubleshooting

### `401 Unauthorized` от auth_check.sh
- Неверные `CLIENT_ID`/`CLIENT_SECRET` (проверьте, что `client_id` — это короткая строка под названием приложения, а не имя приложения)
- Тип приложения — не `script`. У `web app` и `installed app` другой OAuth flow

### `401 invalid_grant` при password grant
- Неправильные `REDDIT_USERNAME`/`REDDIT_PASSWORD`
- На аккаунте включена 2FA — отключите либо используйте app-only режим
- Аккаунт не является владельцем приложения

### `429 Too Many Requests`
- Превышен лимит. Скилл делает один retry, если `Retry-After ≤ 60s`. При больших значениях — fail с текстом из заголовка. Подождите указанное время.

### `403 Forbidden` от write-команд
- Сабреддит закрыт/требует флера/не пускает аккаунт-новичок
- На сабреддите бан/мут

### Write-команды ничего не делают / падают
- **`Write is disabled` ошибка** — не задан `REDDIT_ENABLE_WRITE=1` в `.env`. Это намеренная защита: чтобы вообще включить write-режим, нужно явно поставить переменную.
- **Dry-run без сетевого запроса** — `REDDIT_ENABLE_WRITE=1` стоит, но не передан `--confirm`. Это ожидаемое поведение: команда печатает что бы отправила и завершается.
- Чтобы реально отправить запрос — нужны оба условия одновременно.

---

## Дополнительно

- Reddit API docs: https://www.reddit.com/dev/api/
- OAuth2 wiki: https://github.com/reddit-archive/reddit/wiki/OAuth2
- Rules: https://github.com/reddit-archive/reddit/wiki/API
