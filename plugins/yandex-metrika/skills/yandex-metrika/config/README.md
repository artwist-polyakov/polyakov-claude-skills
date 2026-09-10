# Получение токена Yandex Metrika API

## Шаг 1: Зарегистрируйте приложение

1. Перейдите на https://oauth.yandex.ru/client/new
2. Укажите название приложения (например, "Claude Metrika")
3. В разделе "Платформы" выберите "Веб-сервисы"
4. В «Доступы» добавьте:
   - `metrika:read` — отчёты, счётчики, сегменты и разрешения на чтение;
   - `metrika:write` — создание и удаление сегментов, выдача и изменение доступов.
5. Если токен выпускается от имени логина паспортной организации, добавьте
   `passport:business`.
6. Сохраните и запишите `client_id`.

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

Если вы добавили новые доступы уже после выпуска токена, выпустите новый токен:
старый не получает расширенные права автоматически.

## Шаг 3: Настройте токен

```bash
umask 077
cp config/.env.example config/.env
chmod 600 config/.env
```

Вставьте токен:
```
YANDEX_METRIKA_TOKEN=ваш_токен_здесь
```

Сценарии откажутся читать `config/.env`, если файл доступен группе или другим
пользователям. Исправьте права командой `chmod 600 config/.env`.

## Проверка

```bash
bash scripts/counters.sh
bash scripts/segments.sh --counter <ID> --action list
bash scripts/grants.sh --counter <ID> --action list
```

Команды должны показать счётчики, API-сегменты и прямые разрешения. Просмотр
разрешений доступен владельцу счётчика или пользователю с ролью `edit`.

## Лимиты API

- **Reporting API**: ~200 запросов / 5 минут
- **Management API**: мягкие лимиты

## Срок жизни токена

Токен действует **1 год**. После истечения получите новый по той же ссылке.

## Документация

- Metrika API: https://yandex.ru/dev/metrika/ru/
- Reporting API: https://yandex.ru/dev/metrika/ru/stat/
- OAuth: https://yandex.ru/dev/id/doc/ru/
