# Настройка Yandex Search API

Для работы нужны каталог Yandex Cloud, роль Search API и один из способов
авторизации:

- API-ключ Yandex AI Studio — рекомендуемый вариант;
- IAM через ключ сервисного аккаунта — совместимый fallback.

## Вариант A: API-ключ Yandex AI Studio

### Шаг 1: подготовьте каталог

1. Откройте https://console.yandex.cloud/ и выберите каталог.
2. Скопируйте его ID. Он выглядит как `b1gabcdef12345678900`.
3. Убедитесь, что в каталоге активирован Search API.
4. Назначьте субъекту ключа роль `search-api.webSearch.user`.

API-ключ должен иметь scope `yc.search-api.execute`. Создать и настроить ключ
можно в Yandex AI Studio: https://aistudio.yandex.ru/

### Шаг 2: создайте config.json

```bash
cp config/config.example.json config/config.json
```

Замените placeholder на ID каталога:

```json
{
  "yandex_cloud_folder_id": "b1gabcdef12345678900",
  "auth": {
    "mode": "api_key"
  }
}
```

Остальные секции из примера оставьте без изменений.

### Шаг 3: сохраните ключ локально

Создайте `config/.env`:

```bash
YANDEX_AI_API_KEY=AQVN_your_api_key
```

```bash
chmod 600 config/.env
```

`config/.env` и `config/config.json` исключены из git. Никогда не печатайте
ключ в логах, отчётах или сообщениях.

## Вариант B: IAM через сервисный аккаунт

1. Создайте сервисный аккаунт в каталоге Yandex Cloud.
2. Назначьте ему роль `search-api.webSearch.user`.
3. Создайте авторизованный ключ и сохраните JSON как
   `config/service_account_key.json`.
4. Укажите IAM-режим:

```json
{
  "yandex_cloud_folder_id": "b1gabcdef12345678900",
  "auth": {
    "mode": "iam",
    "service_account_key_file": "config/service_account_key.json",
    "openssl_bin": "openssl"
  }
}
```

5. Проверьте генерацию токена:

```bash
bash scripts/iam_token_get.sh
```

На macOS может понадобиться `brew install openssl` и путь
`/opt/homebrew/bin/openssl` (Apple Silicon) либо
`/usr/local/opt/openssl/bin/openssl` (Intel).

## Частые проблемы

### `auth.mode=api_key requires YANDEX_AI_API_KEY`

Нет `config/.env`, переменная названа неверно или файл недоступен.

### `403 Forbidden`

Проверьте роль `search-api.webSearch.user`, scope ключа
`yc.search-api.execute`, ID каталога и принадлежность ключа каталогу.

### `config.json not found`

Скопируйте `config/config.example.json` в `config/config.json`.

### Ошибка LibreSSL или OpenSSL

Она относится только к IAM-режиму. Установите OpenSSL и укажите
`auth.openssl_bin` в `config.json`.

## Лимиты и цены

Запросы Search API могут тарифицироваться. Перед массовым запуском проверьте
актуальные условия: https://yandex.cloud/ru/docs/search-api/pricing

## Настройки по умолчанию

| Настройка | Значение | Что это |
|-----------|----------|---------|
| Регион | Россия (225) | Откуда «смотрим» поиск |
| Тип поиска | Русскоязычный | Поиск по рунету |
| Фильтр контента | Умеренный | Фильтрует откровенный контент |
| Исправление опечаток | Включено | Яндекс исправляет опечатки |
| Результатов на странице | 10 | Сколько ссылок в ответе |
