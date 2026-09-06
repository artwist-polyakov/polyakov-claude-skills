# Справочник ZoomKit API

- Официальная спецификация: https://zoomkit.ru/docs/api-v1.yaml
- Версия при подготовке навыка: `1.8.0`
- Основной адрес: `https://zoomkit.ru/api/v1`

Каждый сетевой запрос должен содержать:

```text
Accept: application/json
Authorization: Bearer <ключ>
```

Без `Accept: application/json` сервер может перенаправить ошибку авторизации на страницу входа вместо ответа `401`.

## Пути

| Команда сценария | Метод и путь | Успех |
|---|---|---|
| `token` | `GET /token` | `200` |
| `balance` | `GET /billing/balance` | `200` |
| `invoices` | `GET /billing/invoices` | `200` |
| `clients` | `GET /stats/clients` | `200` |
| `reports` | `GET /stats/reports` | `200` |
| `report-create` | `POST /stats/reports` | `202` |
| `report` | `GET /stats/reports/{id}` | `200` |
| `report-delete` | `DELETE /stats/reports/{id}` | `200` |
| `client-update` | `POST /yandex/direct/clients/{client}/update` | `202` |
| `bidrules` | `GET /yandex/direct/campaigns/{campaign}/bidrules` | `200` |
| `autotargeting` | `POST /yandex/direct/tokens/{token}/autotargeting` | `200` |
| `bidrule-common-update` | `PUT /yandex/direct/campaigns/{campaign}/bidrules/common` | `200` |
| `bidrule-keywords-create` | `POST /yandex/direct/campaigns/{campaign}/bidrules/keywords` | `201` |
| `bidrule-keywords-update` | `PUT /yandex/direct/campaigns/{campaign}/bidrules/keywords/{bidrule}` | `200` |
| `bidrule-keywords-delete` | `DELETE /yandex/direct/campaigns/{campaign}/bidrules/keywords/{bidrule}` | `200` |
| `url-check-settings` | `GET /yandex/direct/campaigns/{campaign}/url-check-settings` | `200` |
| `url-check-settings-update` | `PUT /yandex/direct/campaigns/{campaign}/url-check-settings` | `200` |
| `url-check-tasks` | `GET /yandex/direct/campaigns/{campaign}/url-check-tasks` | `200` |
| `url-check-task` | `GET /yandex/direct/campaigns/{campaign}/url-check-tasks/{task}` | `200` |

В спецификации нет `operationId`; ориентируйся на метод и путь.

`report-wait` — локальная оболочка над повторными `GET /stats/reports/{id}`, а не отдельный метод API.

## Границы публичного API

В публичном API 1.8.0 нет:

- отдельного метода списка кампаний Яндекс.Директа;
- пакетного чтения правил ставок или настроек проверки ссылок;
- признака тарификации отдельного кабинета и метода отключения от списаний;
- документированного признака, отличающего правило автотаргетинга в ответе `bidrules`;
- изменения текстовых правил проверки страниц;
- запуска проверки ссылок вручную и получения публичной ссылки на её отчёт.

Не подменяй эти возможности похожими методами. В частности, `client-update` лишь обновляет данные кабинета, а `autotargeting` сразу изменяет подходящие кампании.

Сам список кампаний в ZoomKit есть: он доступен в интерфейсе настройки ставок `https://zoomkit.ru/yandex/direct/campaigns`, раздел «Все кампании». Ограничение выше относится только к публичному API. Метод `/stats/clients` официально возвращает «Список проектов/аккаунтов» и должен использоваться для запроса пользователя о подключённых проектах.

## Общие ошибки

- `400` — неверное поле или тело запроса;
- `401` — ключ отсутствует или недействителен;
- `403` — нет доступа, оплаченного кабинета или активной подписки;
- `404` — объект не найден либо принадлежит другому владельцу;
- `409` — конфликт с состоянием объекта или уже выполняющейся задачей;
- `415` — для JSON не передан правильный `Content-Type`;
- `429` — превышен предел запросов;
- `5xx` — ошибка сервиса.

Общий предел — 60 запросов в минуту с блокировкой на минуту. Смотри заголовки `X-RateLimit-Limit`, `X-RateLimit-Remaining` и `X-RateLimit-Reset`. Создание отчёта ограничено 10 запросами в минуту, обновление кабинета — 5, массовое включение автотаргетинга — 2.

Сценарий не повторяет изменяющие запросы автоматически: повтор может создать дубликат или повторно изменить состояние.
