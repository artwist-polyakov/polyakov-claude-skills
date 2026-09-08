# Сервисы API Директа

Для ежедневной работы используйте готовые команды из [SKILL.md](../SKILL.md). Эта карта показывает возможности официального API, в том числе операции, для которых в скилле нет готовой команды. Полная карта методов, адресов и стоимости вызовов: [api_map.json](api_map.json). Карта доступности действий: [coverage.json](coverage.json).

## Адреса и версии

Основной JSON API: `https://api.direct.yandex.com/json/v501/{service}`. Reports: `https://api.direct.yandex.com/json/v5/reports`. Используйте документированный адрес нужного сервиса. Существование другого отвечающего адреса не гарантирует одинакового поведения.

Обычный запрос содержит `method` и `params`; успешные данные — `result`, общая ошибка — `error`. У пакетного изменения также проверяются `Errors` и `Warnings` каждого объекта. Заголовки для выбора кабинета описаны в [CLIENT_LOGIN.md](CLIENT_LOGIN.md), баллы и повторы — в [ERRORS_AND_LIMITS.md](ERRORS_AND_LIMITS.md).

## Сервисы v501

| Сервис | Назначение | Методы |
|---|---|---|
| [AdExtensions](https://yandex.ru/dev/direct/doc/ru/adextensions/adextensions) | Работа с расширениями к объявлениям. В настоящее время доступен один тип расширения — уточнение. | `add`, `delete`, `get` |
| [AdGroups](https://yandex.ru/dev/direct/doc/ru/adgroups/adgroups) | Работа с группами объявлений. | `add`, `delete`, `get`, `update` |
| [AdImages](https://yandex.ru/dev/direct/doc/ru/adimages/adimages) | Работа с изображениями. | `add`, `delete`, `get` |
| [Ads](https://yandex.ru/dev/direct/doc/ru/ads/ads) | Работа с объявлениями. | `add`, `archive`, `delete`, `get`, `moderate`, `resume`, `suspend`, `unarchive`, `update` |
| [AdVideos](https://yandex.ru/dev/direct/doc/ru/advideos/advideos) | Работа с видео. | `add`, `get` |
| [AgencyClients](https://yandex.ru/dev/direct/doc/ru/agencyclients/agencyclients) | Сервис предназначен для агентств и позволяет управлять клиентами агентства. | `add` (снят), `addPassportOrganization`, `addPassportOrganizationMember`, `get`, `update` |
| [AudienceTargets](https://yandex.ru/dev/direct/doc/ru/audiencetargets/audiencetargets) | Работа с условиями нацеливания на аудиторию. | `add`, `delete`, `get`, `resume`, `setBids`, `suspend` |
| [Bids](https://yandex.ru/dev/direct/doc/ru/bids/bids) | Сервис предназначен для назначения ставок ключевым фразам и автотаргетингам. | `get`, `set`, `setAuto` |
| [Businesses](https://yandex.ru/dev/direct/doc/ru/businesses/businesses) | Сервис позволяет получить профили организаций бизнеса. | `get` |
| [BidModifiers](https://yandex.ru/dev/direct/doc/ru/bidmodifiers/bidmodifiers) | Сервис предназначен для управления корректировками ставок. | `add`, `delete`, `get`, `set` |
| [Campaigns](https://yandex.ru/dev/direct/doc/ru/campaigns/campaigns) | Работа с кампаниями. | `add`, `archive`, `delete`, `get`, `resume`, `suspend`, `unarchive`, `update` |
| [Changes](https://yandex.ru/dev/direct/doc/ru/changes/changes) | Сервис предназначен для проверки наличия изменений. | `checkDictionaries`, `checkCampaigns`, `check` |
| [Clients](https://yandex.ru/dev/direct/doc/ru/clients/clients) | Сервис предназначен для управления параметрами рекламодателя и настройками пользователя — представителя рекламодателя, а также для получения параметров агентства и настроек пользователя — представителя агентства. | `get`, `update` |
| [Creatives](https://yandex.ru/dev/direct/doc/ru/creatives/creatives) | Работа с креативами. | `add`, `get` |
| [Dictionaries](https://yandex.ru/dev/direct/doc/ru/dictionaries/dictionaries) | Сервис предназначен для получения справочных данных: регионов, часовых поясов, курсов валют, станций метрополитена, ограничений на значения параметров и др. | `get`, `getGeoRegions` |
| [Feeds](https://yandex.ru/dev/direct/doc/ru/feeds/feeds) | Работа с фидами. | `add`, `delete`, `get`, `update` |
| [KeywordBids](https://yandex.ru/dev/direct/doc/ru/keywordbids/keywordbids) | Сервис предназначен для назначения ставок ключевым фразам и автотаргетингам и для получения данных, полезных при назначении ставок. | `get`, `set`, `setAuto` |
| [Keywords](https://yandex.ru/dev/direct/doc/ru/keywords/keywords) | Работа с ключевыми фразами и автотаргетингами. | `add`, `delete`, `get`, `resume`, `suspend`, `update` |
| [KeywordsResearch](https://yandex.ru/dev/direct/doc/ru/keywordsresearch/keywordsresearch) | Сервис предназначен для предварительной обработки и исследования ключевых фраз. | `deduplicate`, `hasSearchVolume` |
| [Leads](https://yandex.ru/dev/direct/doc/ru/leads/leads) | Сервис позволяет получить данные, введенные пользователями в формы на Турбо-страницах. | `get` |
| [NegativeKeywordSharedSets](https://yandex.ru/dev/direct/doc/ru/negativekeywordsharedsets/negativekeywordsharedsets) | Работа с наборами минус-фраз. | `add`, `delete`, `get`, `update` |
| [RetargetingLists](https://yandex.ru/dev/direct/doc/ru/retargetinglists/retargetinglists) | Работа с условиями ретаргетинга и подбора аудитории. | `add`, `delete`, `get`, `update` |
| [Sitelinks](https://yandex.ru/dev/direct/doc/ru/sitelinks/sitelinks) | Работа с наборами быстрых ссылок. | `add`, `delete`, `get` |
| [Strategies](https://yandex.ru/dev/direct/doc/ru/strategies/strategies) | Работа с пакетными стратегиями. | `add`, `archive`, `get`, `unarchive`, `update` |
| [TurboPages](https://yandex.ru/dev/direct/doc/ru/turbopages/turbopages) | Сервис позволяет получить параметры Турбо-страниц. | `get` |

## Объявления старых типов

У `DynamicTextAdTargets`, `SmartAdTargets` и `VCards` сохранились отдельные упоминания и схемы, но полноценные страницы справочника доступны не для всех операций. По одному наличию WSDL нельзя установить смысл полей и ограничения. Для изменения используйте только поддержанную команду с проверяемым результатом; иначе покажите путь в интерфейсе.

`ResponsiveAd` — основной формат записи объявлений. Текстово-графические объявления могут автоматически переводиться в него; обновление через старую структуру `TextAd` рискует сократить набор заголовков, текстов и материалов.

## Reports

Reports — отдельный сервис, а не JSON-метод `get`. Он возвращает TSV и может готовить отчёт в очереди. Цели, модели атрибуции, поля, периоды и ожидание описаны в [REPORTS.md](REPORTS.md). Стоимость в баллах нельзя приравнивать к нулю по отсутствию строки в таблице стоимости; соблюдайте ограничения частоты самого Reports.

## Оставшиеся методы версии 4

Вызовы Live v4 идут на `https://api.direct.yandex.com/live/v4/json/`; некоторые методы используют `/v4/json/`. Устаревшее управление кампаниями, объявлениями и ставками не заменяет v501.

Версия 4 отличается формой: `token`, `method`, `param`, `locale` передаются в теле, успешные данные — в `data`, ошибки — в корневых `error_code`, `error_str`, `error_detail`. Штатный клиент скрывает токен; не собирайте такой запрос вручную в выводимой команде.

| Область | Методы справочника |
|---|---|
| Общий счёт | `EnableSharedAccount`, `AccountManagement` |
| Финансовые операции | `CreateInvoice`, `PayCampaigns`, `TransferMoney`, `GetCreditLimits` |
| Прогноз бюджета | `CreateNewForecast`, `GetForecast`, `GetForecastList`, `DeleteForecastReport` |
| Подбор фраз и статистика запросов | `GetKeywordsSuggestion`, `CreateNewWordstatReport`, `GetWordstatReport`, `GetWordstatReportList`, `DeleteWordstatReport` |
| Метки объявлений и кампаний | `GetBannersTags`, `UpdateBannersTags`, `GetCampaignsTags`, `UpdateCampaignsTags` |
| Ретаргетинг | `GetRetargetingGoals` |
| Баллы | `GetClientsUnits` |
| Справочники | `GetRegions`, `GetStatGoals`, `GetRubrics`, `GetTimeZones` |
| Информация об API | `GetAvailableVersions`, `GetVersion`, `PingAPI`, `GetEventsLog` |

`GetRetargetingGoals` используется для доступных целей, когда нужен этот источник. Баллы v4 образуют отдельный пул: `GetClientsUnits` не показывает остаток v5. Остаток v5 читается из заголовка `Units`, суточное ограничение — из `Clients.get → Restrictions → API_POINTS`.

Для изображений используйте `AdImages` и `Ads` v501. Старый `AdImage` версии 4 не считается рабочей заменой. Регионы и часовые пояса берите через `Dictionaries` v501.

Финансовые методы перечислены для понимания границ API; наличие их в справочнике не является разрешением оплачивать или переводить средства. Такие действия не выполняются этим скиллом.

Источники: [справочник v5](https://yandex.ru/dev/direct/doc/ru/), [методы v4](https://yandex.ru/dev/direct/doc/dg-v4/ru/reference/_AllMethods).
