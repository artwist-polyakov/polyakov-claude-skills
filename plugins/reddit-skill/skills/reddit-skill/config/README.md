# Настройка скилла reddit-skill

Скилл общается с Reddit API напрямую через OAuth2 — никакого PRAW. Поддерживает два режима, скилл выбирает их автоматически:

| Режим      | Grant                | Что нужно                                      | Что доступно           |
|------------|----------------------|------------------------------------------------|------------------------|
| app-only   | `client_credentials` | `CLIENT_ID` + `CLIENT_SECRET`                  | Все read-операции      |
| user       | `password`           | + `REDDIT_USERNAME` + `REDDIT_PASSWORD`        | Read + write + `me`    |

Для write-команд — двойной предохранитель: `REDDIT_ENABLE_WRITE=1` в `.env` и явный `--confirm` в командной строке.

---

## Шаг 1: Получите Reddit OAuth-приложение

С 2024 Reddit изменил порядок выдачи API-доступа. Старый «зашёл в `/prefs/apps` → нажал create app → получил creds» работает не у всех — для новых аккаунтов часто требуется ручное одобрение через форму Reddit Help. Поэтому сначала **проверь, не подойдёт ли что-то из уже существующего**, и только потом подавай заявку.

### Алгоритм (по приоритету)

#### Вариант A — у тебя уже есть Reddit-приложение

**Используй его.** Скиллу нужны только `client_id` + `client_secret` от любого `script` или `web app` приложения — название и исходное назначение неважны.

1. Открой https://old.reddit.com/prefs/apps
2. Найди существующее приложение в списке
3. Под названием будет тип в скобках (`personal use script` / `web app`) и серая короткая строка ~14 символов — это **`client_id`**
4. Чуть ниже поле **`secret`** — это **`client_secret`** (если скрыто, нажми «edit»)
5. Переходи к Шагу 2

> **Не удаляй старые приложения**, даже если они выглядят неактуальными. По свежим жалобам в r/redditdev после удаления старого app многие не могут получить новый — попадают в новый approval-flow.
>
> `installed app` не подходит — у него нет `client_secret`. В этом случае создавай новое (B) или подавай заявку (C).

#### Вариант B — попробовать создать новое через `/prefs/apps`

Иногда форма всё ещё работает — особенно если аккаунт не свежий и не помечен анти-фродом.

1. Открой https://old.reddit.com/prefs/apps (старый интерфейс стабильнее нового)
2. Жми **«create another app...»** внизу
3. Заполни:
   - **Name**: `reddit-skill` (или любое)
   - **Type**: см. таблицу ниже
   - **Description**: пусто или короткое нейтральное описание (см. подсказки ниже про формулировки)
   - **About URL**: можно пусто
   - **Redirect URI**: `http://localhost:8080` (формально требуется; для read-only не используется)
4. Пройди капчу и **сразу же** жми **`create app`** (токен капчи живёт ~2 минуты)

Если форма принимает запрос — Reddit редиректит на список приложений и показывает новую карточку. Забирай `client_id` + `secret` и переходи к Шагу 2.

Если форма **молчит / редиректит на Responsible Builder Policy / показывает «You cannot create any more applications»** — это новый approval-flow. Переходи к варианту C.

#### Вариант C — заявка через Request API Access

Reddit официально пишет: для нового Data API доступа нужно подать заявку через форму Reddit Help, и только после одобрения возвращаешься в `/prefs/apps` создавать приложение.

1. Открой форму **Request API Access**: https://support.reddithelp.com/hc/en-us/requests/new
2. В поле «What do you need assistance with?» выбери **«Request API Access»**
3. Заполни описание use case аккуратно — формулировка решает (см. шаблоны ниже)
4. Жди одобрения (обычно несколько дней — недели)
5. После одобрения возвращайся к Варианту B и создавай приложение

### Какой тип приложения выбрать?

| Тип             | Что выдаёт            | `client_credentials` (read) | `password` grant (write) | Функций скилла |
|-----------------|------------------------|------------------------------|---------------------------|----------------|
| **`script`**    | client_id + secret    | ✅                           | ✅                        | **все 14**     |
| **`web app`**   | client_id + secret    | ✅                           | ❌                        | 10 read-операций |
| `installed app` | только client_id      | ❌ (нет secret)              | ❌                        | не подходит    |

**Рекомендация:** ставь `script`. Если форма не пускает или одобрение получено только под non-personal use — `web app` тоже подойдёт для read-режима (это 90% полезности скилла).

### Формулировка use case (важно)

Reddit разделяет non-commercial и commercial use, и определение коммерческого довольно широкое: использование «by a business» / «on behalf of a business» / «as part of a monetized product or service» уже считается commercial. AI/ML training вообще запрещено без отдельного контракта.

Этот скилл — **read-only research helper**: тянет публичные посты/комментарии, кеширует кратковременно, ничего не публикует, не голосует, не пишет в DM, не используется для тренировки моделей и не перепродаёт данные. Это укладывается в non-commercial профиль, который Reddit одобряет охотнее.

**Что писать в Description / в заявке Request API Access:**

```
A private, read-only research assistant for monitoring a small set of public
subreddits and producing internal summaries of public discussions.

The app does NOT:
  - post, comment, vote, or send messages
  - automate any user-facing interaction
  - scrape outside of the official Data API
  - train or fine-tune AI/ML models on Reddit content
  - redistribute or resell Reddit data

The app uses a unique User-Agent, OAuth credentials, short-lived response
caching for operational use only, and removes cached content during routine
cleanup to comply with Reddit's data retention/deletion requirements.
```

Если у тебя уже есть существующий Reddit app под другой проект — добавь это в заявку:

```
I already have an existing Reddit API app and would like to register a
separate new read-only app for an isolated internal research workflow.
```

**Чего НЕ писать:**

- «AI agent для генерации идей для канала» / «для блога» — звучит как контент-фарминг
- «Generates content for…» — Reddit чувствителен к ML/training-кейсам
- Любые упоминания монетизации, B2B-продукта, SaaS, подписок, перепродажи
- «Trains a model on…» — это явный трип-вайр, прямо запрещено

**Если use case действительно коммерческий** (канал/консалтинг монетизируется) — Reddit ожидает, что ты заявишь это явно через отдельный канал: https://support.reddithelp.com/hc/en-us/requests/new (выбрать commercial-вариант). Для personal exploration / private research / non-commercial — обычная Request API Access форма.

### Если форма `/prefs/apps` не сабмитится

Симптомы: жмёшь `create app` — ничего не происходит, URL не меняется, или редиректит на Responsible Builder Policy.

Прежде чем подавать заявку через Вариант C — проверь техническое:

1. **Hard reload** (`⌘+Shift+R` / `Ctrl+Shift+R`) — заполни заново и пройди капчу ровно перед кликом
2. **Инкогнито-режим** — расширения (uBlock Origin, Privacy Badger, AdGuard) ломают Reddit anti-bot. В инкогнито их нет.
3. **Старый Reddit** явно — `https://old.reddit.com/prefs/apps`
4. **Без VPN** — Reddit банит часть VPN-IP с определённых регионов
5. **Приложение случайно не создалось** — открой `/prefs/apps` без `/create` и посмотри список. Один из «молчаливых» сабмитов мог сработать
6. **DevTools → Network → XHR** — нажми create, посмотри запрос. `4xx`-ответ покажет реальную причину

Если после всего этого форма всё равно показывает policy-страницу или ругается «cannot create any more applications» — это approval-flow, технически не обходится. Подавай заявку через Вариант C.

### Чего точно не делать

- ❌ Не использовать reverse-engineered токены официальных мобильных приложений Reddit (в r/redditdev иногда советуют — это нарушение ToS, бан прилетает быстро)
- ❌ Не шарить чужие creds
- ❌ Не пытаться обходить approval-flow техническими средствами
- ❌ Не использовать дефолтный User-Agent из браузера (Reddit банит за это)

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

### `/prefs/apps` редиректит на Responsible Builder Policy
- Это новый approval-flow, а не баг формы. Технически не обходится.
- Подавай заявку через https://support.reddithelp.com/hc/en-us/requests/new → «Request API Access». Шаблон описания — в Шаге 1 → Вариант C.
- Если у тебя уже есть рабочий старый app — используй его creds, заявка не нужна.

### `You cannot create any more applications`
- Reddit стал жёстче к новым/малоактивным аккаунтам и в принципе к новым OAuth-приложениям. Иногда показывает эту ошибку, даже если у аккаунта 0 приложений.
- Не удаляй имеющиеся приложения — после удаления получить новое сложнее.
- Решение — заявка через Reddit Help (Вариант C в Шаге 1).

---

## Дополнительно

- Reddit API docs: https://www.reddit.com/dev/api/
- OAuth2 wiki: https://github.com/reddit-archive/reddit/wiki/OAuth2
- API rules / User-Agent: https://github.com/reddit-archive/reddit/wiki/API
- Reddit Data API Terms: https://www.redditinc.com/policies/data-api-terms
- Developer Terms: https://www.redditinc.com/policies/developer-terms
- Responsible Builder Policy (что Reddit хочет видеть в use case): https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy
- Запрос API Access (non-commercial): https://support.reddithelp.com/hc/en-us/requests/new — выбрать «Request API Access»
- Запрос commercial Data API: https://support.reddithelp.com/hc/en-us/requests/new — отдельный commercial-вариант
