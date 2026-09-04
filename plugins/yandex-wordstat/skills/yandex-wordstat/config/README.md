# Настройка скилла Yandex Wordstat

Скилл поддерживает только Wordstat API в Yandex Cloud Search API v2: `https://searchapi.api.cloud.yandex.net/v2/wordstat`.

Для авторизации используется ключ сервисного аккаунта. Скрипты сами получают IAM-токен, сохраняют его в локальный кеш и обновляют по мере необходимости. Получать и вставлять токен вручную не нужно.

Старый API `api.wordstat.yandex.net/v1` не работает. Его OAuth-токен не подходит для Yandex Cloud Search API; инструкции получения и обновления старого токена больше не поддерживаются.

## Переход со старого API

Удалите `YANDEX_WORDSTAT_TOKEN` и `YANDEX_WORDSTAT_BACKEND` из `config/.env` и настроек окружения. Для текущей оболочки выполните:

```bash
unset YANDEX_WORDSTAT_TOKEN YANDEX_WORDSTAT_BACKEND
```

Затем настройте доступ по инструкции ниже. Команды и аргументы скриптов анализа не меняются.

## Настройка доступа

Все команды ниже выполняются из каталога скилла `plugins/yandex-wordstat/skills/yandex-wordstat`.

### Шаг 1: Создайте каталог в Яндекс.Облаке

1. Откройте [консоль Yandex Cloud](https://console.yandex.cloud/)
2. Зарегистрируйтесь (нужен Яндекс ID), если ещё нет аккаунта
3. Создайте **каталог** или используйте существующий
4. Скопируйте **ID каталога** (`b1g...`) — он понадобится дальше

### Шаг 2: Создайте сервисный аккаунт

1. В консоли откройте ваш каталог
2. Слева выберите **Сервисные аккаунты** (раздел IAM)
3. Нажмите **Создать сервисный аккаунт**
4. Имя: `wordstat-sa` (или любое)
5. Нажмите **Создать**

### Шаг 3: Назначьте роль

1. Откройте созданный сервисный аккаунт
2. Назначьте роль `search-api.webSearch.user` на созданный каталог

### Шаг 4: Создайте ключ авторизации

1. В сервисном аккаунте → **Авторизованные ключи** → **Создать**
2. Скачайте JSON-файл
3. Переименуйте в `service_account_key.json`
4. Положите в `config/` (рядом с этим README)

> Файл секретный — он уже в `.gitignore`.

### Шаг 5: Создайте config.json

```bash
cp config/config.example.json config/config.json
```

Откройте `config.json` и подставьте ваш `yandex_cloud_folder_id`:

```json
{
  "yandex_cloud_folder_id": "b1g_ваш_id_каталога",
  "auth": {
    "service_account_key_file": "config/service_account_key.json",
    "openssl_bin": "openssl"
  }
}
```

`auth.service_account_key_file` — путь к ключу. Относительные пути отсчитываются от корня скилла, не от `config/`. Можно указать абсолютный путь.

Если доступ уже настроен для скилла `yandex-search-api`, можно использовать тот же ключ сервисного аккаунта и каталог: схема `config.json` одинакова. Скопируйте файлы в `config/` этого скилла или укажите абсолютный путь к существующему ключу.

### Шаг 6: Проверьте

```bash
sh scripts/quota.sh
```

При успешном запросе скрипт выведет `Wordstat API: OK`. Проверка обращается к API.

## Dynamics: ограничения операторов

Метод `dynamics` (`scripts/dynamics.sh`) поддерживает все [операторы поиска Wordstat](https://yandex.ru/support/direct/keywords/symbols-and-operators.html) **только при детализации `daily`**. При `weekly` и `monthly` доступен **только оператор `+`**.

Это ограничение на стороне Yandex Cloud Search API — задокументировано в [официальной документации](https://aistudio.yandex.ru/docs/ru/search-api/operations/wordstat-getdynamics.html).

Скилл проверяет фразу перед отправкой: если вы запустите `dynamics.sh --period weekly --phrase "юрист -бесплатно"`, скрипт завершится с понятной ошибкой до запроса.

| Фраза                  | period=daily | period=weekly/monthly |
|------------------------|--------------|-----------------------|
| `юрист дтп`            | ✓            | ✓                     |
| `юрист +по дтп`        | ✓            | ✓ (`+` разрешён)      |
| `юрист -бесплатно`     | ✓            | ✗ (минус-слово)       |
| `"юрист дтп"`          | ✓            | ✗ (кавычки)           |
| `(юрист\|адвокат) дтп` | ✓            | ✗ (группировка)       |
| `!юрист`               | ✓            | ✗ (точная форма)      |
| `санкт-петербург`      | ✓            | ✓ (внутрисловный дефис) |
| `б/у дымоход`          | ✓            | ✓ (слэш)              |

## Устранение ошибок

### `Wordstat API: Error` / `wordstat 401`

- Проверьте, что файл ключа содержит действующий авторизованный ключ сервисного аккаунта. Если ключ отозван или повреждён, создайте новый по основной инструкции.
- IAM-токен обновляется автоматически. Вручную получать OAuth-токен не нужно.

### `Wordstat 403 Forbidden`

- Роль `search-api.webSearch.user` не назначена сервисному аккаунту, либо вы пытаетесь обратиться не к тому каталогу.
- Проверьте `yandex_cloud_folder_id` в `config.json`.

### `LibreSSL detected` (macOS)

macOS по умолчанию использует LibreSSL, который не поддерживает PS256 для подписи JWT.

```bash
brew install openssl@3
```

И в `config.json`:

```json
{
  "auth": {
    "openssl_bin": "/opt/homebrew/bin/openssl"
  }
}
```

Узнать точный путь: `brew --prefix openssl`.

### Неполная или неверная настройка `config/config.json`

- `yandex_cloud_folder_id` пустой → заполните
- Файл ключа не найден по пути из `auth.service_account_key_file` → проверьте, что файл есть и читается. Помните: относительные пути отсчитываются от корня скилла, а не от `config/`.

## Дополнительно

- [Документация Wordstat API](https://aistudio.yandex.ru/docs/ru/search-api/concepts/wordstat.html)
- [Актуальные квоты и лимиты Wordstat](https://aistudio.yandex.ru/ru/docs/search-api/concepts/limits)
- [Тарификация](https://yandex.cloud/ru/docs/search-api/pricing)
- [Операторы поиска](https://yandex.ru/support/direct/keywords/symbols-and-operators.html)
