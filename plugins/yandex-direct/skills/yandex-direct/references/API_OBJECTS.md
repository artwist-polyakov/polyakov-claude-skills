# Объекты и поля API Директа

Этот справочник нужен, когда требуется уточнить поле, совместимость типов или форму запроса. Наличие метода в API ещё не означает готовую команду скилла: доступные команды перечислены в [SKILL.md](../SKILL.md), ограничения по операциям — в [coverage.json](coverage.json).

Основной формат записи объявлений — `ResponsiveAd` через v501. Другие форматы читаются для анализа; запись структуры старого `TextAd` может сократить комбинаторный набор до одного элемента.

## Перед изменением

У `ResponsiveAd.update` обязательны **все Titles и Texts**. Чтобы заменить месяц или цену в одном элементе, сначала прочитайте свежее объявление, измените нужные строки в полном наборе и сохраните остальные элементы и дополнения. Порядок показа сравнения и применения описан в [CHANGES.md](CHANGES.md).

Для `AdImageHashes` и `VideoExtensionIds` форма зависит от метода: `add` принимает обычный массив, `update` — объект `{"Items": [...]}`. Значение `null` отвязывает элементы, а пропуск необязательного поля оставляет его без изменений. `CalloutSetting` поддерживает `ADD`, `REMOVE` и `SET`; `SET` нельзя смешивать с двумя другими операциями.

При чтении разбирайте все реально пришедшие типовые структуры: их может быть несколько даже у одного объявления. Отсутствующее поле не заменяется нулём или пустым массивом без понимания его смысла.

`State` показывает состояние показов, `Status` — модерацию. После правки может продолжать показываться прежняя версия, пока новая проверяется или отклонена. Если автоматически допущенную новую версию затем отклонили, прежняя версия не обязательно возобновится. Итог оценивается по обоим полям и `StatusClarification`.

У пакетной стратегии часть целей, счётчиков, бюджета и атрибуции принадлежит самой стратегии. Не заменяйте отсутствующие на кампании поля выдуманными значениями. Кампании Мастера кампаний через `Campaigns.get` полноценно не читаются, но статистика по ним может быть доступна через Reports.

Ниже сохранены таблицы полей и совместимости. Числа для локальной проверки хранятся в [limits.json](limits.json); актуальные ограничения кабинета и валюты читаются через `Clients.get` и `Dictionaries.get`.

## 2. Кампания (Campaign)

### 2.1. Типы кампаний

| Код типа | Что это | Структура в `add` / `update` | `FieldNames` в `get` | Запись |
|---|---|---|---|---|
| `UNIFIED_CAMPAIGN` | единая перфоманс-кампания | `UnifiedCampaign` | `UnifiedCampaignFieldNames` | да, **v501** |
| `TEXT_CAMPAIGN` | текстово-графические объявления | `TextCampaign` | `TextCampaignFieldNames` | да |
| `MOBILE_APP_CAMPAIGN` | реклама мобильных приложений | `MobileAppCampaign` | `MobileAppCampaignFieldNames` | да |
| `CPM_BANNER_CAMPAIGN` | медийная кампания | `CpmBannerCampaign` | `CpmBannerCampaignFieldNames` | да |
| `SMART_CAMPAIGN` | смарт-баннеры | `SmartCampaign` | `SmartCampaignFieldNames` | да, отдельной страницы документации нет |
| `DYNAMIC_TEXT_CAMPAIGN` | динамические объявления | `DynamicTextCampaign` | `DynamicTextCampaignFieldNames` | да, отдельной страницы документации нет |

### 2.2. Общие параметры

| Параметр | Тип | `add` | `update` | `get` | Что означает |
|---|---|---|---|---|---|
| `Id` | long | — | Да | есть | Идентификатор. В `add` приходит в `AddResults[].Id` |
| `Name` | string | Да | Нет | есть | Название, до 255 символов |
| `ClientInfo` | string | Нет | Нет | есть | Название клиента, до 255 символов. **Нельзя задать для `UnifiedCampaign`** |
| `StartDate` | string | Да | Нет | есть | `YYYY-MM-DD`, не раньше текущей даты. Показы стартуют в 00:00 по Москве независимо от `TimeZone` |
| `EndDate` | string, nillable | Нет | Нет | есть | `YYYY-MM-DD`, показы прекращаются в 24:00 по Москве |
| `CreateTime` | string | — | — | есть | Дата и время создания кампании в формате `YYYY-MM-DDThh:mm:ssZ`. **Только в живом API**: ни документация, ни схема сервиса этого имени не называют. Имя не только принимается — отдельный запрос за данными возвращает значение ([2.6](#26-перечисления-шире-таблиц--и-у-кампаний-тоже)) |
| `TimeZone` | string | Нет | Нет | есть | По умолчанию `Europe/Moscow`, справочник — `Dictionaries.get` |
| `TimeTargeting` | `TimeTargetingAdd` в `add`, `TimeTargeting` дальше | Нет | Нет | есть | Временной таргетинг и почасовые коэффициенты |
| `DailyBudget` | `DailyBudget`, nillable | Нет | Нет | есть | Дневной бюджет для ручных ставок; `null` в `update` сбрасывает. **Снимается с поддержки**, см. ниже |
| `NegativeKeywords` | `ArrayOfString`, nillable | Нет | Нет | есть | Минус-фразы кампании. **Не поддерживается для медийных кампаний** |
| `BlockedIps` | `ArrayOfString`, nillable | Нет | Нет | есть | До 25 IP-адресов |
| `ExcludedSites` | `ArrayOfString`, nillable | Нет | Нет | есть | До 1000 площадок: домены, bundle ID и package name приложений, наименования внешних сетей |
| `Notification` | `Notification` | Нет | Нет | есть, nillable | SMS- и email-уведомления. Состав: `SmsSettings` — события (`MONITORING`, `MODERATION`, `MONEY_IN`, `MONEY_OUT`, `FINISHED`), `TimeFrom`, `TimeTo`; `EmailSettings` — `Email`, `CheckPositionInterval`, `WarningBalance`, `SendAccountNews`, `SendWarnings`. Номера телефона среди них нет |
| `TextCampaign` … `UnifiedCampaign` | типовые структуры | одна из них | Нет | есть | Параметры, зависящие от типа |

### 2.3. UnifiedCampaign — основной тип записи

| Параметр | Тип | `add` | `update` | Что означает |
|---|---|---|---|---|
| `BiddingStrategy` | `UnifiedCampaignStrategy` | Да, если нет `PackageBiddingStrategy` | Нет | Стратегия показа: `Search` и `Network` |
| `PackageBiddingStrategy` | `UnifiedCampaignPackageBiddingStrategy` | Да, если нет `BiddingStrategy` | Нет, nillable | Привязка к пакетной стратегии |
| `Settings` | `array of UnifiedCampaignSetting` | Нет | Нет | Настройки со значениями `YES` / `NO` |
| `CounterIds` | `ArrayOfInteger` | Нет | Нет, nillable | Счётчики Яндекс Метрики |
| `PriorityGoals` | `PriorityGoalsArray` | При `AverageCpaMultipleGoals`, `PayForConversionMultipleGoals` или `MaxProfit` | там же, nillable | Ключевые цели и ценности конверсий, до 30 |
| `TrackingParams` | string | Нет | Нет | Параметры URL для шаблонов |
| `AttributionModel` | `AttributionModelEnum` | Нет | Нет | `FCCD`, `LC`, `LSCCD`, `AUTO`; по умолчанию `AUTO` |
| `NegativeKeywordSharedSetIds` | `ArrayOfLong`, nillable | Нет | Нет | До 3 наборов минус-фраз |

| Имя | Что приходит | Непусто |
|---|---|---|
| `AdvertisedItem` | объект с полем `Href` — **ссылка на продвигаемую страницу кампании** | 1647 из 2452 |
| `DefaultBusinessId` | long — идентификатор организации Яндекс Бизнеса, той же природы, что `BusinessId` объявления | 442 из 2452 |
| `DefaultPhoneId` | long — идентификатор подменного номера, той же природы, что `TrackingPhoneId` объявления | 106 из 2452 |

### 2.4. Состояние, статус, оплата

| Значение | Что означает |
|---|---|
| `ON` | кампания активна, объявления могут показываться |
| `OFF` | черновик, ждёт модерации, отклонена, нет средств или нет активных объявлений |
| `SUSPENDED` | остановлена владельцем |
| `ENDED` | прошла дата окончания |
| `ARCHIVED` | в архиве — вручную, методом `archive` или автоматически при отсутствии средств и показов более 30 дней |
| `CONVERTED` | велась в у. е., перемещена в специальный архив, доступна только на чтение |
| `UNKNOWN` | состояние, не поддерживаемое этой версией API |

### 2.5. Параметры по типам кампаний

| Параметр | `TEXT` | `SMART` | `MOBILE_APP` | `DYNAMIC_TEXT` | `CPM_BANNER` |
|---|---|---|---|---|---|
| `AttributionModel` | + | + | – | + | – |
| `BiddingStrategy` | + | + | + | + | + |
| `CounterId` | – | + | – | – | – |
| `CounterIds` | + | – | – | + | + |
| `DailyBudget` | + | – | + | + | + |
| `FrequencyCap` | – | – | – | – | + |
| `NegativeKeywords` | + | + | + | + | – |
| `PlacementTypes` | – | – | – | + | – |
| `PriorityGoals` | + | + | – | + | – |
| `RelevantKeywords` | + | – | – | – | – |
| `VideoTarget` | – | – | – | – | + |

| Имя | Матрица документации | Схема сервиса | Живой API |
|---|---|---|---|
| `TrackingParams` | строки нет вовсе | есть у `TEXT`, `DYNAMIC_TEXT`, `SMART`, `UNIFIED` | принимается там же |
| `PriorityGoals` | `–` у `CPM_BANNER` | нет в `CpmBannerCampaignFieldEnum` | `CpmBannerCampaignFieldNames` принимает **и отдаёт наполненным** |

#### Настройки `Settings` по типам кампаний

| Настройка | `ЕПК` | `TEXT` | `MOBILE_APP` | `DYNAMIC_TEXT` | `CPM_BANNER` | `SMART` |
|---|---|---|---|---|---|---|
| `ADD_METRICA_TAG` | + | + | – | + | + | – |
| `ADD_OPENSTAT_TAG` | – | + | – | + | + | – |
| `ADD_TO_FAVORITES` | + | + | + | + | + | + |
| `ALTERNATIVE_TEXTS_ENABLED` | + | + | – | – | – | – |
| `CAMPAIGN_EXACT_PHRASE_MATCHING_ENABLED` | + | + | + | + | – | – |
| `DAILY_BUDGET_ALLOWED` | – | чт | чт | чт | чт | чт |
| `ENABLE_AREA_OF_INTEREST_TARGETING` | + | + | + | + | + | + |
| `ENABLE_AUTOFOCUS` | – | + | + | – | – | – |
| `ENABLE_BEHAVIORAL_TARGETING` | – | + | + | + | – | – |
| `ENABLE_COMPANY_INFO` | + | + | – | + | – | – |
| `ENABLE_CURRENT_AREA_TARGETING` | – | + | + | + | + | + |
| `ENABLE_EXTENDED_AD_TITLE` | – | + | – | + | – | – |
| `ENABLE_REGULAR_AREA_TARGETING` | – | + | + | + | + | + |
| `ENABLE_RELATED_KEYWORDS` | – | + | – | – | – | – |
| `ENABLE_SITE_MONITORING` | + | + | – | + | + | – |
| `EXCLUDE_PAUSED_COMPETING_ADS` | – | + | – | – | – | – |
| `MAINTAIN_NETWORK_CPC` | – | + | + | – | – | – |
| `REQUIRE_SERVICING` | + | + | + | + | + | + |
| `SHARED_ACCOUNT_ENABLED` | чт | чт | чт | чт | чт | чт |

#### Какой настройке отвечает какой переключатель кабинета

| Настройка | Переключатель кабинета |
|---|---|
| `ADD_METRICA_TAG` | экрана нет |
| `ADD_OPENSTAT_TAG` | экрана нет |
| `ADD_TO_FAVORITES` | Кампании → Действия → **Добавить в самые важные** |
| `ALTERNATIVE_TEXTS_ENABLED` | Дополнительные настройки → **Персонализация**; массово — Кампании → Действия → **Персонализация** |
| `CAMPAIGN_EXACT_PHRASE_MATCHING_ENABLED` | Дополнительные настройки → **Приоритизация объявлений** → «По фразе, наиболее близкой к запросу» |
| `DAILY_BUDGET_ALLOWED` | экрана нет |
| `ENABLE_AREA_OF_INTEREST_TARGETING` | блока больше нет |
| `ENABLE_AUTOFOCUS` | экрана нет |
| `ENABLE_BEHAVIORAL_TARGETING` | экрана нет |
| `ENABLE_COMPANY_INFO` | экрана нет |
| `ENABLE_CURRENT_AREA_TARGETING` | экрана нет |
| `ENABLE_EXTENDED_AD_TITLE` | экрана нет |
| `ENABLE_REGULAR_AREA_TARGETING` | экрана нет |
| `ENABLE_RELATED_KEYWORDS` | экрана нет |
| `ENABLE_SITE_MONITORING` | Дополнительные настройки → **Мониторинг сайта** |
| `EXCLUDE_PAUSED_COMPETING_ADS` | экрана нет |
| `MAINTAIN_NETWORK_CPC` | экрана нет |
| `REQUIRE_SERVICING` | экрана нет |
| `SHARED_ACCOUNT_ENABLED` | экрана нет по устройству |

### 2.6. Перечисления шире таблиц — и у кампаний тоже

#### Особенности возвращаемых полей

| Имя | Набор | Что вернул `get` |
|---|---|---|
| `CreateTime` | `FieldNames` | значение: `2020-09-01T15:58:59Z` |
| `AdvertisedItem` | `UnifiedCampaignFieldNames` | ключ есть; непусто у 1647 кампаний из 2452 |
| `DefaultBusinessId` | `UnifiedCampaignFieldNames` | ключ есть; непусто у 442 |
| `DefaultPhoneId` | `UnifiedCampaignFieldNames` | ключ есть; непусто у 106 |
| `PriorityGoals` | `CpmBannerCampaignFieldNames` | значение: `Items` с целями и ценностями |
| `ExcludedSitesForVideoAds` | `CpmBannerCampaignFieldNames` | ключ есть, значение `null` |

## 3. Группа объявлений (AdGroup)

### 3.1. Типы групп

| Тип группы | Тип кампании | Какие объявления допустимы |
|---|---|---|
| `UNIFIED_AD_GROUP` | `UNIFIED_CAMPAIGN` | `RESPONSIVE_AD`, `SHOPPING_AD`, `LISTING_AD`, `TEXT_AD`, `IMAGE_AD` (подтипы `TEXT_IMAGE_AD`, `TEXT_AD_BUILDER_AD`) |
| `TEXT_AD_GROUP` | `TEXT_CAMPAIGN` | `TEXT_AD`, `IMAGE_AD` (подтипы `TEXT_IMAGE_AD`, `TEXT_AD_BUILDER_AD`), `CPC_VIDEO_AD` |
| `MOBILE_APP_AD_GROUP` | `MOBILE_APP_CAMPAIGN` | `MOBILE_APP_AD`, `IMAGE_AD` (подтипы `MOBILE_APP_IMAGE_AD`, `MOBILE_APP_AD_BUILDER_AD`), `CPC_VIDEO_AD` (подтип `MOBILE_APP_CPC_VIDEO_AD_BUILDER_AD`) |
| `DYNAMIC_TEXT_AD_GROUP` | `DYNAMIC_TEXT_CAMPAIGN` | `DYNAMIC_TEXT_AD` |
| `SMART_AD_GROUP` | `SMART_CAMPAIGN` | `SMART_AD` |
| `CPM_BANNER_AD_GROUP` | `CPM_BANNER_CAMPAIGN` | `CPM_BANNER_AD` |
| `CPM_VIDEO_AD_GROUP` | `CPM_BANNER_CAMPAIGN` | `CPM_VIDEO_AD` |

| Подтип | Тип группы | Чем задаётся |
|---|---|---|
| `WEBPAGE` | `DYNAMIC_TEXT_AD_GROUP` | структура `DynamicTextAdGroup` с `DomainUrl` |
| `FEED` | `DYNAMIC_TEXT_AD_GROUP` | структура `DynamicTextFeedAdGroup` с `FeedId` |
| `KEYWORDS` | `CPM_BANNER_AD_GROUP` | пустая структура `CpmBannerKeywordsAdGroup` |
| `USER_PROFILE` | `CPM_BANNER_AD_GROUP` | пустая структура `CpmBannerUserProfileAdGroup` |

### 3.2. Параметры группы

| Параметр | Тип | `add` | `update` | `get` | Что означает |
|---|---|---|---|---|---|
| `Id` | long | — | Да | есть | Идентификатор группы |
| `Name` | string | Да | Нет | есть | Название, от 1 до 255 символов |
| `CampaignId` | long | Да | — | есть | Кампания. **В `update` параметра нет** — перенести группу нельзя |
| `RegionIds` | array of long | Да | Нет | есть | Регионы показа; `0` — все регионы; минус перед идентификатором выключает регион |
| `NegativeKeywords` | `ArrayOfString`, nillable | Нет | Нет | есть | Минус-фразы группы |
| `NegativeKeywordSharedSetIds` | `ArrayOfLong`, nillable | Нет | Нет | есть | До 3 наборов минус-фраз |
| `TrackingParams` | string | Нет | Нет | есть | GET-параметры для ссылок всех объявлений группы |
| Типовая структура | по типу группы | Нет | Нет | есть | `UnifiedAdGroup`, `MobileAppAdGroup`, `DynamicTextAdGroup`, `DynamicTextFeedAdGroup`, `SmartAdGroup`, `TextAdGroupFeedParams` |
| Пустая типовая структура | по типу группы | Нет | — | — | `CpmBannerKeywordsAdGroup`, `CpmBannerUserProfileAdGroup`, `CpmVideoAdGroup` — только в `add` |

#### Поля типовых структур

| Набор | Значений | Что принимает |
|---|---|---|
| `UnifiedAdGroupFieldNames` | 4 | `OfferRetargeting`, `PromotionExtension`, `Scenario`, `CurrentAudienceRetargetingConditionId` |
| `MobileAppAdGroupFieldNames` | 7 | `StoreUrl`, `TargetDeviceType`, `TargetCarrier`, `TargetOperatingSystemVersion`, `AppIconModeration`, `AppAvailabilityStatus`, `AppOperatingSystemType` |
| `DynamicTextAdGroupFieldNames` | 4 | `AutotargetingCategories`, `AutotargetingSettings`, `DomainUrl`, `DomainUrlProcessingStatus` |
| `DynamicTextFeedAdGroupFieldNames` | 8 | `AutotargetingCategories`, `AutotargetingSettings`, `Source`, `FeedId`, `AdTitleSource`, `AdBodySource`, `SourceType`, `SourceProcessingStatus` |
| `SmartAdGroupFieldNames` | 5 | `FeedId`, `AdTitleSource`, `AdBodySource`, `LogoExtensionHash`, `LogoExtensionModeration` |
| `TextAdGroupFeedParamsFieldNames` | 2 | `FeedId`, `FeedCategoryIds` |

## 4. Объявление (Ad)

### 4.1. Типы объявлений

| Тип | Подтип | Что это | Структура | Тип группы |
|---|---|---|---|---|
| `RESPONSIVE_AD` | `NONE` | **комбинаторное** | `ResponsiveAd` | `UNIFIED_AD_GROUP` |
| `SHOPPING_AD` | `NONE` | товарное | `ShoppingAd` | `UNIFIED_AD_GROUP` |
| `LISTING_AD` | `NONE` | страница каталога | `ListingAd` | `UNIFIED_AD_GROUP` |
| `TEXT_AD` | `NONE` | текстово-графическое | `TextAd` | `TEXT_AD_GROUP`, `UNIFIED_AD_GROUP` |
| `MOBILE_APP_AD` | `NONE` | продвижение приложения | `MobileAppAd` | `MOBILE_APP_AD_GROUP` |
| `DYNAMIC_TEXT_AD` | `NONE` | динамическое | `DynamicTextAd` | `DYNAMIC_TEXT_AD_GROUP` |
| `SMART_AD` | `NONE` | смарт-баннер | `SmartAdBuilderAd` | `SMART_AD_GROUP` |
| `IMAGE_AD` | `TEXT_IMAGE_AD` | графическое из изображения | `TextImageAd` | `TEXT_AD_GROUP`, `UNIFIED_AD_GROUP` |
| `IMAGE_AD` | `TEXT_AD_BUILDER_AD` | графическое из креатива | `TextAdBuilderAd` | `TEXT_AD_GROUP`, `UNIFIED_AD_GROUP` |
| `IMAGE_AD` | `MOBILE_APP_IMAGE_AD` | графическое для приложения | `MobileAppImageAd` | `MOBILE_APP_AD_GROUP` |
| `IMAGE_AD` | `MOBILE_APP_AD_BUILDER_AD` | графическое из креатива для приложения | `MobileAppAdBuilderAd` | `MOBILE_APP_AD_GROUP` |
| `CPC_VIDEO_AD` | `NONE` | видеообъявление | `CpcVideoAdBuilderAd` | `TEXT_AD_GROUP` |
| `CPC_VIDEO_AD` | `MOBILE_APP_CPC_VIDEO_AD_BUILDER_AD` | видеообъявление для приложения | `MobileAppCpcVideoAdBuilderAd` | `MOBILE_APP_AD_GROUP` |
| `CPM_BANNER_AD` | `NONE` | медийный баннер | `CpmBannerAdBuilderAd` | `CPM_BANNER_AD_GROUP` |
| `CPM_VIDEO_AD` | `NONE` | медийное видеообъявление | `CpmVideoAdBuilderAd` | `CPM_VIDEO_AD_GROUP` |

### 4.2. Что доступно только на чтение

| Что | Почему |
|---|---|
| `MobileAppAd`, `DynamicTextAd`, `MobileAppImageAd`, `MobileAppAdBuilderAd`, `CpcVideoAdBuilderAd`, `MobileAppCpcVideoAdBuilderAd`, `SmartAdBuilderAd` | перечислены среди допустимых значений в `add` и `update`, но таблиц полей и примеров для них документация не содержит |
| `Type` | «задаётся при создании и недоступен для изменения» |
| `AdCategories` | «изменение, присвоение или снятие категории через API недоступно» |
| `AgeLabel` | менять значение можно, добавить или снять метку — только через поддержку |
| `FeedId` в `ShoppingAd` и `ListingAd` | есть только в `...Add`; сменить фид нельзя |
| `TurboPageId`, `VCardId` | «параметр устарел, переданное значение не будет сохранено» |
| `Mobile`, `PreferVCardOverBusiness` | «параметр устарел, будет использовано значение `NO`» |
| объявления со статусом `ARCHIVED` | редактирование не допускается |
| объявления в архивной кампании | никакие операции невозможны |

### 4.5. Поля структур объявлений

#### `TextAd` — текстово-графическое

| Поле | Тип | `add` | `update` | Ограничение |
|---|---|---|---|---|
| `Title` | string | Да | Нет | 56 символов с учётом «узких», слово 22 |
| `Title2` | string, nillable | Нет | Нет | 30 без учёта «узких» + 15 «узких», слово 22 |
| `Text` | string | Да | Нет | 81 без учёта «узких» + 15 «узких», слово 23 |
| `Href` | string, nillable | хотя бы один из `Href`, `TurboPageId`, `VCardId`, `BusinessId` | Нет | 1024 символа, протокол и домен обязательны |
| `TurboPageId` | long, nillable | та же альтернатива | Нет | **устарел**, значение не сохраняется |
| `VCardId` | long, nillable | та же альтернатива | Нет | **устарел**, значение не сохраняется |
| `BusinessId` | long, nillable | та же альтернатива | Нет | только при `IsPublished = YES` |
| `Mobile` | YesNoEnum | Да | — | **устарел**, всегда `NO` |
| `PreferVCardOverBusiness` | YesNoEnum | Нет | Нет | **устарел**, всегда `NO` |
| `AdImageHash` | string, nillable | Нет | Нет | только типы `REGULAR` и `WIDE` |
| `SitelinkSetId` | long, nillable | Нет | Нет | только при `Href` или `TurboPageId` |
| `DisplayUrlPath` | string, nillable | Нет | Нет | 20 символов, только при `Href` |
| `AdExtensionIds` | array of long | Нет | — | до 50; в `update` заменён на `CalloutSetting` |
| `CalloutSetting` | `AdExtensionSetting`, nillable | — | Нет | `ADD` / `REMOVE` / `SET`, `null` отвязывает все |
| `VideoExtension` | `VideoExtension…Item` | Нет | Нет | при `ENABLE_VIDEO_EXTENSION_BY_DEFAULT = YES` формируется сам |
| `PriceExtension` | `PriceExtension…Item`, nillable | Нет | Нет | см. [8.5](#85-цена-в-объявлении) |
| `AgeLabel` | AgeLabelEnum | — | Нет | задать отсутствующую метку нельзя |
| `ErirAdDescription` | string | Нет | Нет | ограничения длины нет |
| `DisplayDomain` | string, nillable | — | — | только чтение; домен определяется по ссылке |
| `DisplayUrlPathModeration` | `ExtensionModeration`, nillable | — | — | только чтение |
| `VCardModeration` | `ExtensionModeration`, nillable | — | — | только чтение |
| `SitelinksModeration` | `ExtensionModeration`, nillable | — | — | только чтение |
| `AdImageModeration` | `ExtensionModeration`, nillable | — | — | только чтение |
| `AdExtensions` | array of `AdExtensionAdGetItem` | — | — | только чтение; привязанные расширения (`AdExtensionId`, `Type`), не результат модерации |

#### `TextImageAd` — графическое из изображения

| Поле | Тип | `add` | `update` | Ограничение |
|---|---|---|---|---|
| `AdImageHash` | string | Да | Нет | только тип `FIXED_IMAGE` |
| `Href` | string, nillable | хотя бы один из `Href` и `TurboPageId` | Нет | 1024 символа |
| `TurboPageId` | long, nillable | та же альтернатива | Нет | — |
| `ErirAdDescription` | string | Нет | Нет | — |

#### `TextAdBuilderAd` — графическое из креатива

| Поле | Тип | `add` | `update` | Ограничение |
|---|---|---|---|---|
| `Creative` | `AdBuilderAd…Item` | Да | Нет | креатив из конструктора |
| `Href` | string, nillable | хотя бы один из `Href` и `TurboPageId` | Нет | 1024 символа |
| `TurboPageId` | long, nillable | та же альтернатива | Нет | — |
| `ErirAdDescription` | string | Нет | Нет | — |

#### `CpmBannerAdBuilderAd` — медийный баннер

| Поле | Тип | `add` | `update` | Ограничение |
|---|---|---|---|---|
| `Creative` | `AdBuilderAd…Item` | Да | Нет | креатив из веб-интерфейса или конструктора |
| `Href` | string, nillable | хотя бы один из `Href` и `TurboPageId` | Нет | 1024 символа |
| `TurboPageId` | long, nillable | та же альтернатива | Нет | — |
| `TrackingPixels` | ArrayOfString, nillable | Нет | Нет | до 2 строк по 1024 символа; в счётчике ADFOX обязателен макрос `%random%` или `%aw_random%` |
| `ErirAdDescription` | string | Нет | Нет | — |

#### `ShoppingAd` и `ListingAd` — товарное и для страниц каталога

| Поле | Тип | `add` | `update` | Ограничение |
|---|---|---|---|---|
| `FeedId` | long | Да | — | **сменить фид нельзя**: поля в `update` нет |
| `DefaultTexts` | array of string | Да | **Да** | ровно одно значение |
| `FeedFilterConditions` | `FeedFilterConditionsItem` | Нет | Нет | до 30 фильтров, суммарно 65 КБайт JSON; `Arguments` при чтении до 10 строк |
| `TitleSources` | array of string | Нет | Нет | имена полей — из `Feeds.get`, `TitleAndTextSources` |
| `TextSources` | array of string | Нет | Нет | то же |
| `SitelinkSetId` | long, nillable | Нет | Нет | — |
| `AdExtensionIds` | array of long | Нет | — | до 50; в `update` заменён на `CalloutSetting` |
| `CalloutSetting` | `CalloutSetting`, nillable | — | Нет | `ADD` / `REMOVE` / `SET` |
| `BusinessId` | long, nillable | Нет | Нет | только при `IsPublished = YES` |

#### Типы, у которых записи в документации нет

| Структура | Что содержит по `get` |
|---|---|
| `MobileAppAd` | `Title` (56 с учётом «узких», слово 22), `Text` (75 с учётом «узких», слово 23), `TrackingUrl`, `Action` (`DOWNLOAD`, `GET`, `INSTALL`, `MORE`, `OPEN`, `UPDATE`, `PLAY`, `BUY_AUTODETECT`), `AdImageHash` (только `WIDE`), `Features`, `VideoExtension`, `ErirAdDescription`, `AdImageModeration` |
| `DynamicTextAd` | `Text` (81 без учёта «узких» + 15 «узких», слово 23), `VCardId`, `AdImageHash` (`REGULAR`, `WIDE`), `SitelinkSetId`, `VCardModeration`, `SitelinksModeration`, `AdImageModeration`, `AdExtensions` |
| `MobileAppImageAd` | `AdImageHash` (только `FIXED_IMAGE`), `TrackingUrl`, `ErirAdDescription` |
| `MobileAppAdBuilderAd` | `Creative`, `TrackingUrl`, `ErirAdDescription` |
| `CpcVideoAdBuilderAd` | `Creative`, `Href` (1024), `TurboPageId`, `ErirAdDescription` |
| `MobileAppCpcVideoAdBuilderAd` | `Creative`, `TrackingUrl`, `ErirAdDescription` |
| `SmartAdBuilderAd` | `Creative`, `LogoExtensionHash`, `LogoExtensionModeration` |

#### Дополнительные поля чтения

| Поведение при чтении | Имена |
|---|---|
| поле отдаётся, ключ есть в теле `TextAd` | `DutPrefix`, `DutSuffix`, `ButtonExtension`, `ButtonExtensionModeration`, `TrackingPhoneId` |
| имя принято, ключ в ответе **не появляется** — ни со значением, ни с `null` | `LogoExtensionHash`, `LogoExtensionModeration` |
| `error_code 5006`, «Неверное использование поля» | `LfHref`, `LfButtonText` |
| `error_code 4000`, «Неизвестное поле» | `Carousel`, `TrackingParams` |

#### Наборы полей чтения

| Набор | Значений | Что принимает |
|---|---|---|
| `ResponsiveAdFieldNames` | 22 | `AdImages`, `DisplayDomain`, `Href`, `FinalUrl`, `SitelinkSetId`, `Texts`, `Titles`, `DisplayUrlPath`, `DutPrefix`, `DutSuffix`, `SitelinksModeration`, `AdExtensions`, `DisplayUrlPathModeration`, `PriceExtension`, `VideoExtensions`, `ButtonExtensionModeration`, `BusinessId`, `TrackingPhoneId`, `ButtonExtension`, `ErirAdDescription`, `TrackingParams`, `Carousel` |
| `TextAdFieldNames` | 34 | `AdImageHash`, `LogoExtensionHash`, `LogoExtensionModeration`, `DisplayDomain`, `FinalUrl`, `Href`, `SitelinkSetId`, `Text`, `Title`, `Title2`, `Mobile`, `VCardId`, `DisplayUrlPath`, `DutPrefix`, `DutSuffix`, `AdImageModeration`, `SitelinksModeration`, `VCardModeration`, `AdExtensions`, `DisplayUrlPathModeration`, `VideoExtension`, `TurboPageId`, `TurboPageModeration`, `ButtonExtensionModeration`, `BusinessId`, `TrackingPhoneId`, `PreferVCardOverBusiness`, `ButtonExtension`, `LfHref`, `LfButtonText`, `ErirAdDescription`, `AutogeneratedErirAdDescription`, `Carousel`, `TrackingParams` |
| `TextAdPriceExtensionFieldNames` | 4 | `Price`, `OldPrice`, `PriceCurrency`, `PriceQualifier` |
| `TextImageAdFieldNames` | 15 | `AdImageHash`, `LogoExtensionHash`, `LogoExtensionModeration`, `FinalUrl`, `Href`, `TurboPageId`, `TurboPageModeration`, `ButtonExtensionModeration`, `ButtonExtension`, `Title`, `Title2`, `Text`, `ErirAdDescription`, `AutogeneratedErirAdDescription`, `TrackingParams` |
| `TextAdBuilderAdFieldNames` | 16 | `LogoExtensionHash`, `LogoExtensionModeration`, `Creative`, `FinalUrl`, `Href`, `TurboPageId`, `TurboPageModeration`, `ButtonExtensionModeration`, `ButtonExtension`, `Title`, `Title2`, `Text`, `ErirAdDescription`, `AutogeneratedErirAdDescription`, `Carousel`, `TrackingParams` |
| `ShoppingAdFieldNames` | 17 | `SitelinkSetId`, `SitelinksModeration`, `AdExtensions`, `BusinessId`, `TrackingPhoneId`, `FeedId`, `FeedFilterConditions`, `FeedProcessingStatus`, `TitleSources`, `TextSources`, `DefaultTexts`, `BodySources`, `DefaultBodies`, `GenerationScopes`, `ListingFeedFilterConditions`, `ListingTitleSources`, `ListingTextSources` |
| `ListingAdFieldNames` | 13 | `SitelinkSetId`, `SitelinksModeration`, `AdExtensions`, `BusinessId`, `TrackingPhoneId`, `FeedId`, `FeedFilterConditions`, `FeedProcessingStatus`, `TitleSources`, `TextSources`, `DefaultTexts`, `ButtonExtension`, `ButtonExtensionModeration` |
| `MobileAppAdFieldNames` | 11 | `AdImageHash`, `Title`, `Text`, `Features`, `Action`, `TrackingUrl`, `ImpressionUrl`, `AdImageModeration`, `VideoExtension`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `MobileAppImageAdFieldNames` | 4 | `AdImageHash`, `TrackingUrl`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `MobileAppAdBuilderAdFieldNames` | 4 | `Creative`, `TrackingUrl`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `DynamicTextAdFieldNames` | 8 | `AdImageHash`, `SitelinkSetId`, `Text`, `VCardId`, `AdImageModeration`, `SitelinksModeration`, `VCardModeration`, `AdExtensions` |
| `CpcVideoAdBuilderAdFieldNames` | 6 | `Creative`, `Href`, `TurboPageId`, `TurboPageModeration`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `MobileAppCpcVideoAdBuilderAdFieldNames` | 4 | `Creative`, `TrackingUrl`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `CpmBannerAdBuilderAdFieldNames` | 8 | `Creative`, `Href`, `TrackingPixels`, `TurboPageId`, `TurboPageModeration`, `TnsId`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `CpmVideoAdBuilderAdFieldNames` | 15 | `LogoExtensionHash`, `LogoExtensionModeration`, `Creative`, `Href`, `TrackingPixels`, `TurboPageId`, `TurboPageModeration`, `TnsId`, `ButtonExtensionModeration`, `ButtonExtension`, `Title`, `Title2`, `Text`, `ErirAdDescription`, `AutogeneratedErirAdDescription` |
| `SmartAdBuilderAdFieldNames` | 3 | `LogoExtensionHash`, `LogoExtensionModeration`, `Creative` |

| Набор | Имя | Код | Что означает |
|---|---|---|---|
| `ResponsiveAdFieldNames` | `TrackingParams` | 4000 | «Неизвестное поле» — имя знакомо перечислению, полю нет |
| `TextAdFieldNames` | `TrackingParams`, `Carousel` | 4000 | то же |
| `TextImageAdFieldNames` | `TrackingParams` | 4000 | то же |
| `TextAdBuilderAdFieldNames` | `TrackingParams` | 4000 | то же |
| `TextAdFieldNames` | `LfHref`, `LfButtonText` | 5006 | «Неверное использование поля» — поле есть, но не для этого случая |
| `UnifiedAdGroupFieldNames` | `PromotionExtension` | 5006 | то же, у группы — [3.2](#32-параметры-группы) |
| `ShoppingAdFieldNames` | `BodySources`, `DefaultBodies` | 8000 | второй, узкий ответ перечисления — [5.4](#54-чтение) |

## 5. ResponsiveAd — единственный формат записи

### 5.1. Состав комплекта

| Элемент | Сколько | Длина элемента | Слово |
|---|---|---|---|
| Заголовки (`Titles`) | от 1 до 7 | 56 символов с учётом «узких» | 22 символа |
| Тексты (`Texts`) | от 1 до 3 | 81 символ без учёта «узких» + 15 «узких» | 23 символа |
| Изображения (`AdImageHashes`) | от 1 до 5 | — | — |
| Видеодополнения (`VideoExtensionIds`) | от 1 до 6 | — | — |
| Неархивных **комбинаторных** объявлений в группе | 3 | — | — |
| Комбинаторных объявлений в группе с учётом архивных | 10 | — | — |

### 5.2. `ResponsiveAdAdd`

| Параметр | Тип | Обязательный | Что означает |
|---|---|---|---|
| `Titles` | array of string | **Да** | Набор заголовков, от 1 до 7 |
| `Texts` | array of string | **Да** | Набор текстов, от 1 до 3 |
| `Href` | string | Хотя бы один из `Href` или `BusinessId` | Ссылка на сайт, до 1024 символов, с протоколом и доменом |
| `BusinessId` | long | там же | Профиль организации на Яндексе; привязать можно только при `IsPublished = YES` |
| `AdImageHashes` | array of string | Нет | Хэши изображений, от 1 до 5 |
| `VideoExtensionIds` | array of long | Нет | Идентификаторы видеодополнений, от 1 до 6 |
| `SitelinkSetId` | long | Нет | Набор быстрых ссылок; **допустим только при наличии `Href`** |
| `DisplayUrlPath` | string | Нет | Отображаемая ссылка, до 20 символов; **только при наличии `Href`** |
| `AdExtensionIds` | array of long | Нет | Уточнения, до 50 элементов |
| `PriceExtension` | `PriceExtensionAddItem` | Нет | Цена в объявлении |
| `AgeLabel` | `AgeLabelEnum` | Нет | Возрастная категория |
| `ErirAdDescription` | string | Нет | Описание объекта продвижения |

### 5.3. `ResponsiveAdUpdate` и сохранение полного комплекта

| Метод | Тип в WSDL | Форма в JSON |
|---|---|---|
| `add` | `ResponsiveAdAdd`, элемент с `maxOccurs="unbounded"` | плоский массив: `"AdImageHashes": ["hash", …]` |
| `update` | `ResponsiveAdUpdate`, тип `general:ArrayOfString`, `nillable="true"` | объект: `"AdImageHashes": {"Items": ["hash", …]}`, `null` отвязывает |

### 5.4. Чтение

| Поле ответа | Тип | Что означает |
|---|---|---|
| `Titles` | array of `TitleGetItem` | `Title`, `Status`, `StatusClarification` для каждого заголовка |
| `Texts` | array of `TextGetItem` | `Text`, `Status`, `StatusClarification` для каждого текста |
| `AdImages` | `ArrayOfAdImageGet`, nillable | `ImageHash`, `ImageUrl`, `Status`, `StatusClarification` |
| `VideoExtensions` | `ArrayOfVideoExtensionGet`, nillable | `CreativeId`, `ThumbnailUrl`, `PreviewUrl`, `Status`, `StatusClarification` |
| `Href`, `DisplayUrlPath`, `SitelinkSetId`, `BusinessId`, `PriceExtension` | nillable | как при записи |
| `DisplayDomain` | string, nillable | продвигаемый домен, определяется автоматически |
| `DisplayUrlPathModeration`, `SitelinksModeration` | `ExtensionModeration`, nillable | результат модерации дополнений |
| `AdExtensions` | array of `AdExtensionAdGetItem` | уточнения |
| `ErirAdDescription` | string, nillable | описание объекта продвижения |

| Поле | Что приходит |
|---|---|
| `Carousel` | объект с `Items`; элемент — `ImageHash`, `Status`, `StatusClarification`. Встречены карусели на 2, 6 и 10 изображений |
| `ButtonExtension` | объект `Href`, `CustomActionText`, `Action`. Значения `Action`: `MORE`, `GO_TO_WEBSITE`, `GET_DISCOUNT`, `WRITE_WHATSAPP`, `WRITE_TELEGRAM` — перечень не полон, он собран по встреченным объявлениям |
| `ButtonExtensionModeration` | `ExtensionModeration`: `Status`, `StatusClarification` («Кнопка принята на модерации») |
| `TrackingPhoneId` | long, nillable — идентификатор подменного номера. Непустым приходит там, где номер подменяется; у объявления без подмены — `null` |
| `FinalUrl` | string, всюду `null` |
| `DutPrefix`, `DutSuffix` | string, всюду `null` |
| `TrackingParams` | ничего: принимается перечислением, чтением не отдаётся |

### 5.5. Переход с текстово-графических объявлений

| Операция | Что произойдёт |
|---|---|
| `add` с `TextAd` | создаст **комбинаторное** объявление типа `RESPONSIVE_AD`, а не текстово-графическое |
| `update` с `TextAd` над комбинаторным | «после обновления в объявлении останется один заголовок, текст, изображение, видео» |
| `Title2` при обеих операциях | приклеивается к основному заголовку через «. », если сумма укладывается в 56 символов, **иначе теряется** |
| `get` с `TextAdFieldNames` без `ResponsiveAdFieldNames` | комбинаторные объявления вернутся как текстово-графические: только первые заголовок, текст, изображение и видео |

## 6. Стратегии показа

### 6.1. Ручные стратегии

| На поиске | В сетях | Тип кампании |
|---|---|---|
| `HIGHEST_POSITION` | `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `SERVING_OFF` | `MANUAL_CPM` | `CPM_BANNER_CAMPAIGN` |

### 6.2. Автоматические стратегии

| На поиске | В сетях | Тип кампании |
|---|---|---|
| `AVERAGE_CPA` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `AVERAGE_CPA_MULTIPLE_GOALS` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `AVERAGE_CPC` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `AVERAGE_CRR` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `MAX_PROFIT` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `PAY_FOR_CONVERSION` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `PAY_FOR_CONVERSION_CRR` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `PAY_FOR_CONVERSION_MULTIPLE_GOALS` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `WB_MAXIMUM_CLICKS` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `WB_MAXIMUM_CONVERSION_RATE` | `NETWORK_DEFAULT` или `SERVING_OFF` | `UNIFIED_CAMPAIGN` |
| `SERVING_OFF` | `AVERAGE_CPA`, `AVERAGE_CPC`, `AVERAGE_CRR`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR` | `UNIFIED_CAMPAIGN` |
| `SERVING_OFF` | `CP_DECREASED_PRICE_FOR_REPEATED_IMPRESSIONS`, `CP_MAXIMUM_IMPRESSIONS`, `WB_DECREASED_PRICE_FOR_REPEATED_IMPRESSIONS`, `WB_MAXIMUM_IMPRESSIONS`, `WB_AVERAGE_CPV`, `CP_AVERAGE_CPV` | `CPM_BANNER_CAMPAIGN` |

### 6.3. Справочник типов стратегий

| Тип | Что это |
|---|---|
| `HIGHEST_POSITION` | максимум кликов с ручными ставками |
| `MANUAL_CPM` | ручное управление ставками для медийных кампаний |
| `SERVING_OFF` | показы отключены |
| `NETWORK_DEFAULT` | показы в сетях по настройкам поиска |
| `AVERAGE_CPC` | оптимизация кликов, ограничивать по средней цене клика |
| `WB_MAXIMUM_CLICKS` | оптимизация кликов, ограничивать по недельному бюджету |
| `AVERAGE_CPA` | оптимизация конверсий, удерживать среднюю цену конверсии |
| `AVERAGE_CPA_MULTIPLE_GOALS` | то же по нескольким целям, оплата за клики |
| `WB_MAXIMUM_CONVERSION_RATE` | оптимизация конверсий без указания средней цены |
| `PAY_FOR_CONVERSION` | оптимизация конверсий, оплата за конверсии |
| `PAY_FOR_CONVERSION_MULTIPLE_GOALS` | оплата за конверсии по каждой из указанных целей |
| `AVERAGE_CRR` | оптимизация доли рекламных расходов |
| `PAY_FOR_CONVERSION_CRR` | доля рекламных расходов с оплатой за конверсии |
| `MAX_PROFIT` | максимум прибыли по марже с конверсии |
| `WEEKLY_CLICK_PACKAGE` | максимум кликов, ограничивать по пакету кликов |
| `WB_MAXIMUM_IMPRESSIONS` | максимум показов по минимальной цене, еженедельно |
| `CP_MAXIMUM_IMPRESSIONS` | то же за период |
| `WB_DECREASED_PRICE_FOR_REPEATED_IMPRESSIONS` | снижение цены повторных показов, еженедельно |
| `CP_DECREASED_PRICE_FOR_REPEATED_IMPRESSIONS` | то же за период |
| `WB_AVERAGE_CPV` | оплата за просмотры, еженедельно |
| `CP_AVERAGE_CPV` | то же за период |
| `UNKNOWN` | стратегия, не поддерживаемая этой версией API |

### 6.4. Пакетные стратегии

| Что | Допустимо |
|---|---|
| Типы стратегий | `AVERAGE_CPA`, `AVERAGE_CPA_MULTIPLE_GOALS`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_MULTIPLE_GOALS`, `WB_MAXIMUM_CONVERSION_RATE`, `WB_MAXIMUM_CLICKS`, `AVERAGE_CRR`, `PAY_FOR_CONVERSION_CRR` |
| Типы кампаний | `TEXT_CAMPAIGN`, `DYNAMIC_TEXT_CAMPAIGN`, `SMART_CAMPAIGN`, `UNIFIED_CAMPAIGN` |
| Смешивание типов | запрещено: в одну стратегию добавляются кампании только одного типа |

### 6.6. Состав структур стратегий

#### Какому блоку кабинета отвечает какое место показа

| Значение | Как называет его документация API | Переключатель кабинета |
|---|---|---|
| `SearchResults` | «Поисковая выдача» | Места показа → Ручная настройка → **Продвижение в поисковой выдаче** |
| `ProductGallery` | «Товарная галерея» | → **Товарная галерея на поиске** |
| `DynamicPlaces` | «Динамические места на поиске» | → **Динамические места на поиске** (бета) |
| `Maps` | «Яндекс Карты» | → **Яндекс Карты** |
| `SearchOrganizationList` | «Список организаций в результатах поиска» | → **Список организаций, отели и галерея услуг** |

| Значение | Пишется | Почему |
|---|---|---|
| `SearchResults` | да | Поле читается и пишется, сверка после записи полная |
| `ProductGallery` | да | То же |
| `DynamicPlaces` | нет | Записи не поддаётся: документация откладывает управление, а отправленное значение заменяет значением `SearchResults` |
| `Maps` | нет | Флажку отвечает пара полей, и сетевое из них в чтении недоступно — сверка после записи проверит половину |
| `SearchOrganizationList` | нет | Кабинетный флажок шире поля, и что включит его запись, документация не говорит |

#### Какие типы допустимы у какого типа кампании

| Тип кампании | На поиске | В сетях |
|---|---|---|
| ЕПК | `AVERAGE_CPA`, `AVERAGE_CPA_MULTIPLE_GOALS`, `AVERAGE_CPC`, `AVERAGE_CRR`, `HIGHEST_POSITION`, `MAX_PROFIT`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR`, `PAY_FOR_CONVERSION_MULTIPLE_GOALS`, `SERVING_OFF`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE` | `AVERAGE_CPA`, `AVERAGE_CPA_MULTIPLE_GOALS`, `AVERAGE_CPC`, `AVERAGE_CRR`, `MAX_PROFIT`, `NETWORK_DEFAULT`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR`, `PAY_FOR_CONVERSION_MULTIPLE_GOALS`, `SERVING_OFF`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE` |
| `TEXT_CAMPAIGN` | `AVERAGE_CPA`, `AVERAGE_CPA_MULTIPLE_GOALS`, `AVERAGE_CPC`, `AVERAGE_CRR`, `AVERAGE_ROI`, `HIGHEST_POSITION`, `IMPRESSIONS_BELOW_SEARCH`, `MAX_PROFIT`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR`, `PAY_FOR_CONVERSION_MULTIPLE_GOALS`, `SERVING_OFF`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE`, `WEEKLY_CLICK_PACKAGE` | `AVERAGE_CPA`, `AVERAGE_CPA_MULTIPLE_GOALS`, `AVERAGE_CPC`, `AVERAGE_CRR`, `AVERAGE_ROI`, `MAXIMUM_COVERAGE`, `MAX_PROFIT`, `NETWORK_DEFAULT`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR`, `PAY_FOR_CONVERSION_MULTIPLE_GOALS`, `SERVING_OFF`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE`, `WEEKLY_CLICK_PACKAGE` |
| `MOBILE_APP_CAMPAIGN` | `AVERAGE_CPC`, `AVERAGE_CPI`, `HIGHEST_POSITION`, `IMPRESSIONS_BELOW_SEARCH`, `PAY_FOR_INSTALL`, `SERVING_OFF`, `WB_MAXIMUM_APP_INSTALLS`, `WB_MAXIMUM_CLICKS`, `WEEKLY_CLICK_PACKAGE` | `AVERAGE_CPC`, `AVERAGE_CPI`, `MAXIMUM_COVERAGE`, `NETWORK_DEFAULT`, `PAY_FOR_INSTALL`, `SERVING_OFF`, `WB_MAXIMUM_APP_INSTALLS`, `WB_MAXIMUM_CLICKS`, `WEEKLY_CLICK_PACKAGE` |
| `DYNAMIC_TEXT_CAMPAIGN` | `AVERAGE_CPA`, `AVERAGE_CPC`, `AVERAGE_CRR`, `AVERAGE_ROI`, `HIGHEST_POSITION`, `IMPRESSIONS_BELOW_SEARCH`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR`, `SERVING_OFF`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE`, `WEEKLY_CLICK_PACKAGE` | `AVERAGE_CPA`, `AVERAGE_CPC`, `AVERAGE_CRR`, `AVERAGE_ROI`, `MAXIMUM_COVERAGE`, `NETWORK_DEFAULT`, `PAY_FOR_CONVERSION`, `PAY_FOR_CONVERSION_CRR`, `SERVING_OFF`, `WB_MAXIMUM_CLICKS`, `WB_MAXIMUM_CONVERSION_RATE`, `WEEKLY_CLICK_PACKAGE` |
| `CPM_BANNER_CAMPAIGN` | `SERVING_OFF` | `CP_AVERAGE_CPV`, `CP_DECREASED_PRICE_FOR_REPEATED_IMPRESSIONS`, `CP_MAXIMUM_IMPRESSIONS`, `MANUAL_CPM`, `WB_AVERAGE_CPV`, `WB_DECREASED_PRICE_FOR_REPEATED_IMPRESSIONS`, `WB_MAXIMUM_IMPRESSIONS` |
| `SMART_CAMPAIGN` | `AVERAGE_CPA_PER_CAMPAIGN`, `AVERAGE_CPA_PER_FILTER`, `AVERAGE_CPC_PER_CAMPAIGN`, `AVERAGE_CPC_PER_FILTER`, `AVERAGE_CRR`, `AVERAGE_ROI`, `PAY_FOR_CONVERSION_CRR`, `PAY_FOR_CONVERSION_PER_CAMPAIGN`, `PAY_FOR_CONVERSION_PER_FILTER`, `SERVING_OFF` | `AVERAGE_CPA_PER_CAMPAIGN`, `AVERAGE_CPA_PER_FILTER`, `AVERAGE_CPC_PER_CAMPAIGN`, `AVERAGE_CPC_PER_FILTER`, `AVERAGE_CRR`, `AVERAGE_ROI`, `NETWORK_DEFAULT`, `PAY_FOR_CONVERSION_CRR`, `PAY_FOR_CONVERSION_PER_CAMPAIGN`, `PAY_FOR_CONVERSION_PER_FILTER`, `SERVING_OFF` |

#### Поля структур

| Структура | Поля | Имя поля в `…Add` |
|---|---|---|
| `AverageCpa` | **`AverageCpa`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `ExplorationBudget`, `BudgetType` | то же имя |
| `AverageCpaMultipleGoals` | `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `ExplorationBudget`, `BudgetType` | то же имя |
| `AverageCpaPerCampaign` | **`AverageCpa`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `ExplorationBudget`, `BudgetType` | не проверено: страницы `add-*` у `SMART_CAMPAIGN` нет |
| `AverageCpaPerFilter` | **`FilterAverageCpa`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `ExplorationBudget`, `BudgetType` | не проверено: страницы `add-*` у `SMART_CAMPAIGN` нет |
| `AverageCpc` | **`AverageCpc`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BudgetType` | то же имя |
| `AverageCpcPerCampaign` | **`AverageCpc`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `BudgetType` | не проверено: страницы `add-*` у `SMART_CAMPAIGN` нет |
| `AverageCpcPerFilter` | `FilterAverageCpc`, `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `BudgetType` | не проверено: страницы `add-*` у `SMART_CAMPAIGN` нет |
| `AverageCpi` | **`AverageCpi`**, `WeeklySpendLimit`, `BidCeiling` | то же имя |
| `AverageCrr` | **`Crr`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `ExplorationBudget`, `BudgetType` | то же имя |
| `AverageRoi` | **`ReserveReturn`**, **`RoiCoef`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BidCeiling`, `Profitability`, `ExplorationBudget`, `BudgetType` | у `TEXT_CAMPAIGN` поля нет; у `DYNAMIC_TEXT_CAMPAIGN` и `SMART_CAMPAIGN` не проверено |
| `CpAverageCpv` | **`AverageCpv`**, **`SpendLimit`**, **`StartDate`**, **`EndDate`**, **`AutoContinue`** | то же имя |
| `CpDecreasedPriceForRepeatedImpressions` | **`AverageCpm`**, **`SpendLimit`**, **`StartDate`**, **`EndDate`**, **`AutoContinue`** | то же имя |
| `CpMaximumImpressions` | **`AverageCpm`**, **`SpendLimit`**, **`StartDate`**, **`EndDate`**, **`AutoContinue`** | то же имя |
| `HighestPosition` | `WeeklySpendLimit` | у `TEXT_CAMPAIGN` поля нет |
| `ManualCpm` | `WeeklySpendLimit` | то же имя |
| `MaxProfit` | `WeeklySpendLimit`, `CustomPeriodBudget`, `ExplorationBudget`, `BudgetType` | то же имя |
| `MaximumAppInstalls` | `WeeklySpendLimit`, `BidCeiling` | поле зовётся `WbMaximumAppInstalls` |
| `MaximumClicks` | `WeeklySpendLimit`, `BidCeiling`, `CustomPeriodBudget`, `BudgetType` | поле зовётся `WbMaximumClicks` |
| `MaximumConversionRate` | `WeeklySpendLimit`, `BidCeiling`, **`GoalId`**, `CustomPeriodBudget`, `BudgetType` | поле зовётся `WbMaximumConversionRate` |
| `NetworkDefault` | `LimitPercent` | у ЕПК поля нет |
| `PayForConversion` | **`Cpa`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BudgetType` | то же имя |
| `PayForConversionCrr` | **`Crr`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BudgetType` | то же имя |
| `PayForConversionMultipleGoals` | `WeeklySpendLimit`, `CustomPeriodBudget`, `BudgetType` | то же имя |
| `PayForConversionPerCampaign` | **`Cpa`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BudgetType` | не проверено: страницы `add-*` у `SMART_CAMPAIGN` нет |
| `PayForConversionPerFilter` | **`Cpa`**, **`GoalId`**, `WeeklySpendLimit`, `CustomPeriodBudget`, `BudgetType` | не проверено: страницы `add-*` у `SMART_CAMPAIGN` нет |
| `PayForInstall` | **`AverageCpi`**, `WeeklySpendLimit` | то же имя |
| `WbAverageCpv` | **`AverageCpv`**, **`SpendLimit`** | то же имя |
| `WbDecreasedPriceForRepeatedImpressions` | **`AverageCpm`**, **`SpendLimit`** | то же имя |
| `WbMaximumImpressions` | **`AverageCpm`**, **`SpendLimit`** | то же имя |
| `WeeklyClickPackage` | **`ClicksPerWeek`**, `AverageCpc`, `BidCeiling` | то же имя |

#### Структура есть не у каждого типа

| Тип | Половина | Код | Ожидаемое поле | Код называет страница | Строка в «Полях структур» |
|---|---|---|---|---|---|
| ЕПК | сети | `NETWORK_DEFAULT` | `NetworkDefault` | да | есть |
| `TEXT_CAMPAIGN` | поиск | `AVERAGE_ROI` | `AverageRoi` | нет, только схема | есть |
| `TEXT_CAMPAIGN` | поиск | `HIGHEST_POSITION` | `HighestPosition` | да | есть |
| `TEXT_CAMPAIGN` | поиск | `IMPRESSIONS_BELOW_SEARCH` | `ImpressionsBelowSearch` | нет, только схема | нет |
| `TEXT_CAMPAIGN` | сети | `AVERAGE_ROI` | `AverageRoi` | нет, только схема | есть |
| `TEXT_CAMPAIGN` | сети | `MAXIMUM_COVERAGE` | `MaximumCoverage` | да | нет |
| `MOBILE_APP_CAMPAIGN` | поиск | `IMPRESSIONS_BELOW_SEARCH` | `ImpressionsBelowSearch` | нет, только схема | нет |
| `MOBILE_APP_CAMPAIGN` | сети | `MAXIMUM_COVERAGE` | `MaximumCoverage` | да | нет |

| Строка таблицы | Поле в `…Add` | Схема структуры |
|---|---|---|
| `MaximumClicks` | `WbMaximumClicks` | `StrategyMaximumClicksAdd` |
| `MaximumConversionRate` | `WbMaximumConversionRate` | `StrategyMaximumConversionRateAdd` |
| `MaximumAppInstalls` | `WbMaximumAppInstalls` | `StrategyMaximumAppInstallsAdd` |

## 7. Ключевые фразы и минус-фразы

| Уровень | Поле | Суммарная длина |
|---|---|---|
| Ключевая фраза | минус-слова внутри `Keyword` | входит в 4096 символов фразы |
| Группа | `AdGroup.NegativeKeywords` | 4096 символов |
| Кампания | `Campaign.NegativeKeywords` | 20 000 символов |
| Набор | `NegativeKeywordSharedSet` | 4096 символов на набор |

### 7.1. Настройки автотаргетинга

| Половина | Поля | Значения |
|---|---|---|
| `Categories` | `Exact`, `Narrow`, `Alternative`, `Accessory`, `Broader` | `YES`, `NO` |
| `BrandOptions` | `WithoutBrands`, `WithAdvertiserBrand`, `WithCompetitorsBrand` | `YES`, `NO` |

| Поле | Элемент | Перечень |
|---|---|---|
| `AutotargetingCategories` | `Category` и `Value` | `EXACT`, `ALTERNATIVE`, `COMPETITOR`, `BROADER`, `ACCESSORY` |
| `AutotargetingBrandOptions` | `Option` и `Value` | `WITHOUT_BRANDS`, `WITH_ADVERTISER_BRAND` |

#### Что чему равно

| Плоское имя | Половина вложенной формы | Поле |
|---|---|---|
| `EXACT` — категория | `Categories` | `Exact` |
| `ALTERNATIVE` — категория | `Categories` | `Alternative` |
| `BROADER` — категория | `Categories` | `Broader` |
| `ACCESSORY` — категория | `Categories` | `Accessory` |
| `COMPETITOR` — тоже категория | `BrandOptions` | `WithCompetitorsBrand` |
| `WITHOUT_BRANDS` — признак бренда | `BrandOptions` | `WithoutBrands` |
| `WITH_ADVERTISER_BRAND` — признак бренда | `BrandOptions` | `WithAdvertiserBrand` |
| плоской формой не выражается | `Categories` | `Narrow` |

## 8. Дополнения к объявлению

### 8.1. Быстрые ссылки

| Поле `Sitelink` | Тип | Обязательный | Ограничение |
|---|---|---|---|
| `Title` | string | Да | до 30 символов |
| `Href` | string | хотя бы один из `Href` и `TurboPageId` | до 1024 символов, с протоколом и доменом |
| `TurboPageId` | long | там же | идентификатор Турбо-страницы |
| `Description` | string | Нет | до 60 символов, для расширенного формата |

### 8.3. Изображения

| Тип | Что это | Для каких объявлений |
|---|---|---|
| `REGULAR` | соотношение от 1:1 до 3:4 и 4:3, стороны 450…5000 пикселей | `TEXT_AD`, `DYNAMIC_TEXT_AD` |
| `WIDE` | 16:9, от 1080 × 607 до 5000 × 2812 | `TEXT_AD`, `DYNAMIC_TEXT_AD`, `MOBILE_APP_AD` |
| `FIXED_IMAGE` | один из фиксированных размеров | `IMAGE_AD` |
| `SMALL`, `UNFIT` | для обратной совместимости | привязать к объявлению нельзя |

#### Изображения: запрос и ответ

| Отвязка (журнал) | Прочитано | Через | `Associated` |
|---|---|---|---|
| 19:44:10 | 19:44:17 | +7 с | `YES` |
| ^ | 19:44:28 | +18 с | `YES` |
| ^ | 19:44:43 | +33 с | `YES` |
| ^ | 19:45:14 | +64 с | `NO` |
| ^ | 19:46:12 и 19:47:13 | +122 и +183 с | `NO` |
| 19:47:28 | 19:48:16 | +48 с | `NO` |
| 19:50:16 | 19:50:39 | +23 с | `NO` |

### 8.4. Видео и креативы

| Тип креатива | Для каких объявлений |
|---|---|
| `IMAGE_CREATIVE` | `IMAGE_AD` (подтипы `TEXT_AD_BUILDER_AD`, `MOBILE_APP_AD_BUILDER_AD`), `CPM_BANNER_AD` |
| `HTML5_CREATIVE` | `CPM_BANNER_AD` |
| `VIDEO_EXTENSION_CREATIVE` | `TEXT_AD` |
| `CPC_VIDEO_CREATIVE` | `CPC_VIDEO_AD` |
| `CPM_VIDEO_CREATIVE` | `CPM_VIDEO_AD` |
| `SMART_CREATIVE` | `SMART_AD` |

### 8.5. Цена в объявлении

| Поле | `PriceExtensionAddItem` | `PriceExtensionUpdateItem` |
|---|---|---|
| `Price` | Да | Нет |
| `PriceQualifier` (`FROM`, `UP_TO`, `NONE`) | Да | Нет |
| `PriceCurrency` | Да | Нет |
| `OldPrice` | Нет | Нет, nillable |

## 10. Ретаргетинг и аудитории

| Группа | Сколько условий |
|---|---|
| `TEXT_AD_GROUP` | до 50, из них с типом `AUDIENCE` — одно |
| `MOBILE_APP_AD_GROUP` | до 50 |
| `CPM_BANNER_AD_GROUP`, подтип `USER_PROFILE` | одно |
| `CPM_VIDEO_AD_GROUP` | одно |
| `CPM_BANNER_AD_GROUP`, подтип `KEYWORDS` | **добавление не допускается** |

## 12. Клиент (Client)

### 12.1. Чьё поле — кабинета или представителя

| Кому принадлежит | На чтении | На записи |
|---|---|---|
| представителю | `Login`, `ClientInfo`, `CreatedAt`, `Notification`, `Phone` | `ClientInfo`, `Notification`, `Phone` |
| рекламодателю или агентству | всё остальное | `Settings` |
| документация не относит | — | `TinInfo`, `ErirAttributes` |

### 12.2. Состав на чтение — `ClientGetItem`

| Поле | Тип | Что это | Пишется |
|---|---|---|---|
| `ClientInfo` | string | ФИО пользователя Директа, до 255 символов; наследуется | Да |
| `Phone` | string | телефон пользователя Директа, до 255 символов; наследуется | Да |
| `AccountQuality` | decimal, nillable | показатель качества аккаунта | Нет |
| `Archived` | YesNoEnum | рекламодатель или агентство в архиве | Нет |
| `ClientId` | long | идентификатор рекламодателя или агентства | Нет |
| `CountryId` | int | страна из справочника регионов | Нет |
| `CreatedAt` | string | дата регистрации пользователя, `YYYY-MM-DD` | Нет |
| `Currency` | CurrencyEnum | валюта рекламодателя | Нет |
| `Grants` | array of GrantGetItem | полномочия по управлению кампаниями | Нет |
| `Bonuses` | BonusesGet | ожидающий бонус с НДС и без, ×1 000 000; только RUB | Нет |
| `Login` | string | логин пользователя Директа | Нет |
| `Notification` | NotificationGet | SMS- и email-уведомления представителя ([12.3](#123-уведомления-читается-больше-чем-пишется)) | Частично |
| `OverdraftSumAvailable` | long | лимит овердрафта в валюте кабинета, ×1 000 000 | Нет |
| `Representatives` | array of Representative | логин, почта и роль представителей | Нет |
| `Restrictions` | array of ClientRestrictionItem | количественные ограничения кабинета, включая `API_POINTS` | Нет |
| `Settings` | array of ClientSettingGetItem | переключатели кабинета из `YES`/`NO` ([12.4](#124-настройки-кабинета--settings)) | Частично |
| `Type` | string | `CLIENT`, `SUBCLIENT` или `AGENCY` | Нет |
| `VatRate` | decimal, nillable | ставка НДС плательщика | Нет |
| `ForbiddenPlatform` | ForbiddenPlatformEnum | `SEARCH`, `NETWORK` или `NONE` | Нет |
| `AvailableCampaignTypes` | array of AvailableCampaignTypeEnum | типы кампаний, доступные логину | Нет |
| `AvailableAdGroupTypes` | array of AvailableAdGroupTypeEnum | типы групп, доступные логину; документация имени не знает | Нет |
| `Subtype` | string | Поле вне документированного набора; может возвращать `NONE` | Нет |
| `ManagedLogins` | array | Поле вне документированного набора; может возвращаться пустым | Нет |
| `ProductMode` | string | Поле вне документированного набора; может возвращать `PRO` | Нет |
| `TinInfo` | TinInfoGet | тип организации и ИНН конечного рекламодателя | Да |
| `ErirAttributes` | ErirAttributesGet | организация, договор и контрагент для маркировки | Да |

### 12.3. Уведомления: читается больше, чем пишется

| Поле `Notification` | Чтение `NotificationGet` | Запись `NotificationUpdate` |
|---|---|---|
| `Lang` | `RU`, `UK`, `EN`, `TR` | Да |
| `Email` | адрес для аккаунтных уведомлений, до 255 символов | Да |
| `EmailSubscriptions` | `RECEIVE_RECOMMENDATIONS`, `TRACK_MANAGED_CAMPAIGNS`, `TRACK_POSITION_CHANGES` | Да |
| `SmsPhoneNumber` | телефон для SMS из профиля Яндекс ID | **—** |

### 12.4. Настройки кабинета — `Settings`

| Имя | Что переключает | Чтение | Запись |
|---|---|---|---|
| `CORRECT_TYPOS_AUTOMATICALLY` | автоматически исправлять ошибки и опечатки | Да | Да |
| `DISPLAY_STORE_RATING` | дополнять объявления данными из внешних источников | Да | Да |
| `SHARED_ACCOUNT_ENABLED` | подключён общий счёт | Да | **—** |

### 12.5. Состав на запись — `ClientUpdateItem`

| Поле | Тип | Обязательное |
|---|---|---|
| `ClientInfo` | string | Нет |
| `Phone` | string | Нет |
| `Notification` | NotificationUpdate | Нет |
| `Settings` | array of ClientSettingUpdateItem | Нет |
| `TinInfo` | TinInfoUpdate, nillable | Нет |
| `ErirAttributes` | ErirAttributesUpdate, nillable | Нет |

## 13. Поля, которые пишутся и не читаются

| Вид | Что это | Примеры |
|---|---|---|
| содержимое | бинарные данные и секреты: обратно их не отдают | `AdImages.add` → `ImageData`; `AdVideos.add` → `VideoData`; `Feeds` → `FileFeed.Data`, `UrlFeed.Password` |
| указание | не поле объекта, а команда над ним | `Campaigns.update` → `…PriorityGoals.Items.Operation`; `Ads.update` → `…CalloutSetting.AdExtensions.Operation`; `Bids.setAuto` → `Scope`, `Position`, `CalculateBy`, `IncreasePercent`, `MaxBid`; `KeywordBids.setAuto` → `BiddingRule`; `Campaigns` → `…PackageBiddingStrategy.StrategyFromCampaignId` |
| поле создания без чтения | структура `get` этих полей не содержит вовсе | `AdVideos.add` → `Url`, `Name` (чтение отдаёт только `Id` и `Status`); `AgencyClients.add` → `Email`, `Lang`, `Privilege`, `Tin`, `TinType`, `EmailSubscriptions`; `Creatives.add` → `VideoExtensionCreative.VideoId`; `Ads` → `SmartAdBuilderAd.LogoExtensionHash` |

## Дополнительные особенности

**Уточнения.** `AdExtensions` умеет добавлять, читать и удалять уточнения; редактирование текста требует нового объекта и перепривязки. Длина одного уточнения — до 25 символов, к объявлению привязывается до 50. Общая длина показанных уточнений ограничена 132 символами на компьютерах и 76 на телефонах; порядок может меняться. См. [справку об уточнениях](https://yandex.ru/support/direct/ru/efficiency/callout).

**Корректировки ставок.** `BidModifiers.add/set/delete/get` работает на уровне кампании или группы. `DESKTOP_ADJUSTMENT` несовместима с `TABLET_ADJUSTMENT` и `DESKTOP_ONLY_ADJUSTMENT`; региональные корректировки задаются на кампании, медийные — на группе. Ставка умножается на `BidModifier / 100`. Диапазоны и совместимость проверяются до записи. Приоритет `StrategyPriority` у ключевых фраз может игнорироваться; не используйте его как замену ставке.

**Фиды.** `Feeds` поддерживает `add/update/delete/get`. `ShoppingAd` и `ListingAd` относятся к `UNIFIED_AD_GROUP`; в группе допускается по одному неархивному объявлению каждого вида. `FeedId` задаётся при создании, для смены фида требуется пересоздание. `FeedFilterConditions` использует операторы `CONTAINS_ANY`, `EQUALS_ANY`, `EXISTS`, `GREATER_THAN`, `IN_RANGE`, `LESS_THAN`, `NOT_CONTAINS_ALL`; ограничения значений зависят от поля фида.

**Проверка записи.** Некоторые значения не возвращаются в ответе: содержимое загруженного файла, секрет фида или команда `Operation`. Ищите доступный результат действия: хеш изображения, итоговый набор уточнений, идентификатор стратегии. Поля с другим именем чтения всё равно проверяются: `AdImageHashes → AdImages`, `VideoExtensionIds → VideoExtensions`, `AdExtensionIds → AdExtensions`, `SearchBid → Search.Bid`. Если результат проверить невозможно, назовите конкретное непроверенное поле пользователю.

Актуальные определения: [справочник API Директа](https://yandex.ru/dev/direct/doc/ru/), [метод Ads.update](https://yandex.ru/dev/direct/doc/ru/ads/update).
