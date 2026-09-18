"""Состав чтения групп, объявлений и фраз: перечни полей и разбор ответа."""

from __future__ import annotations

import money
from config import DirectFailure, excerpt

SERVICE_GROUPS = "adgroups"
SERVICE_ADS = "ads"
SERVICE_KEYWORDS = "keywords"

# --------------------------------------------------------------------------
# Группы
# --------------------------------------------------------------------------

# Общие поля группы. Перечень спрошен у Директа (замер 01.09.2026) и принят им
# целиком. `ServingStatus` здесь — это и есть «мало показов»: `RARELY_SERVED`
# означает, что группа почти не показывается, и увидеть это больше негде.
GROUP_FIELDS = (
    "Id", "CampaignId", "Name", "Type", "Subtype", "Status", "ServingStatus",
    "RegionIds", "RestrictedRegionIds", "NegativeKeywords",
    "NegativeKeywordSharedSetIds", "TrackingParams",
)

# Наборы полей типовых структур группы. Отправляются все сразу, как у кампаний:
# тип группы выясняется из ответа, а набор нужен до того, как ответ получен.
GROUP_TYPE_FIELDS = {
    "UnifiedAdGroupFieldNames": (
        "OfferRetargeting", "Scenario", "CurrentAudienceRetargetingConditionId",
    ),
    "MobileAppAdGroupFieldNames": (
        "StoreUrl", "TargetDeviceType", "TargetCarrier",
        "TargetOperatingSystemVersion", "AppIconModeration",
        "AppAvailabilityStatus", "AppOperatingSystemType",
    ),
    "DynamicTextAdGroupFieldNames": (
        "AutotargetingCategories", "AutotargetingSettings", "DomainUrl",
        "DomainUrlProcessingStatus",
    ),
    "DynamicTextFeedAdGroupFieldNames": (
        "AutotargetingCategories", "AutotargetingSettings", "Source", "FeedId",
        "AdTitleSource", "AdBodySource", "SourceType", "SourceProcessingStatus",
    ),
    "SmartAdGroupFieldNames": (
        "FeedId", "AdTitleSource", "AdBodySource", "LogoExtensionHash",
        "LogoExtensionModeration",
    ),
    "TextAdGroupFeedParamsFieldNames": ("FeedId", "FeedCategoryIds"),
}

# Тип и подтип группы — имя структуры в ответе. Подтип различает две структуры
# динамических объявлений: `WEBPAGE` описывается доменом, `FEED` — фидом.
# Пустая строка означает, что структуры на чтение у типа нет вовсе: у медийных
# групп она задаётся при создании пустым объектом и обратно не читается —
# `CpmBannerKeywordsAdGroupFieldNames` и два его соседа Директ не знает как
# параметр вовсе («Указан неизвестный параметр», замер 01.09.2026).
GROUP_TYPE_BODY = {
    ("UNIFIED_AD_GROUP", ""): "UnifiedAdGroup",
    ("TEXT_AD_GROUP", ""): "TextAdGroupFeedParams",
    ("MOBILE_APP_AD_GROUP", ""): "MobileAppAdGroup",
    ("DYNAMIC_TEXT_AD_GROUP", "WEBPAGE"): "DynamicTextAdGroup",
    ("DYNAMIC_TEXT_AD_GROUP", "FEED"): "DynamicTextFeedAdGroup",
    ("SMART_AD_GROUP", ""): "SmartAdGroup",
    ("CPM_BANNER_AD_GROUP", ""): "",
    ("CPM_VIDEO_AD_GROUP", ""): "",
}

GROUP_TYPE_RU = {
    "UNIFIED_AD_GROUP": "группа ЕПК",
    "TEXT_AD_GROUP": "текстово-графическая",
    "MOBILE_APP_AD_GROUP": "реклама приложения",
    "DYNAMIC_TEXT_AD_GROUP": "динамические объявления",
    "SMART_AD_GROUP": "смарт-баннеры",
    "CPM_BANNER_AD_GROUP": "медийная",
    "CPM_VIDEO_AD_GROUP": "медийное видео",
    "UNKNOWN": "тип вне API",
}

GROUP_STATUS_RU = {
    "DRAFT": "черновик",
    "MODERATION": "на модерации",
    "PREACCEPTED": "допущена автоматически",
    "ACCEPTED": "принята модерацией",
    "REJECTED": "отклонена модерацией",
    "UNKNOWN": "статус вне API",
}

# `RARELY_SERVED` — это и есть «мало показов» из интерфейса.
SERVING_RU = {
    "ELIGIBLE": "показывается",
    "RARELY_SERVED": "мало показов",
    "UNKNOWN": "состояние показов вне API",
}

GROUP_STATUSES = tuple(name for name in GROUP_STATUS_RU if name != "UNKNOWN")
SERVING_STATUSES = ("ELIGIBLE", "RARELY_SERVED")

# --------------------------------------------------------------------------
# Объявления
# --------------------------------------------------------------------------

# Общие поля объявления — весь перечень `AdGetItem`, принятый Директом целиком.
AD_FIELDS = (
    "Id", "CampaignId", "AdGroupId", "Type", "Subtype", "State", "Status",
    "StatusClarification", "AdCategories", "AgeLabel",
)

# Наборы полей структур объявления. Все сразу и всегда — см. док-строку модуля.
AD_TYPE_FIELDS = {
    "ResponsiveAdFieldNames": (
        "Titles", "Texts", "AdImages", "VideoExtensions", "Carousel", "Href",
        "DisplayDomain", "DisplayUrlPath", "DisplayUrlPathModeration",
        "SitelinkSetId", "SitelinksModeration", "AdExtensions", "PriceExtension",
        "ButtonExtension", "ButtonExtensionModeration", "BusinessId",
        "TrackingPhoneId", "ErirAdDescription", "FinalUrl", "DutPrefix",
        "DutSuffix",
    ),
    "TextAdFieldNames": (
        "Title", "Title2", "Text", "AdImageHash", "AdImageModeration",
        "LogoExtensionHash", "LogoExtensionModeration", "DisplayDomain",
        "DisplayUrlPath", "DisplayUrlPathModeration", "Href", "SitelinkSetId",
        "SitelinksModeration", "AdExtensions", "VideoExtension", "VCardId",
        "VCardModeration", "TurboPageId", "TurboPageModeration",
        "ButtonExtension", "ButtonExtensionModeration", "BusinessId",
        "TrackingPhoneId", "Mobile", "PreferVCardOverBusiness",
        "ErirAdDescription", "AutogeneratedErirAdDescription", "FinalUrl",
        "DutPrefix", "DutSuffix",
    ),
    "TextAdPriceExtensionFieldNames": (
        "Price", "OldPrice", "PriceCurrency", "PriceQualifier",
    ),
    "TextImageAdFieldNames": (
        "AdImageHash", "LogoExtensionHash", "LogoExtensionModeration", "Href",
        "TurboPageId", "TurboPageModeration", "ButtonExtension",
        "ButtonExtensionModeration", "Title", "Title2", "Text",
        "ErirAdDescription", "AutogeneratedErirAdDescription", "FinalUrl",
    ),
    "TextAdBuilderAdFieldNames": (
        "Creative", "LogoExtensionHash", "LogoExtensionModeration", "Href",
        "TurboPageId", "TurboPageModeration", "ButtonExtension",
        "ButtonExtensionModeration", "Title", "Title2", "Text", "Carousel",
        "ErirAdDescription", "AutogeneratedErirAdDescription", "FinalUrl",
    ),
    "ShoppingAdFieldNames": (
        "FeedId", "FeedFilterConditions", "FeedProcessingStatus", "TitleSources",
        "TextSources", "DefaultTexts", "GenerationScopes",
        "ListingFeedFilterConditions", "ListingTitleSources",
        "ListingTextSources", "SitelinkSetId", "SitelinksModeration",
        "AdExtensions", "BusinessId", "TrackingPhoneId",
    ),
    "ListingAdFieldNames": (
        "FeedId", "FeedFilterConditions", "FeedProcessingStatus", "TitleSources",
        "TextSources", "DefaultTexts", "SitelinkSetId", "SitelinksModeration",
        "AdExtensions", "BusinessId", "TrackingPhoneId", "ButtonExtension",
        "ButtonExtensionModeration",
    ),
    "MobileAppAdFieldNames": (
        "Title", "Text", "AdImageHash", "AdImageModeration", "Features",
        "Action", "TrackingUrl", "ImpressionUrl", "VideoExtension",
        "ErirAdDescription", "AutogeneratedErirAdDescription",
    ),
    "MobileAppImageAdFieldNames": (
        "AdImageHash", "TrackingUrl", "ErirAdDescription",
        "AutogeneratedErirAdDescription",
    ),
    "MobileAppAdBuilderAdFieldNames": (
        "Creative", "TrackingUrl", "ErirAdDescription",
        "AutogeneratedErirAdDescription",
    ),
    "DynamicTextAdFieldNames": (
        "Text", "AdImageHash", "AdImageModeration", "SitelinkSetId",
        "SitelinksModeration", "VCardId", "VCardModeration", "AdExtensions",
    ),
    "CpcVideoAdBuilderAdFieldNames": (
        "Creative", "Href", "TurboPageId", "TurboPageModeration",
        "ErirAdDescription", "AutogeneratedErirAdDescription",
    ),
    "MobileAppCpcVideoAdBuilderAdFieldNames": (
        "Creative", "TrackingUrl", "ErirAdDescription",
        "AutogeneratedErirAdDescription",
    ),
    "CpmBannerAdBuilderAdFieldNames": (
        "Creative", "Href", "TrackingPixels", "TurboPageId",
        "TurboPageModeration", "TnsId", "ErirAdDescription",
        "AutogeneratedErirAdDescription",
    ),
    "CpmVideoAdBuilderAdFieldNames": (
        "Creative", "Href", "TrackingPixels", "TurboPageId",
        "TurboPageModeration", "TnsId", "LogoExtensionHash",
        "LogoExtensionModeration", "ButtonExtension", "ButtonExtensionModeration",
        "Title", "Title2", "Text", "ErirAdDescription",
        "AutogeneratedErirAdDescription",
    ),
    "SmartAdBuilderAdFieldNames": (
        "Creative", "LogoExtensionHash", "LogoExtensionModeration",
    ),
}

# Имя структуры комбинаторного объявления и те её поля, без которых чтение
# теряет комплект. Отдельным перечнем, а не «весь набор целиком»: сторож обязан
# ловить потерю комплекта, а не расхождение в необязательном поле, и разница
# видна ровно на этих четырёх именах — заголовки, тексты, изображения, видео.
RESPONSIVE = "ResponsiveAd"
RESPONSIVE_REQUIRED = ("Titles", "Texts", "AdImages", "VideoExtensions")

# Структуры, у которых комплект вообще бывает. У комбинаторного объявления он
# есть, у текстово-графического — будет: автоконвертация превращает одно в
# другое в случайно выбранный день. У графического, товарного, медийного и
# прочих комплекта нет вовсе, и «недозаполненный комплект» про них — не
# наблюдение, а бессмыслица: семи заголовков там не бывает по устройству.
KIT_STRUCTURES = (RESPONSIVE, "TextAd")

REJECTED_BY_GET = {
    "ResponsiveAdFieldNames": {"TrackingParams": 4000},
    "TextAdFieldNames": {"TrackingParams": 4000, "Carousel": 4000,
                         "LfHref": 5006, "LfButtonText": 5006},
    "TextImageAdFieldNames": {"TrackingParams": 4000},
    "TextAdBuilderAdFieldNames": {"TrackingParams": 4000},
    "ShoppingAdFieldNames": {"BodySources": 8000, "DefaultBodies": 8000},
    "UnifiedAdGroupFieldNames": {"PromotionExtension": 5006},
}

# Тип и подтип объявления — имя структуры (`API_OBJECTS.md`, раздел 4.1).
# Служит подписью человеку и проверкой «структура типа в ответе есть», но не
# разбором: разбирается то, что пришло, — см. `bodies_of`.
AD_TYPE_BODY = {
    ("RESPONSIVE_AD", ""): "ResponsiveAd",
    ("SHOPPING_AD", ""): "ShoppingAd",
    ("LISTING_AD", ""): "ListingAd",
    ("TEXT_AD", ""): "TextAd",
    ("MOBILE_APP_AD", ""): "MobileAppAd",
    ("DYNAMIC_TEXT_AD", ""): "DynamicTextAd",
    ("SMART_AD", ""): "SmartAdBuilderAd",
    ("IMAGE_AD", "TEXT_IMAGE_AD"): "TextImageAd",
    ("IMAGE_AD", "TEXT_AD_BUILDER_AD"): "TextAdBuilderAd",
    ("IMAGE_AD", "MOBILE_APP_IMAGE_AD"): "MobileAppImageAd",
    ("IMAGE_AD", "MOBILE_APP_AD_BUILDER_AD"): "MobileAppAdBuilderAd",
    ("CPC_VIDEO_AD", ""): "CpcVideoAdBuilderAd",
    ("CPC_VIDEO_AD", "MOBILE_APP_CPC_VIDEO_AD_BUILDER_AD"):
        "MobileAppCpcVideoAdBuilderAd",
    ("CPM_BANNER_AD", ""): "CpmBannerAdBuilderAd",
    ("CPM_VIDEO_AD", ""): "CpmVideoAdBuilderAd",
}

AD_TYPE_RU = {
    "RESPONSIVE_AD": "комбинаторное",
    "TEXT_AD": "текстово-графическое",
    "SHOPPING_AD": "товарное",
    "LISTING_AD": "страница каталога",
    "MOBILE_APP_AD": "реклама приложения",
    "DYNAMIC_TEXT_AD": "динамическое",
    "SMART_AD": "смарт-баннер",
    "IMAGE_AD": "графическое",
    "CPC_VIDEO_AD": "видеообъявление",
    "CPM_BANNER_AD": "медийный баннер",
    "CPM_VIDEO_AD": "медийное видео",
    "UNKNOWN": "тип вне API",
}

AD_STATE_RU = {
    "ON": "показывается",
    "OFF": "выключено",
    "SUSPENDED": "остановлено",
    "OFF_BY_MONITORING": "остановлено мониторингом сайта",
    "ARCHIVED": "в архиве",
    "UNKNOWN": "состояние вне API",
}

AD_STATUS_RU = {
    "DRAFT": "черновик",
    "MODERATION": "на модерации",
    "PREACCEPTED": "допущено автоматически",
    "ACCEPTED": "принято модерацией",
    "REJECTED": "отклонено модерацией",
    "UNKNOWN": "статус вне API",
}

AD_STATES = tuple(name for name in AD_STATE_RU if name != "UNKNOWN")
AD_STATUSES = tuple(name for name in AD_STATUS_RU if name != "UNKNOWN")

# Значения `Status` **объявления**, при которых вердикт модерации уже пришёл.
# Только при них поэлементный статус можно читать как решение модератора.
AD_VERDICT_STATUSES = ("ACCEPTED", "REJECTED")

# Почему вердикта нет — по значению `Status` объявления. Остальные четыре
# значения перечня и есть весь набор без вердикта, и названы они поимённо: счёт
# «всё, кроме черновика» неверен ровно так же, как исходная ошибка, только в
# другую сторону. `AD_CONTENT.md`, раздел 10, и `API_OBJECTS.md`, раздел 4.3.
#
# `UNKNOWN` здесь не для полноты перечня: он приходит **только на чтение**, то
# есть ровно там, где поэлементный статус и читают.
NO_VERDICT_RU = {
    "DRAFT": "на модерацию не отправлялось",
    "MODERATION": "отправлено, ответа ещё нет",
    "PREACCEPTED": "допущено автоматически, проверки модератором ещё не было",
    "UNKNOWN": "статус объявления вне API",
}

# Статуса объявления в ответе нет вовсе — например, `FieldNames` его не
# спросил. Это тоже отсутствие вердикта, а не его наличие: недоказанное
# краснеет, и молчаливое «принято» здесь стоило бы дороже лишней оговорки.
VERDICT_UNREAD = "статус объявления не прочитан"

# Где в структуре лежат части комплекта: имя массива чтения, имя поля значения
# и одиночные поля той же роли. Разбор идёт по именам полей, а не по типу
# объявления: `Type` — состояние, которое Директ меняет сам.
KIT_PARTS = {
    "titles": {"array": "Titles", "value": "Title", "scalars": ("Title", "Title2")},
    "texts": {"array": "Texts", "value": "Text",
              "scalars": ("Text",), "lists": ("DefaultTexts",)},
    "images": {"array": "AdImages", "value": "ImageHash",
               "scalars": ("AdImageHash",)},
    "videos": {"array": "VideoExtensions", "value": "CreativeId",
               "scalars": ("VideoExtension",)},
    "carousel": {"array": "Carousel", "value": "ImageHash"},
}

KIT_LIMITS = {
    "titles": "ResponsiveAd.Titles",
    "texts": "ResponsiveAd.Texts",
    "images": "ResponsiveAd.AdImageHashes",
    "videos": "ResponsiveAd.VideoExtensionIds",
}

# Статус элемента, при котором показывать его нечем. `UNKNOWN` сюда не входит:
# это «статус вне API», а не отказ.
REJECTED = "REJECTED"

# --------------------------------------------------------------------------
# Фразы
# --------------------------------------------------------------------------

KEYWORD_FIELDS = (
    "Id", "Keyword", "AdGroupId", "CampaignId", "State", "Status",
    "ServingStatus", "Bid", "ContextBid", "AutotargetingSearchBidIsAuto",
    "StrategyPriority", "UserParam1", "UserParam2", "AutotargetingCategories",
    "AutotargetingBrandOptions",
)

KEYWORD_STATE_RU = {
    "ON": "показывается",
    "OFF": "не прошла модерацию",
    "SUSPENDED": "остановлена",
    "UNKNOWN": "состояние вне API",
}

KEYWORD_STATUS_RU = {
    "DRAFT": "черновик",
    "ACCEPTED": "принята модерацией",
    "REJECTED": "отклонена модерацией",
    "UNKNOWN": "статус вне API",
}

FLAT_TO_NESTED = {
    "EXACT": ("Categories", "Exact"),
    "ALTERNATIVE": ("Categories", "Alternative"),
    "BROADER": ("Categories", "Broader"),
    "ACCESSORY": ("Categories", "Accessory"),
    "COMPETITOR": ("BrandOptions", "WithCompetitorsBrand"),
    "WITHOUT_BRANDS": ("BrandOptions", "WithoutBrands"),
    "WITH_ADVERTISER_BRAND": ("BrandOptions", "WithAdvertiserBrand"),
}

ONLY_NESTED = ("Categories", "Narrow")

CATEGORY_RU = {
    "Exact": "точные",
    "Narrow": "узкие",
    "Alternative": "альтернативные",
    "Accessory": "сопутствующие",
    "Broader": "широкие",
}

BRAND_RU = {
    "WithoutBrands": "без брендов",
    "WithAdvertiserBrand": "свой бренд",
    "WithCompetitorsBrand": "бренды конкурентов",
}

KEYWORD_STATES = tuple(name for name in KEYWORD_STATE_RU if name != "UNKNOWN")
KEYWORD_STATUSES = tuple(name for name in KEYWORD_STATUS_RU if name != "UNKNOWN")

# Текст, которым Директ обозначает автотаргетинг в сервисе `Keywords`.
AUTOTARGETING = "---autotargeting"


def _check_rejected() -> None:
    """Ни одно отвергаемое имя не должно попасть в запрос."""
    asked = dict(AD_TYPE_FIELDS)
    asked.update(GROUP_TYPE_FIELDS)
    for name, refused in REJECTED_BY_GET.items():
        overlap = sorted(set(asked.get(name, ())) & set(refused))
        if overlap:
            raise DirectFailure(
                f"В набор {name} попали имена, которые `get` отвергает: "
                f"{', '.join(overlap)}. Один такой элемент роняет весь вызов."
            )


_check_rejected()


# --------------------------------------------------------------------------
# Разбор ответа
# --------------------------------------------------------------------------

def items_of(value) -> list:
    """Массив Директа: либо `{"Items": [...]}`, либо голый список, либо ничего.

    Обе формы встречаются в одном ответе: `NegativeKeywords` группы приходит
    объектом с `Items`, `AdExtensions` объявления — голым списком. Разбор,
    знающий одну форму, молча вернул бы пустоту на другой."""
    if isinstance(value, dict):
        value = value.get("Items")
    return list(value) if isinstance(value, list) else []


def identifiers(value, what: str) -> list:
    """Перечень идентификаторов через запятую — тем же разбором, что и файлы.

    Разбор берётся оттуда же, откуда его берут брифы и `ads_generate.py`:
    иначе вердикт зависел бы от того, набран идентификатор в командной строке
    или записан в файле. Своя копия правила разошлась бы с чужой молча — и
    разошлась бы в трёх командах разом.

    Отрицательное отвергается здесь, а не там: `whole_number` знает знак,
    потому что читает и координаты, и коэффициенты, а идентификатор объекта
    Директа положителен. Минус в этом поле — не «объект номер минус один», а
    команда исключения из другого места (`RegionIds`), и принять её значило бы
    прочитать не тот объект."""
    from incoming import whole_number

    found = []
    for item in str(value).split(","):
        text = item.strip()
        if not text:
            continue
        number = whole_number(text, f"Значение {what}")
        if number <= 0:
            raise DirectFailure(
                f"{what}: «{excerpt(text, 32)}» — не идентификатор. "
                f"Идентификатор объекта Директа положителен."
            )
        found.append(number)
    if not found:
        raise DirectFailure(f"{what}: перечень пуст")
    return found


# Как аргумент команды называется в `SelectionCriteria` каждого сервиса. Таблица
# нужна затем, чтобы предел можно было проверить **до** сборки запроса: у групп
# идентификатор группы уходит в `Ids`, у объявлений и фраз — в `AdGroupIds`, и
# без таблицы каждая команда переводила бы имена сама.
CRITERIA = {
    SERVICE_GROUPS: {"campaign_ids": "CampaignIds", "group_ids": "Ids"},
    SERVICE_ADS: {"campaign_ids": "CampaignIds", "group_ids": "AdGroupIds",
                  "ad_ids": "Ids"},
    SERVICE_KEYWORDS: {"campaign_ids": "CampaignIds", "group_ids": "AdGroupIds",
                       "keyword_ids": "Ids"},
}


def selection(service: str, **named) -> dict:
    """Критерий отбора из аргументов команды: только непустое.

    Пустой перечень отбором не является и в критерий не попадает — иначе
    `SelectionCriteria` с пустым массивом уходил бы в Директ, а тот отвечает на
    него отказом за баллы."""
    table = CRITERIA[service]
    unknown = sorted(set(named) - set(table))
    if unknown:
        raise DirectFailure(
            f"У сервиса {service} нет отбора «{', '.join(unknown)}»: "
            f"допустимо {', '.join(sorted(table))}."
        )
    return {table[name]: [int(one) for one in values]
            for name, values in named.items() if values}


def fits_selection(service: str, criteria: dict, limits=None) -> None:
    """Влезает ли отбор в предел метода — по справочнику, а не по догадке.

    Предел здесь машинно проверяемый, значит проверяет его код, а не агент: у `Ads.get.AdGroupIds` он тысяча, у `…Ids` — десять тысяч, у
    `…CampaignIds` — десять. Захотеть обратного нельзя: Директ такой запрос не
    примет ни при каких обстоятельствах.

    Проверять надо **до** отправки, и до самого первого вызова, а не до этого.
    Перечень идентификаторов человек называет сам, и негодным он остаётся
    независимо от того, нашлась ли кампания: поиск кампании, список кабинетов и
    справочник регионов стоят баллов и уходят раньше сборки запроса. Отсюда
    вторая точка вызова — в `main` каждой команды, по названному человеком, ещё
    до клиента.

    Отказ Директа приходит на весь вызов и уже за баллы, а текст его говорит о
    значении поля, а не о числе элементов: человек слышит «неверное значение
    перечисления» там, где у него на одну группу больше предела."""
    if limits is None:
        from writer import Limits

        limits = Limits.load()
    for criterion, values in sorted(criteria.items()):
        top = limits.selection(service, criterion)
        if not isinstance(top, int) or isinstance(top, bool) or top < 1:
            raise DirectFailure(
                f"В справочнике лимитов нет предела отбора "
                f"`{service}.get.{criterion}`. Без него выборка уходила бы "
                f"вслепую, а отказ приходил бы от Директа уже за баллы."
            )
        if len(values) > top:
            raise DirectFailure(
                f"В отборе {criterion} {len(values)} значений при пределе "
                f"{top} (справочник лимитов, `selection`). Директ отвергает "
                f"такой запрос целиком и берёт за отказ баллы; разбейте "
                f"выборку на части."
            )


def group_params(campaign_ids=None, group_ids=None) -> dict:
    """Тело запроса `AdGroups.get`: все наборы полей сразу.

    Отбор обязателен: без `SelectionCriteria` метод не отвечает. Кампаний в
    одном вызове не больше десяти (`limits.json`, `selection`), и следит за
    этим вызывающий код — здесь собирается тело, а не пачка вызовов."""
    criteria = selection(SERVICE_GROUPS, campaign_ids=campaign_ids,
                         group_ids=group_ids)
    if not criteria:
        raise DirectFailure(
            "Чтение групп без отбора: назовите кампанию или группу. "
            "`AdGroups.get` без `SelectionCriteria` не отвечает."
        )
    fits_selection(SERVICE_GROUPS, criteria)
    params = {"SelectionCriteria": criteria, "FieldNames": list(GROUP_FIELDS)}
    params.update({name: list(fields)
                   for name, fields in GROUP_TYPE_FIELDS.items()})
    return params


def ads_params(campaign_ids=None, group_ids=None, ad_ids=None) -> dict:
    """Тело запроса `Ads.get`: все наборы полей всех структур сразу.

    `ResponsiveAdFieldNames` здесь безусловен — см. док-строку модуля. Условия
    вокруг него не бывает: тип объявления Директ меняет сам."""
    criteria = selection(SERVICE_ADS, campaign_ids=campaign_ids,
                         group_ids=group_ids, ad_ids=ad_ids)
    if not criteria:
        raise DirectFailure(
            "Чтение объявлений без отбора: назовите кампанию, группу или "
            "объявление. `Ads.get` без `SelectionCriteria` не отвечает."
        )
    fits_selection(SERVICE_ADS, criteria)
    params = {"SelectionCriteria": criteria, "FieldNames": list(AD_FIELDS)}
    params.update({name: list(fields)
                   for name, fields in AD_TYPE_FIELDS.items()})
    return params


def keyword_params(campaign_ids=None, group_ids=None, keyword_ids=None) -> dict:
    """Тело запроса `Keywords.get`: фразы, автотаргетинг и обе его формы."""
    criteria = selection(SERVICE_KEYWORDS, campaign_ids=campaign_ids,
                         group_ids=group_ids, keyword_ids=keyword_ids)
    if not criteria:
        raise DirectFailure(
            "Чтение фраз без отбора: назовите кампанию, группу или фразу. "
            "`Keywords.get` без `SelectionCriteria` не отвечает."
        )
    fits_selection(SERVICE_KEYWORDS, criteria)
    categories, brands = _autotargeting_fields()
    return {
        "SelectionCriteria": criteria,
        "FieldNames": list(KEYWORD_FIELDS),
        "AutotargetingSettingsCategoriesFieldNames": list(categories),
        "AutotargetingSettingsBrandOptionsFieldNames": list(brands),
    }


def _autotargeting_fields() -> tuple:
    """Половины вложенной формы — те же, что пишет `phrases`.

    Ввозится по требованию: `phrases` тянет за собой движок записи, а команде
    чтения он не нужен до самого запроса. Своей копии перечня здесь нет
    намеренно — разойдясь, две копии читали бы и писали разные настройки."""
    from phrases import BRAND_OPTIONS, CATEGORIES

    return CATEGORIES, BRAND_OPTIONS


# --------------------------------------------------------------------------
# Группы: разбор
# --------------------------------------------------------------------------

def group_body(record: dict) -> tuple:
    """Типовая структура группы: пара «имя, содержимое».

    Пустое имя означает, что структуры у типа нет вовсе, а пустое содержимое
    при непустом имени — что её не прислали. Различать обязательно: у
    текстово-графической группы без фида `TextAdGroupFeedParams` законно
    отсутствует, а у динамической отсутствие домена или фида — это неполный
    ответ."""
    kind = str(record.get("Type") or "")
    subtype = str(record.get("Subtype") or "")
    name = GROUP_TYPE_BODY.get((kind, subtype))
    if name is None:
        name = GROUP_TYPE_BODY.get((kind, ""), "")
    body = record.get(name) if name else None
    return name or "", body if isinstance(body, dict) else {}


def regions_of(record: dict) -> dict:
    """Регионы группы: включённые, выключенные и «везде».

    Ноль и минус — не идентификаторы, а команды: `0` означает все регионы, а
    минус перед идентификатором **выключает** регион (`API_OBJECTS.md`,
    раздел 3.2). Печатать их числом значит показать человеку `0, -213` вместо
    настройки, которая у него в интерфейсе выглядит понятной строкой."""
    everywhere, included, excluded = False, [], []
    for region in items_of(record.get("RegionIds")):
        # Логическое значение отсекается до целого: `True` — это не регион
        # номер один, а чужое значение в поле регионов.
        if isinstance(region, bool) or not isinstance(region, int):
            continue
        if region == 0:
            everywhere = True
        elif region < 0 and -region not in excluded:
            excluded.append(-region)
        elif region > 0 and region not in included:
            included.append(region)
    restricted = [one for one in items_of(record.get("RestrictedRegionIds"))
                  if isinstance(one, int) and not isinstance(one, bool)]
    return {"everywhere": everywhere, "included": sorted(included),
            "excluded": sorted(excluded), "restricted": sorted(restricted)}


def region_ids(records) -> list:
    """Какие идентификаторы спросить у справочника регионов.

    Только положительные — и у включённых регионов, и у выключенных: имя нужно
    и тем, и другим, а знак `Dictionaries.getGeoRegions` отвергает кодом 5005
    на **весь** вызов, то есть одна исключённая область оставила бы без имён и
    все остальные регионы."""
    asked = set()
    for record in records:
        found = regions_of(record)
        asked.update(found["included"])
        asked.update(found["excluded"])
        asked.update(found["restricted"])
    return sorted(asked)


def group_row(record: dict) -> dict:
    """Плоская строка группы: то, что уходит в TSV, в `--csv` и в `--json`.

    Плоская намеренно: по индексу ищут через `grep`, а вложенный объект в
    ячейке TSV ищется только целиком."""
    name, body = group_body(record)
    regions = regions_of(record)
    serving = str(record.get("ServingStatus") or "")
    return {
        "id": record.get("Id"),
        "campaign_id": record.get("CampaignId"),
        "name": record.get("Name") or "",
        "type": record.get("Type") or "",
        "type_ru": GROUP_TYPE_RU.get(record.get("Type"), record.get("Type") or ""),
        "subtype": record.get("Subtype") or "",
        "status": record.get("Status") or "",
        "status_ru": GROUP_STATUS_RU.get(record.get("Status"),
                                         record.get("Status") or ""),
        "serving_status": serving,
        "serving_ru": SERVING_RU.get(serving, serving),
        "rarely_served": serving == "RARELY_SERVED",
        "regions_everywhere": regions["everywhere"],
        "regions_included": len(regions["included"]),
        "regions_excluded": len(regions["excluded"]),
        "regions_restricted": len(regions["restricted"]),
        "negative_keywords": len(items_of(record.get("NegativeKeywords"))),
        "shared_sets": len(items_of(record.get("NegativeKeywordSharedSetIds"))),
        # Пустая строка и отсутствие поля — разные вещи: Директ отдаёт `""` у
        # группы, где параметры отслеживания не заданы, и `null` там, где поля
        # нет. Строка индекса различать их не обязана, а `--json` обязан, и там
        # лежит сырой ответ.
        "tracking_params": record.get("TrackingParams") or "",
        "structure": name,
        "structure_read": bool(body) if name else None,
    }


GROUP_COLUMNS = [
    "id", "campaign_id", "name", "type", "subtype", "status", "serving_status",
    "rarely_served", "regions_everywhere", "regions_included",
    "regions_excluded", "regions_restricted", "negative_keywords",
    "shared_sets", "tracking_params", "structure",
]


# --------------------------------------------------------------------------
# Объявления: разбор
# --------------------------------------------------------------------------

def bodies_of(record: dict) -> dict:
    """Все типовые структуры, пришедшие в ответе: имя → содержимое.

    Все, а не одна по типу. Живой ответ отдаёт объявлению `RESPONSIVE_AD`
    вдобавок `TextAdBuilderAd` с видеокреативом (замер 01.09.2026), и разбор,
    берущий структуру по `Type`, потерял бы то, что Директ прислал."""
    known = {name[:-len("FieldNames")] for name in AD_TYPE_FIELDS}
    known.discard("TextAdPriceExtension")
    return {name: record[name] for name in sorted(known)
            if isinstance(record.get(name), dict)}


def expected_body(record: dict) -> str:
    """Имя структуры, которую тип объявления обещает. Пустая строка — тип вне API."""
    kind = str(record.get("Type") or "")
    subtype = str(record.get("Subtype") or "")
    name = AD_TYPE_BODY.get((kind, subtype))
    if name is None:
        name = AD_TYPE_BODY.get((kind, ""), "")
    return name or ""


def moderation_verdict(record: dict) -> tuple:
    """Пришёл ли по объявлению вердикт модерации и — если нет — почему.

    Три вопроса про модерацию разные, и путать их нельзя: что стоит в поле
    элемента, отправлялось ли объявление и **был ли вердикт**. Отвечает здесь
    третий, и отвечает по `Status` объявления, а не по признаку «черновик или
    нет»: элементный `ACCEPTED` не значит ничего на четырёх значениях из шести.

    Про **взгляд** человека `Status` не говорит ни при каком значении — при
    `MODERATION` проверка может идти прямо сейчас, — поэтому вопрос ставится
    про ответ, а не про модератора."""
    status = str(record.get("Status") or "")
    if status in AD_VERDICT_STATUSES:
        return True, ""
    return False, NO_VERDICT_RU.get(status) or VERDICT_UNREAD


def _element(value, status, clarification, where: str, verdict: bool) -> dict:
    """Один элемент комплекта — со своим статусом и с ценой этого статуса.

    Готовая формулировка кладётся сюда, а не собирается у каждого читателя:
    словарь `AD_STATUS_RU` переводит **значение поля**, ничего не зная об
    объявлении, и «принято модерацией» верно только там, где вердикт есть.
    Оставь перевод читателям — и правило встанет на один путь показа из
    нескольких, а обойдётся вторым.

    Умолчания у `verdict` нет намеренно. Безопасное умолчание тут есть —
    «вердикта нет» ничего не утверждает, — но забытый аргумент оно превращает
    в тихую неправду наоборот: у отмодерированного объявления показ снял бы
    верный вердикт, и набор бы этого не заметил. Без умолчания забытый вызов
    падает на месте."""
    status = status or ""
    if verdict:
        said = AD_STATUS_RU.get(status, status or "—")
    else:
        said = f"{status}, вердикта нет" if status else "—"
    return {"value": value, "status": status,
            "clarification": clarification or "", "where": where,
            "verdict": verdict, "status_ru": said}


def _part(bodies: dict, part: str, verdict: bool) -> list:
    """Одна часть комплекта из всех пришедших структур.

    Сперва массив чтения — он есть у комбинаторного объявления и несёт
    поэлементный статус модерации; затем одиночные поля той же роли, которыми
    ту же вещь описывают остальные структуры."""
    rule = KIT_PARTS[part]
    found = []
    for name, body in bodies.items():
        array = rule.get("array")
        for number, item in enumerate(items_of(body.get(array)) if array else []):
            if not isinstance(item, dict):
                continue
            value = item.get(rule["value"])
            if value is None or value == "":
                continue
            found.append(_element(value, item.get("Status"),
                                  item.get("StatusClarification"),
                                  f"{name}.{array}[{number}]", verdict))
        for field in rule.get("lists", ()):
            for number, item in enumerate(items_of(body.get(field))):
                if item not in (None, ""):
                    where = f"{name}.{field}[{number}]"
                    found.append(_element(item, None, None, where, verdict))
        for field in rule.get("scalars", ()):
            value = body.get(field)
            if isinstance(value, dict):
                # Одиночное видеодополнение приходит структурой, а не числом.
                found.append(_element(value.get(rule["value"]),
                                      value.get("Status"),
                                      value.get("StatusClarification"),
                                      f"{name}.{field}", verdict))
                continue
            if value in (None, ""):
                continue
            moderation = body.get(f"{field.replace('Hash', '')}Moderation")
            moderation = moderation if isinstance(moderation, dict) else {}
            found.append(_element(value, moderation.get("Status"),
                                  moderation.get("StatusClarification"),
                                  f"{name}.{field}", verdict))
    return found


def kit_limits(limits=None) -> dict:
    """Пределы состава комплекта из справочника лимитов."""
    if limits is None:
        from writer import Limits

        limits = Limits.load()
    found = {}
    for part, rule_name in KIT_LIMITS.items():
        rule = limits.collections.get(rule_name) or {}
        limit = rule.get("max")
        if not isinstance(limit, int) or limit < 1:
            raise DirectFailure(
                f"В справочнике лимитов нет предела состава «{rule_name}». "
                f"Без него недозаполненный комплект не от чего отличать."
            )
        found[part] = limit
    return found


def composition(record: dict, limits=None) -> dict:
    """Состав комплекта объявления: сколько чего, чего не хватает и что отклонено."""
    bodies = bodies_of(record)
    # Комплект берётся из комбинаторной структуры, когда она пришла, и из
    # остальных, когда её нет, — как `responsive.Kit.of` собирает комплект для
    # записи. Складывать структуры нельзя: заголовок, лежащий и в `Titles`, и в
    # соседнем `Title`, посчитался бы дважды, и комплект «7 из 7» вырос бы до
    # восьми. Ветка тут не на `Type` — на то, что **пришло**: тип Директ меняет
    # сам, а пришедшее уже пришло.
    sources = ({RESPONSIVE: bodies[RESPONSIVE]} if RESPONSIVE in bodies
               else bodies)
    # Вердикт снимается один раз со `Status` **объявления** и достаётся всем
    # частям сразу. Спрашивать его у каждого читателя значило бы поставить
    # правило на один путь показа из нескольких — а обходится такое вторым.
    verdict, verdict_note = moderation_verdict(record)
    parts = {part: _part(sources, part, verdict) for part in KIT_PARTS}
    tops = kit_limits(limits)
    counts = {part: len(items) for part, items in parts.items()}
    applies = any(name in sources for name in KIT_STRUCTURES)
    missing = ({part: max(0, tops[part] - counts[part]) for part in tops}
               if applies else {})
    # Отказ спрашивается у самого поля, и вердикт объявления его не заслоняет.
    # Причин молчать на черновике две, и работает вторая: не «отбор знает, что
    # вердикта нет», а «в поле не `REJECTED`» — начальное значение `ACCEPTED`,
    # и оно замерено. Разница не словесная: заслони отказ отсутствием вердикта
    # — и `--rejected` промолчит на объявлении, у которого элемент отклонён
    # по-настоящему, а показ прежней версии продолжается. Скрывать `ACCEPTED`
    # стоит недоказанной похвалы, скрывать `REJECTED` — настоящего сигнала.
    rejected = [item for items in parts.values() for item in items
                if item["status"] == REJECTED]
    return {
        "structures": sorted(bodies),
        "kit_from": sorted(sources),
        "expected_structure": expected_body(record),
        # Вердикт модерации по объявлению и — когда его нет — почему. Стоит
        # рядом с составом, потому что читается вместе с ним: поэлементный
        # статус без этого ответа не означает ничего.
        "verdict": verdict,
        "verdict_note": verdict_note,
        "parts": parts,
        "counts": counts,
        "limits": tops,
        "missing": missing,
        "kit_applies": applies,
        "combinations": (counts["titles"] * counts["texts"]) if applies else None,
        "complete": (not missing["titles"] and not missing["texts"]
                     if applies else None),
        "rejected": rejected,
    }


def ad_row(record: dict, limits=None) -> dict:
    """Плоская строка объявления — то же назначение, что у строки группы."""
    kit = composition(record, limits)
    bodies = bodies_of(record)
    combo = bodies.get("ResponsiveAd") or {}
    # Ссылку и домен показывает та структура, где они есть: у комбинаторного
    # объявления своя, у остальных своя. Перебор идёт по пришедшим структурам,
    # а не по типу, по той же причине, что и весь разбор.
    link, domain, path = "", "", ""
    for body in bodies.values():
        link = link or (body.get("Href") or "")
        domain = domain or (body.get("DisplayDomain") or "")
        path = path or (body.get("DisplayUrlPath") or "")
    kind = record.get("Type") or ""
    return {
        "id": record.get("Id"),
        "campaign_id": record.get("CampaignId"),
        "group_id": record.get("AdGroupId"),
        "type": kind,
        "type_ru": AD_TYPE_RU.get(kind, kind),
        "subtype": record.get("Subtype") or "",
        "state": record.get("State") or "",
        "state_ru": AD_STATE_RU.get(record.get("State"), record.get("State") or ""),
        "status": record.get("Status") or "",
        "status_ru": AD_STATUS_RU.get(record.get("Status"),
                                      record.get("Status") or ""),
        "clarification": record.get("StatusClarification") or "",
        "structure": kit["expected_structure"],
        "structures": ", ".join(kit["structures"]),
        "titles": kit["counts"]["titles"],
        "texts": kit["counts"]["texts"],
        "images": kit["counts"]["images"],
        "videos": kit["counts"]["videos"],
        "carousel": kit["counts"]["carousel"],
        "combinations": kit["combinations"],
        "complete": kit["complete"],
        "missing_titles": kit["missing"].get("titles"),
        "missing_texts": kit["missing"].get("texts"),
        "rejected_elements": len(kit["rejected"]),
        "href": link,
        "display_domain": domain,
        "display_url_path": path,
        "sitelink_set_id": combo.get("SitelinkSetId") or "",
        "business_id": combo.get("BusinessId") or "",
        "ad_extensions": len(items_of(combo.get("AdExtensions"))),
        "age_label": record.get("AgeLabel") or "",
        "ad_categories": ", ".join(str(one) for one
                                   in items_of(record.get("AdCategories"))),
    }


AD_COLUMNS = [
    "id", "campaign_id", "group_id", "type", "subtype", "state", "status",
    "clarification", "structure", "structures", "titles", "texts", "images",
    "videos", "carousel", "combinations", "complete", "missing_titles",
    "missing_texts", "rejected_elements", "href", "display_domain",
    "display_url_path", "sitelink_set_id", "business_id", "ad_extensions",
    "age_label",
]


def asks_responsive(params: dict) -> None:
    """Сторож: в запросе объявлений есть `ResponsiveAdFieldNames`.

    Проверяется **запрос**, а не ответ, и это существенно. По ответу усечённое
    чтение неотличимо от кабинета, где комбинаторных объявлений просто нет: в
    обоих случаях приходят `TEXT_AD` с одним заголовком. Отличает их только то,
    о чём спросили, — и спросить можно ровно один раз.

    Сторожит он не сегодняшний код (`ads_params` кладёт набор всегда), а
    завтрашний: аргумент «читать только такие-то поля», условие на типе
    кампании, попытка сэкономить балл. Любая из этих правок выглядит невинно и
    отчитывается успехом, потеряв шесть заголовков из семи."""
    asked = params.get(f"{RESPONSIVE}FieldNames")
    missing = sorted(set(RESPONSIVE_REQUIRED) - set(asked or ()))
    if missing:
        raise DirectFailure(
            f"Запрос объявлений уходит без {RESPONSIVE}FieldNames"
            + (" полностью" if not asked
               else f" по полям {', '.join(missing)}")
            + ". Комбинаторное объявление вернулось бы текстово-графическим — "
              "с одним заголовком вместо семи и без признака, что данные "
              "неполны."
        )


def asks_autotargeting(params: dict) -> None:
    """Сторож: в запросе фраз есть обе формы настроек автотаргетинга.

    По тем же основаниям. Вложенная форма — единственная, где виден `Narrow`;
    плоская — единственная, по которой видно, что Директ перестал согласовывать
    формы между собой. Запрос без любой из них отвечает успехом и молчит о
    половине настройки."""
    for name in ("AutotargetingSettingsCategoriesFieldNames",
                 "AutotargetingSettingsBrandOptionsFieldNames"):
        if not params.get(name):
            raise DirectFailure(
                f"Запрос фраз уходит без {name}. Настройки автотаргетинга "
                f"вернулись бы наполовину, и ответ был бы успешным."
            )
    missing = sorted({"AutotargetingCategories", "AutotargetingBrandOptions"}
                     - set(params.get("FieldNames") or ()))
    if missing:
        raise DirectFailure(
            f"Запрос фраз уходит без плоских настроек ({', '.join(missing)}). "
            f"Рассогласование форм — то, ради чего читаются обе, — стало бы "
            f"невидимым."
        )


# --------------------------------------------------------------------------
# Фразы: разбор
# --------------------------------------------------------------------------

def is_autotargeting(record) -> bool:
    """Автотаргетинг ли это — по тексту, а не по типу объекта.

    Признака типа у `Keywords.get` нет вовсе: сервис отдаёт фразы и
    автотаргетинги одним списком с одинаковым набором полей."""
    text = record.get("Keyword") if isinstance(record, dict) else record
    return isinstance(text, str) and text.strip() == AUTOTARGETING


def autotargeting_of(record: dict) -> dict:
    """Настройки автотаргетинга обеими формами и расхождения между ними."""
    nested = record.get("AutotargetingSettings")
    nested = nested if isinstance(nested, dict) else {}
    halves = {}
    for half in ("Categories", "BrandOptions"):
        body = nested.get(half)
        halves[half] = body if isinstance(body, dict) else {}
    flat = {}
    for item in items_of(record.get("AutotargetingCategories")):
        if isinstance(item, dict) and item.get("Category"):
            flat[item["Category"]] = item.get("Value")
    for item in items_of(record.get("AutotargetingBrandOptions")):
        if isinstance(item, dict) and item.get("Option"):
            flat[item["Option"]] = item.get("Value")
    disagreements = []
    for name, (half, field) in sorted(FLAT_TO_NESTED.items()):
        if name not in flat or field not in halves[half]:
            continue
        if flat[name] != halves[half][field]:
            disagreements.append({"flat": name, "half": half, "field": field,
                                  "flat_value": flat[name],
                                  "nested_value": halves[half][field]})
    return {
        "nested": halves,
        "flat": flat,
        "disagreements": disagreements,
        # `Narrow` читается только вложенной формой. Отсутствие его в плоской —
        # не расхождение, а свойство формы, и путать эти два не надо.
        "only_nested": {ONLY_NESTED[1]: halves[ONLY_NESTED[0]].get(ONLY_NESTED[1])},
        "read": bool(halves["Categories"] or halves["BrandOptions"] or flat),
    }


def enabled(named: dict, labels: dict) -> str:
    """Включённые настройки словами. Пустая строка — включённых нет."""
    return ", ".join(labels.get(name, name) for name, value in named.items()
                     if value == "YES")


def keyword_row(record: dict, currency: str = "") -> dict:
    """Плоская строка фразы или автотаргетинга."""
    settings = autotargeting_of(record)
    state = record.get("State") or ""
    status = record.get("Status") or ""
    serving = str(record.get("ServingStatus") or "")
    return {
        "id": record.get("Id"),
        "group_id": record.get("AdGroupId"),
        "campaign_id": record.get("CampaignId"),
        "keyword": record.get("Keyword") or "",
        "autotargeting": is_autotargeting(record),
        "state": state,
        "state_ru": KEYWORD_STATE_RU.get(state, state),
        "status": status,
        "status_ru": KEYWORD_STATUS_RU.get(status, status),
        "serving_status": serving,
        "serving_ru": SERVING_RU.get(serving, serving),
        "rarely_served": serving == "RARELY_SERVED",
        "bid": money_text(record.get("Bid"), currency),
        "context_bid": money_text(record.get("ContextBid"), currency),
        "search_bid_is_auto": record.get("AutotargetingSearchBidIsAuto") or "",
        "priority": record.get("StrategyPriority") or "",
        "param1": record.get("UserParam1") or "",
        "param2": record.get("UserParam2") or "",
        "categories": enabled(settings["nested"]["Categories"], CATEGORY_RU),
        "brands": enabled(settings["nested"]["BrandOptions"], BRAND_RU),
        "forms_disagree": len(settings["disagreements"]),
    }


KEYWORD_COLUMNS = [
    "id", "group_id", "campaign_id", "keyword", "autotargeting", "state",
    "status", "serving_status", "rarely_served", "bid", "context_bid",
    "search_bid_is_auto", "priority", "param1", "param2", "categories",
    "brands", "forms_disagree",
]


def money_text(units, currency: str = "") -> str:
    """Денежное значение словами. Пустая строка — «неизвестно», а не «ноль».

    Разница существенна: ставка, которой в ответе нет, и ставка в ноль — разные
    вещи, а у автотаргетинга без ставки поле законно пустое."""
    if units is None:
        return ""
    try:
        return money.format_api(units, currency)
    except money.MoneyError:
        # Чужое значение в денежном поле — не повод уронить чтение группы.
        # Показывается как есть, чтобы человек увидел, что именно пришло.
        return str(units)
