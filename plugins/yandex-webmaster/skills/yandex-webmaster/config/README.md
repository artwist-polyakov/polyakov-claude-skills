# Получение токена Yandex Webmaster API

## Шаг 1: Зарегистрируйте приложение

1. Перейдите на https://oauth.yandex.ru/client/new
2. Укажите название приложения (например, "Claude Webmaster")
3. В разделе "Платформы" выберите "Веб-сервисы"
4. В "Доступы" добавьте ОБА scope:
   - `webmaster:hostinfo` — чтение данных о сайтах
   - `webmaster:verify` — управление сайтами (добавление, верификация, переобход)
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
YANDEX_WEBMASTER_TOKEN=ваш_токен_здесь
```

### Доступ через прокси

Для запросов к API скрипты запускают `curl -q`, поэтому настройки из `~/.curlrc` пропускаются. Если нужен прокси, задайте его адрес в окружении перед запуском скрипта.

```sh
export https_proxy="http://proxy.example.org:3128"
```

Если прокси подписывает сертификаты сайтов корпоративным сертификатом, укажите в `CURL_CA_BUNDLE` путь к файлу PEM с доверенным сертификатом этого центра.

## Проверка

```bash
bash scripts/hosts.sh
```

Должен показать список ваших сайтов в Вебмастере.

## Срок жизни токена

Токен действует **1 год**. После истечения получите новый по той же ссылке.

## SESSION_ID — для Alice (Share of Voice)

Данные раздела «Эффективность → Алиса» **не доступны через Webmaster API v4** —
они отдаются только при SSR-рендере страницы. Чтобы их парсить (`scripts/alice.sh`),
нужна cookie `Session_id` авторизованного пользователя.

Где взять:
1. Откройте https://webmaster.yandex.ru в обычном залогиненном Chrome.
2. DevTools (F12) → **Application** → **Cookies** → `https://yandex.ru`
3. Найдите строку `Session_id` (httpOnly) и скопируйте Value целиком.
4. Вставьте в `config/.env` **в кавычках** (значение содержит `|`):
   ```
   SESSION_ID="3:1775...|142834...|3:11824..."
   ```

Cookie живёт долго (несколько месяцев), но при логауте/смене пароля
протухает — тогда нужно достать заново.

## Документация

- Webmaster API: https://yandex.ru/dev/webmaster/doc/ru/
- OAuth: https://yandex.ru/dev/id/doc/ru/
