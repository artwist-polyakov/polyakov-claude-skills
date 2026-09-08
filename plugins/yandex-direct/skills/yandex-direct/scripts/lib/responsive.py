"""Комплект комбинаторного объявления: одно содержимое в трёх формах."""

from __future__ import annotations

from config import DirectFailure, excerpt
from writer import CLEAR  # noqa: F401  — признак очистки, общий на весь скилл

SERVICE = "ads"
GROUPS = "adgroups"

# Тип объявления и имя структуры. Второе задаёт имена полей чтения:
# `ResponsiveAdFieldNames` образуется от имени структуры.
AD_TYPE = "RESPONSIVE_AD"
STRUCTURE = "ResponsiveAd"

# Общие поля ответа `Ads.get`. `Type` и `Subtype` читаются не ради ветвления, а
# ради журнала и предпросмотра: человеку видно, с чем он имеет дело.
FIELD_NAMES = ("Id", "CampaignId", "AdGroupId", "Type", "Subtype", "State",
               "Status")

# Поля комплекта. `AdImages` и `VideoExtensions` — имена **чтения**; имена
# записи (`AdImageHashes`, `VideoExtensionIds`) перечислением отвергаются.
RESPONSIVE_FIELD_NAMES = ("Titles", "Texts", "Href", "DisplayUrlPath",
                          "AdImages", "VideoExtensions")

# Поля текстово-графической структуры. Читаются **всегда**, вместе с
# предыдущими, и только на чтение: дополнительный заголовок живёт здесь, а
# заранее знать, прошло объявление конвертацию или нет, нельзя.
TEXT_FIELD_NAMES = ("Title", "Title2", "Text", "Href", "DisplayUrlPath")

# Правила справочника по путям запроса. Ключ — путь без индексов, значение —
# имя правила в `limits.json`. Пути одни и те же в обеих формах записи:
# `AdImageHashes` у `add` массив, у `update` обёртка с `Items`, а правило на
# состав понимает и то, и другое.
TEXTS = {
    f"{STRUCTURE}.Titles": f"{STRUCTURE}.Titles.item",
    f"{STRUCTURE}.Texts": f"{STRUCTURE}.Texts.item",
    f"{STRUCTURE}.Href": "Href",
    f"{STRUCTURE}.DisplayUrlPath": "DisplayUrlPath",
}

COLLECTIONS = {
    f"{STRUCTURE}.Titles": f"{STRUCTURE}.Titles",
    f"{STRUCTURE}.Texts": f"{STRUCTURE}.Texts",
    f"{STRUCTURE}.AdImageHashes": f"{STRUCTURE}.AdImageHashes",
    f"{STRUCTURE}.VideoExtensionIds": f"{STRUCTURE}.VideoExtensionIds",
}

MERGED_TITLE_LIMIT = 56

# Разделитель, которым автоконвертация склеивает основной заголовок с
# дополнительным. Считается в длину наравне с текстом.
TITLE_JOINER = ". "


# Поля комплекта, которые `ResponsiveAdUpdate` объявляет `nillable`: имя поля
# `Kit` → пара «имя в запросе, как это зовётся человеку». Имя аргумента команды
# отсюда же — оно отличается только дефисами вместо подчёркиваний, и второго
# перечня для него не заводится: разошедшиеся перечни означали бы флаг, который
# принимается и не делает ничего.
#
# `href` перечислен наравне с остальными, хотя очистка его не проходит: схема
# объявляет поле `nillable`, а объект без цели Директ не принимает (замер
# ниже, в `Kit`). Убрать его отсюда значило бы отвечать на просьбу «invalid
# choice» вместо названной причины — а причина здесь и есть весь ответ.
CLEARABLE = {
    "href": ("Href", "ссылка"),
    "display_url_path": ("DisplayUrlPath", "отображаемая ссылка"),
    "images": ("AdImageHashes", "изображения"),
    "videos": ("VideoExtensionIds", "видео"),
}


def rules_for(items) -> tuple:
    """Какие правила справочника объявить этой записи: пара «тексты, состав».

    Объявляются только пути, которые в элементах есть. Движок отвергает
    правило, которому нечего проверять, и он прав: правило, объявленное на
    отсутствующий путь, неотличимо от опечатки в пути, а опечатка молча не
    проверяет ничего. Отсюда и здесь не общий словарь, а его сужение под
    конкретную запись: `Href` и отображаемая ссылка есть не у всякого
    комплекта.

    Очищаемое поле в перечень не попадает. `null` в запросе — это не значение,
    у которого бывает длина или состав: правило справочника на нём не проверяет
    ничего и жалуется на всё — «ожидается строка, а не NoneType», — и законная
    очистка вставала бы «остановлено валидацией»."""
    present = set()
    for item in items:
        for key, value in (item.get(STRUCTURE) or {}).items():
            if value is None:
                continue
            present.add(f"{STRUCTURE}.{key}")
    return ({path: rule for path, rule in TEXTS.items() if path in present},
            {path: rule for path, rule in COLLECTIONS.items() if path in present})


def read_params(*, fields=FIELD_NAMES) -> dict:
    """Параметры `Ads.get` для конвейера: оба набора имён полей разом.

    Отбор по идентификаторам подставляет движок записи, поэтому его здесь нет.

    Оба набора — не перестраховка. С одним `TextAdFieldNames` уже
    сконвертированное объявление приходит как текстово-графическое: один
    заголовок, один текст, и `Type: TEXT_AD` в придачу. Проверено на живом
    кабинете 29.08.2026 — один и тот же идентификатор отдаётся то так, то этак,
    в зависимости от того, о чём спросили."""
    return {
        "FieldNames": list(fields),
        "TextAdFieldNames": list(TEXT_FIELD_NAMES),
        f"{STRUCTURE}FieldNames": list(RESPONSIVE_FIELD_NAMES),
    }


def merged_title(title: str, title2, limit: int = MERGED_TITLE_LIMIT) -> tuple:
    """Как склеивается дополнительный заголовок: пара «заголовок, остаток».

    Остаток — это `title2`, который в склейку не поместился. Автоконвертация в
    этом случае его **теряет**; здесь он возвращается наружу, чтобы уйти в
    комплект отдельным заголовком: у комбинаторного объявления их до семи, и
    повторять потерю незачем."""
    if not title2:
        return title, None
    glued = f"{title}{TITLE_JOINER}{title2}"
    if len(glued) <= limit:
        return glued, None
    return title, title2


class Kit:
    """Комплект комбинаторного объявления.

    Значения хранятся ровно так, как их видит человек, — строками и числами.
    Формы собираются методами: `added`, `updated`, `expected`. Хранить одну из
    форм и переделывать её в остальные значило бы завести четвёртое
    представление и первым же делом перепутать его с одним из трёх."""

    __slots__ = ("titles", "texts", "images", "videos", "href",
                 "display_url_path")

    def __init__(self, titles, texts, *, images=(), videos=(), href=None,
                 display_url_path=None):
        for name, value in (("href", href),
                            ("display_url_path", display_url_path)):
            if value is None or value is CLEAR or str(value).strip():
                continue
            # Пустая строка — не «оставить как было» и не очистка. Молча
            # превращать её в первое нельзя: команда отчиталась бы успехом, а
            # старое значение осталось бы в кабинете. Считать её вторым — тоже:
            # очистка называется явно, и признак, который можно получить
            # случайным пустым аргументом, отличать две просьбы перестаёт.
            said, flag = CLEARABLE[name][1], name.replace("_", "-")
            raise DirectFailure(
                f"Пустая {said} — Директ пустую строку в этом поле не "
                f"принимает. Очистка называется явно: `--clear {flag}`. Чтобы "
                f"оставить прежнее значение, уберите аргумент."
            )
        self.titles = [str(one) for one in titles]
        self.texts = [str(one) for one in texts]
        self.images = CLEAR if images is CLEAR else [str(one) for one in images]
        self.videos = CLEAR if videos is CLEAR else [int(one) for one in videos]
        self.href = href
        self.display_url_path = display_url_path
        if self.href is CLEAR:
            # `nillable` в схеме означает, что тип поля принимает `null`, а не
            # что объект без него остаётся годным. У ссылки расходится именно
            # это: `Ads.update` с `Href: null` возвращает `6000 ·
            # Неконсистентное состояние объекта — В объявлении должна быть
            # указана организация или основная ссылка`. Замерено на кабинете
            # `api-artwist-test` 29.08.2026 дважды — в одиночку и вместе с
            # отображаемой ссылкой, — отказ один и тот же (исходная проверка API,
            # раздел 9).
            #
            # Отказ здесь, а не отказом Директа за баллы: он поэлементный, и
            # одна негодная просьба роняет весь пакет — «снять ссылку у
            # объявлений группы» не записало бы ни одного.
            raise DirectFailure(
                "Снять ссылку у объявления нечем: Директ отвечает «6000 · "
                "Неконсистентное состояние объекта — В объявлении должна быть "
                "указана организация или основная ссылка», в том числе когда "
                "отображаемую снимают вместе с ней. Ссылку можно заменить "
                "другой (`--href`); остаться без цели объявление может только "
                "с привязанной организацией, а `BusinessId` скилл не пишет "
                "(scripts/ads_write.py)."
            )
        if self.display_url_path and not self.href:
            # Правило самого Директа: отображаемая ссылка допустима только при
            # заполненной `Href`. Справочник лимитов его не выражает — там
            # длины и состав, а не зависимости полей, — поэтому оно живёт
            # здесь, рядом с формами, а не в валидации по справочнику.
            raise DirectFailure(
                "Отображаемая ссылка задана, а `Href` нет. Директ такую пару "
                "не принимает: `DisplayUrlPath` допустима только при "
                "заполненной ссылке."
            )

    # -- сборка из ответа ---------------------------------------------------

    @classmethod
    def of(cls, ad: dict, *, carry: bool = True) -> "Kit":
        """Комплект из ответа `Ads.get`, прочитанного обоими наборами полей.

        Ветки по `Type` здесь нет и быть не может: день конвертации Яндекс
        выбирает случайно, и объявление, вчера бывшее текстово-графическим,
        сегодня комбинаторное — без предупреждения и без нашего участия.
        Собирается комплект по тому, что **пришло**: заголовки берутся из
        комбинаторной структуры, а когда её в ответе нет — из
        текстово-графической.

        Дополнительный заголовок подхватывается в обоих случаях. У
        сконвертированного объявления его уже нет — конвертация склеила или
        потеряла его сама, — а у несконвертированного он и есть то, что мы
        переносим в комплект раньше, чем Яндекс его потеряет.

        `carry=False` собирает тот же комплект **без** переноса. Нужен он, чтобы
        было с чем сравнить: перенос — отдельная просьба, и понять, дал ли он
        что-нибудь, можно только рядом с комплектом, где его не делали.
        Вычитать поле из прочитанного ответа для этого нельзя — тогда в коде
        появляется присваивание в `TextAd`, неотличимое от пути записи, которого
        в скилле нет."""
        combo = ad.get(STRUCTURE) or {}
        legacy = ad.get("TextAd") or {}
        titles = [item["Title"] for item in combo.get("Titles") or []
                  if item.get("Title")]
        texts = [item["Text"] for item in combo.get("Texts") or []
                 if item.get("Text")]
        if not titles and legacy.get("Title"):
            titles = [legacy["Title"]]
        if not texts and legacy.get("Text"):
            texts = [legacy["Text"]]
        kit = cls(
            titles, texts,
            images=[item["ImageHash"] for item in _items(combo.get("AdImages"))
                    if item.get("ImageHash")],
            videos=[item["CreativeId"]
                    for item in _items(combo.get("VideoExtensions"))
                    if item.get("CreativeId") is not None],
            href=combo.get("Href") or legacy.get("Href"),
            display_url_path=(combo.get("DisplayUrlPath")
                              or legacy.get("DisplayUrlPath")),
        )
        return kit.carrying(legacy.get("Title2")) if carry else kit

    def carrying(self, title2) -> "Kit":
        """Комплект с перенесённым дополнительным заголовком.

        Судьбу заголовка решает `_carry` — здесь берётся только комплект.
        Что при этом могло пропасть, отвечает `crowded_out`, и берёт он ответ
        оттуда же."""
        return _carry(self, title2)[0]

    def but(self, **over) -> "Kit":
        """Тот же комплект с заменёнными частями. Исходный не меняется."""
        values = {
            "titles": self.titles, "texts": self.texts, "images": self.images,
            "videos": self.videos, "href": self.href,
            "display_url_path": self.display_url_path,
        }
        values.update(over)
        titles, texts = values.pop("titles"), values.pop("texts")
        return Kit(titles, texts, **values)

    # -- три формы ----------------------------------------------------------

    def cleared(self) -> list:
        """Поля, которые комплект просит очистить, — именами `Kit`.

        Порядок задаёт `CLEARABLE`, а не порядок аргументов: перечень уходит
        человеку в предпросмотр, и «то же самое, но в другом порядке» читается
        как другая правка."""
        return [name for name in CLEARABLE if getattr(self, name) is CLEAR]

    def added(self) -> dict:
        """`ResponsiveAdAdd`: плоские массивы."""
        cleared = self.cleared()
        if cleared:
            # `nillable` — свойство `ResponsiveAdUpdate`, и только его. У
            # создаваемого объявления очищать нечего: поля, которого нет, не
            # бывает со старым значением, а `null` в `add` Директ не примет.
            raise DirectFailure(
                f"Создаваемый комплект просит очистить: "
                f"{', '.join(CLEARABLE[one][1] for one in cleared)}. При "
                f"создании очищать нечего — в `ResponsiveAdAdd` эти поля не "
                f"`nillable`. Просто не передавайте их."
            )
        return self._shape(lambda values: list(values))

    def updated(self) -> dict:
        """`ResponsiveAdUpdate`: обёртки `general:ArrayOf…` с `Items`.

        Форма другая не по прихоти: в WSDL у `update` эти поля объявлены типом
        `general:ArrayOfString` и `general:ArrayOfLong`, а те содержат
        единственный повторяющийся `Items`. Плоский массив здесь означает не
        отказ, а иное толкование запроса."""
        return self._shape(lambda values: {"Items": list(values)})

    def expected(self) -> dict:
        """Что должен вернуть `Ads.get` после записи этого комплекта.

        Служит `expect` операции: сверять форму записи с формой чтения нельзя,
        они не сходятся по построению. Поля модерации сюда не попадают — сверка
        сравнивает отправленные ключи, а не объект целиком, и статус каждого
        элемента к сохранности комплекта отношения не имеет.

        Очищенное поле ожидается **явным `null`**, а не пропуском ключа. Ключ,
        которого в ожидании нет, сверка не обходит вовсе — и очистка, не
        случившаяся в кабинете, прошла бы как удавшаяся: поле со старым
        значением никто не спросил. Замер того, что `Ads.get` отдаёт после
        очистки, — состав ответа API; сверка принимает все три вида
        пустоты, но спрашивает про поле обязательно."""
        shape = {
            "Titles": [{"Title": one} for one in self.titles],
            "Texts": [{"Text": one} for one in self.texts],
        }
        if self.images is CLEAR:
            shape["AdImages"] = None
        elif self.images:
            shape["AdImages"] = {
                "Items": [{"ImageHash": one} for one in self.images]}
        if self.videos is CLEAR:
            shape["VideoExtensions"] = None
        elif self.videos:
            shape["VideoExtensions"] = {
                "Items": [{"CreativeId": one} for one in self.videos]}
        return _with_link(shape, self.href, self.display_url_path)

    def _shape(self, wrap) -> dict:
        """Общее у обеих форм записи: заголовки и тексты плоские всегда.

        Обёртка касается только изображений и видео — у заголовков и текстов
        форма одна и та же в `add` и в `update`. Пустые коллекции не
        передаются вовсе: в `update` пустое значение означает отвязать всё, а
        комплект, из которого не просили ничего убирать, убирать нечего.

        Просьба убрать выражается `CLEAR` и уходит `null`, а не пустой
        обёрткой: `null` очищает поле по документации, а `{"Items": []}` — это
        массив из нуля элементов, которому справочник задаёт минимум в один."""
        shape = {"Titles": list(self.titles), "Texts": list(self.texts)}
        if self.images is CLEAR:
            shape["AdImageHashes"] = None
        elif self.images:
            shape["AdImageHashes"] = wrap(self.images)
        if self.videos is CLEAR:
            shape["VideoExtensionIds"] = None
        elif self.videos:
            shape["VideoExtensionIds"] = wrap(self.videos)
        return _with_link(shape, self.href, self.display_url_path)

    # -- человеку -----------------------------------------------------------

    def summary(self) -> str:
        parts = [f"заголовков {len(self.titles)}", f"текстов {len(self.texts)}"]
        if self.images:
            parts.append(f"изображений {len(self.images)}")
        if self.videos:
            parts.append(f"видео {len(self.videos)}")
        cleared = self.cleared()
        if cleared:
            # Очистка называется в той же строке, что и состав: это описание
            # уходит в план, в вопрос человеку и в журнал, а снятая ссылка —
            # ровно та новость, ради которой человека и спрашивают.
            parts.append("очищается: "
                         + ", ".join(CLEARABLE[one][1] for one in cleared))
        return ", ".join(parts)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Kit):
            return NotImplemented
        return self.expected() == other.expected()

    def __repr__(self) -> str:
        return f"<Kit {self.summary()}: {excerpt(self.titles[:1], 40)}>"


def _carry(kit: "Kit", title2):
    """Перенос дополнительного заголовка: пара «комплект, потерянное».

    **Единственное место, где решается судьба `Title2`.** Раньше решений было
    два — склейку делал `carrying`, а на вопрос «пропало ли» отвечал
    `crowded_out`, — и оговорка «он уже в комплекте» попала только во второе:
    комплект `['Alpha', 'Beta']` с `Title2 == 'Beta'` превращался в
    `['Alpha. Beta', 'Beta']`, то есть в дубль, и объявление уходило на
    модерацию заново вместо того, чтобы остаться нетронутым.

    Склейка повторяет автоконвертацию, а её отказ — нет: не поместившийся
    заголовок уходит отдельным элементом и пропадает только тогда, когда все
    места заняты."""
    if not title2 or not kit.titles:
        return kit, None
    if title2 in kit.titles:
        # Содержимое уже в комплекте отдельным заголовком: ни склеивать, ни
        # терять нечего. Комплект от этого не меняется — как и когда места не
        # нашлось, — но новость ровно обратная, и путать их нельзя.
        return kit, None
    first, rest = merged_title(kit.titles[0], title2)
    titles = [first] + kit.titles[1:]
    if rest is None:
        return kit.but(titles=titles), None
    if len(titles) < titles_max():
        return kit.but(titles=titles + [rest]), None
    return kit, rest


def crowded_out(ad: dict):
    """Дополнительный заголовок, которому в комплекте не нашлось места.

    `None` — переносить нечего либо перенос удался. Строка — заголовок,
    который **пропадёт**: он не пуст, в склейку не поместился, а свободных мест
    в комплекте нет.

    Отдельная функция, а не признак внутри `Kit`, по той же причине, по какой
    комплект не хранит одну из своих форм: `Kit` описывает содержимое, а это
    вопрос про исходное объявление, которого у комплекта уже нет.

    Молчать об этом случае нельзя. Команда переноса иначе отчитывается «уже в
    комплекте либо пусто» — и то, и другое неправда, — а текст доживает ровно
    до автоконвертации, которая его выбросит."""
    title2 = (ad.get("TextAd") or {}).get("Title2")
    if not title2:
        return None
    return _carry(Kit.of(ad, carry=False), title2)[1]


def titles_max() -> int:
    """Сколько заголовков помещается в комплект — по справочнику лимитов."""
    from writer import Limits

    rule = Limits.load().collections.get(f"{STRUCTURE}.Titles") or {}
    limit = rule.get("max")
    if not isinstance(limit, int) or limit < 1:
        raise DirectFailure(
            f"В справочнике лимитов нет предела состава «{STRUCTURE}.Titles». "
            f"Без него перенос дополнительного заголовка дописывал бы элементы "
            f"вслепую, а отказ приходил бы от Директа уже за баллы."
        )
    return limit


def _items(value):
    """Элементы коллекции чтения: `{"Items": [...]}` либо `null`.

    Оба вида приходят от Директа: `AdImages` объявлены `nillable`, и у
    объявления без изображений поле либо отсутствует, либо равно `null`.
    Плоский массив тоже принимается — так эти же поля выглядят в срезе
    ответа API после удаления служебных ключей."""
    if isinstance(value, dict):
        return value.get("Items") or []
    if isinstance(value, list):
        return value
    return []


def _with_link(shape: dict, href, display_url_path) -> dict:
    """Ссылка и отображаемая ссылка — общая часть всех трёх форм.

    Состояний у поля три, и каждое выражается своим: значение кладётся как
    есть, `CLEAR` кладётся `null`, «не трогать» не кладётся вовсе. Свести
    последние два к одному нельзя — на них Директ отвечает противоположным.

    Очищенной сюда доходит только отображаемая ссылка: очистку самой `Href`
    комплект отвергает раньше, замером. Разбирается пара всё равно одинаково —
    правило про ссылку принадлежит `Kit`, и второе его изложение здесь
    разошлось бы с первым в тот день, когда правило изменится."""
    for key, value in (("Href", href), ("DisplayUrlPath", display_url_path)):
        if value is CLEAR:
            shape[key] = None
        elif value:
            shape[key] = value
    return shape


# --------------------------------------------------------------------------
# Операции конвейера
# --------------------------------------------------------------------------
#
# Собираются здесь, а не в вызывающем скрипте, по той же причине, по какой
# формы живут в `Kit`: три представления одного содержимого расходятся молча, и
# место, где они сводятся вместе с правилами справочника и объявленным
# расхождением, должно быть ровно одно.
#
# Правка комплекта не трогает ни денег, ни показов: остановленное объявление
# от новых заголовков не начнёт тратить. Прежде это записывалось как «средний
# уровень риска» со ссылкой на раздел 6 архитектуры; уровней прежние не
# оставило, и спрашивают здесь ровно так же, как везде. Свойство осталось, и
# оно про то, что цена ошибки тут — текст, а не расход.


def add_operation(group_id, kits, labels=None):
    """Создание комбинаторных объявлений в группе.

    Идентификатора у создаваемого объявления ещё нет, поэтому в плане и в
    журнале оно зовётся именем из `labels`. Перечитывание от этого не
    отменяется: движок берёт назначенные Директом идентификаторы из ответа и
    сверяет созданное тем же сравнением, что и изменённое.

    Поэтому здесь `search=None`: конвейер скажет «идентификатора Директ не
    прислал, а чем искать созданное, операция не назвала — проверьте кабинет».
    Ответ неполный, но честный, и лучшего у этого сервиса нет."""
    from policy import Change
    from writer import Operation

    kits = list(kits)
    for kit in kits:
        if not kit.href:
            # Директ принимает хотя бы одно из `Href` и `BusinessId`
            # (`API_OBJECTS.md`, раздел 5.2). `BusinessId` скилл не пишет:
            # привязка профиля организации требует `IsPublished: YES` и живёт
            # не здесь. Значит годная цель у создаваемого объявления ровно
            # одна, и спросить её надо тут, а не отказом Директа за баллы —
            # один негодный элемент роняет весь пакет целиком.
            #
            # Правило стоит у создания, а не в самом комплекте: обновление без
            # ссылки законно — её просто не трогают.
            raise DirectFailure(
                f"Комплект «{excerpt(kit.titles[:1], 40)}» создаётся без "
                f"ссылки. Комбинаторному объявлению нужна цель: Директ "
                f"принимает `Href` либо `BusinessId`, и второго скилл не "
                f"пишет."
            )
    labels = list(labels) if labels is not None else [
        f"объявление {number + 1} в группе {group_id}"
        for number in range(len(kits))
    ]
    items, expect, changes = [], [], []
    for kit, label in zip(kits, labels):
        items.append({"AdGroupId": group_id, STRUCTURE: kit.added()})
        expect.append({"AdGroupId": group_id, STRUCTURE: kit.expected()})
        changes.append(Change(object_id=label, what="группа",
                              field="AdGroupId", after=group_id,
                              service="объявление"))
        changes.append(Change(object_id=label,
                              what=f"комплект ({kit.summary()})",
                              field=STRUCTURE, after=kit.added(),
                              service="объявление"))
    texts, collections = rules_for(items)
    return Operation(
        SERVICE, "add", params_key="Ads", items=items, expect=expect,
        labels=labels, changes=changes, read=read_params(), search=None,
        derived=(STRUCTURE,), texts=texts, collections=collections,
    )


def update_operation(kits: dict):
    """Обновление комбинаторных объявлений: `{идентификатор: Kit}`.

    Комплект передаётся целиком — частичного обновления не бывает. Отсюда
    единственный безопасный порядок: прочитать объявление, собрать `Kit.of`,
    поправить нужное и отдать сюда. Собранный из головы комплект затрёт то, о
    чём вызывающий код не знал."""
    from policy import Change
    from writer import Operation

    items, expect, changes = [], [], []
    cleared = set()
    for identifier, kit in kits.items():
        items.append({"Id": identifier, STRUCTURE: kit.updated()})
        expect.append({"Id": identifier, STRUCTURE: kit.expected()})
        changes.append(Change(object_id=identifier,
                              what=f"комплект ({kit.summary()})",
                              field=STRUCTURE, after=kit.updated(),
                              service="объявление"))
        # Какие пути этой записи очищаются, движок сам не выведет: `nillable` —
        # свойство схемы Директа, а не догадка по значению. Без объявления он
        # проверял бы `null` правилом длины и жаловался бы на законную очистку;
        # с объявлением на весь запрос — отпускал бы и `null` в заголовке,
        # который очисткой не бывает и до Директа доезжать не должен.
        cleared.update(f"{STRUCTURE}.{CLEARABLE[name][0]}"
                       for name in kit.cleared())
    texts, collections = rules_for(items)
    return Operation(
        SERVICE, "update", params_key="Ads", items=items, expect=expect,
        changes=changes, read=read_params(), derived=(STRUCTURE,),
        texts=texts, collections=collections, clears=tuple(sorted(cleared)),
    )
