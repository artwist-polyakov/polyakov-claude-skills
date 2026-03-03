# Получение токена Yandex Metrika API

## Шаг 1: Зарегистрируйте приложение

1. Перейдите на https://oauth.yandex.ru/client/new
2. Укажите название приложения (например, "Claude Metrika")
3. В разделе "Платформы" выберите "Веб-сервисы"
4. В "Доступы" добавьте: `metrika:read` (Яндекс.Метрика — чтение)
5. Сохраните и запишите `client_id`

Подробнее: https://yandex.ru/dev/id/doc/ru/register-client

## Шаг 2: Получите OAuth токен

Откройте в браузере:

```
https://oauth.yandex.ru/authorize?response_type=token&client_id=ВАШ_CLIENT_ID
```

После авторизации токен будет в URL:
```
https://oauth.yandex.ru/#access_token=ВАШТОКЕН&token_type=bearer&expires_in=31536000
```

Скопируйте значение `access_token`.

## Шаг 3: Настройте токен

```bash
cp config/.env.example config/.env
```

Вставьте токен:
```
YANDEX_METRIKA_TOKEN=ваш_токен_здесь
```

## Проверка

```bash
bash scripts/counters.sh
```

Должен показать список ваших счётчиков.

## Лимиты API

- **Reporting API**: ~200 запросов / 5 минут
- **Management API**: мягкие лимиты

## Срок жизни токена

Токен действует **1 год**. После истечения получите новый по той же ссылке.

## Документация

- Metrika API: https://yandex.ru/dev/metrika/ru/
- Reporting API: https://yandex.ru/dev/metrika/ru/stat/
- OAuth: https://yandex.ru/dev/id/doc/ru/
