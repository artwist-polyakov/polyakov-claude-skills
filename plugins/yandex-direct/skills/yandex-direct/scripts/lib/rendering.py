"""Отрисовка объявления в самодостаточную HTML-страницу: состав и обрезание."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from config import DirectFailure, excerpt
from writer import Limits

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_FILE = SKILL_DIR / "assets" / "previews" / "page.html"

PLACEMENTS = ("search_desktop", "search_mobile", "network", "product_gallery")

# Имена правил справочника для полей комплекта. Второго перечня не заводится —
# те же имена знает `combinatorial`.
TITLE_FIELD = "ResponsiveAd.Titles.item"
TEXT_FIELD = "ResponsiveAd.Texts.item"
DISPLAY_FIELD = "DisplayUrlPath"
SITELINK_FIELD = "Sitelink.Title"
SITELINK_TEXT_FIELD = "Sitelink.Description"
CALLOUT_FIELD = "AdExtension.Callout.CalloutText"

SLOT_LIMITS = {
    "title": TITLE_FIELD,
    "text": TEXT_FIELD,
    "link": DISPLAY_FIELD,
    "sitelinks": SITELINK_FIELD,
    "callouts": CALLOUT_FIELD,
    # Визитка — не поле Директа: строку про город, телефон и часы собирает
    # человек, а сам Директ показывает привязанную организацию. Длины у неё
    # поэтому нет, и обрезать её не по чему.
    "vcard": None,
    # Цену Директ берёт из фида или из расширения «цена в объявлении», и
    # ни того, ни другого скилл не пишет.
    "price": None,
    # Не текст вовсе.
    "image": None,
    # Пометку «Реклама» ставит сам Директ (`AD_CONTENT.md`, раздел 8).
    "label": None,
}

# Правило описания быстрой ссылки стоит отдельно: у слота `sitelinks` два
# ограниченных значения, а в таблице выше — по одному на слот.
NESTED_LIMITS = {"sitelinks": SITELINK_TEXT_FIELD}

# Сколько элементов Директ примет, и где справочник это говорит: пара «раздел,
# ключ». Разделы разные не по недосмотру — состав набора быстрых ссылок
# описан коллекцией, а предел уточнений на объявление лежит среди количеств
# объектов, и сводить их в один раздел значило бы переписать справочник.
#
# Проверять состав приходится **здесь**: расширения приходят только в превью,
# и `ads_generate.py` их не читает вовсе. Обычное правило «вердикт даёт разбор
# комплекта» на них не распространяется, потому что разбирать их некому.
SLOT_COLLECTIONS = {
    "sitelinks": ("collections", "SitelinksSet.Sitelinks"),
    "callouts": ("objects", "callouts_per_ad"),
    "image": ("collections", "ResponsiveAd.AdImageHashes"),
}

# Поля расширений, которые Директ проверяет, а показать не показывает. Хвоста
# у них не бывает — зачёркивать нечего, — но предел есть, и превышение
# называется в отчёте: выброшенный при разборе адрес был бы неотличим от
# незаданного.
HIDDEN_FIELDS = {"sitelink_href": "Sitelink.Href"}


def collection_limit(limits: Limits, where) -> int:
    """Предел состава по паре «раздел, ключ». `None` — справочник молчит."""
    section, key = where
    found = (limits.data.get(section) or {}).get(key)
    if isinstance(found, dict):
        found = found.get("max")
    return found if isinstance(found, int) else None


# --------------------------------------------------------------------------
# Объявление глазами человека
# --------------------------------------------------------------------------

class Sitelink:
    """Быстрая ссылка: заголовок, необязательное описание, адрес.

    Адрес не показывается — Директ показывает текст ссылки, — но проверяется:
    предел у него свой, и превышение называется в отчёте."""

    __slots__ = ("title", "description", "href")

    def __init__(self, title: str, description=None, href=None):
        self.title = str(title)
        self.description = None if description is None else str(description)
        self.href = None if href is None else str(href)

    def __repr__(self) -> str:
        return f"<Sitelink {excerpt(self.title, 30)}>"


class Ad:
    """Объявление глазами человека: комплект плюс всё, что покажется рядом."""

    __slots__ = ("kit", "sitelinks", "callouts", "vcard", "price", "domain")

    def __init__(self, kit, *, sitelinks=(), callouts=(), vcard=None,
                 price=None):
        self.kit = kit
        self.sitelinks = list(sitelinks)
        self.callouts = [str(one) for one in callouts]
        self.vcard = None if vcard is None else str(vcard)
        self.price = None if price is None else str(price)
        self.domain = domain_of(kit.href)


def domain_of(href) -> str:
    """Домен из ссылки объявления — то, что человек видит в выдаче.

    Пустая строка означает, что домена нет. Подставлять на это место
    правдоподобный `example.com` нельзя: человек согласовал бы цель, которой
    не просил, а Директ такое объявление и не примет — ему нужна `Href` либо
    `BusinessId`, и второго скилл не пишет."""
    said = str(href or "")
    # Без `//` разбор считает адрес путём, и «example.com/x» осталось бы без
    # домена вовсе. Протокол Директ требует, но сказать об этом — работа
    # валидации, а не картинки: рисуется то, что человек написал.
    if "//" not in said:
        said = "//" + said
    try:
        return urlsplit(said).hostname or ""
    except ValueError:
        return ""


# Что Директ делает с отображаемой ссылкой перед показом: пробел и `_`
# заменяются на `-`, повторы `-`, `№`, `/`, `%` схлопываются в один, остальные
# символы удаляются. Справка «Отображаемая ссылка», сверено 02.09.2026.
_DISPLAY_REPEATS = re.compile(r"([-№/%])\1+")
_DISPLAY_SPACE = re.compile(r"[\s_]")


def display_path(said: str, limits: Limits) -> str:
    """Отображаемая ссылка так, как её приведёт Директ.

    Что считается буквой, спрашивает справочник — тем же признаком, которым
    валидация решает, принять значение или отвергнуть. Перечень латиницы с
    кириллицей выражением выбрасывал `é` и `қ`: `café` показывался как `caf`,
    хотя валидация его принимает, — то есть превью рисовало не тот адрес, по
    которому человек согласовывает объявление."""
    marks = limits.marks(DISPLAY_FIELD)
    said = _DISPLAY_SPACE.sub("-", str(said))
    said = "".join(one for one in said if one.isalnum() or one in marks)
    return _DISPLAY_REPEATS.sub(r"\1", said)


# --------------------------------------------------------------------------
# Куски текста, готовые к отрисовке
# --------------------------------------------------------------------------

class Piece:
    """Значение поля, разобранное по пределу Директа.

    `kept` — то, что Директ примет; `dropped` — хвост за пределом. Хвост не
    выбрасывается: он рисуется зачёркнутым и попадает в отчёт, потому что
    новость «здесь текст кончится» и есть то, ради чего превью существует."""

    __slots__ = ("field", "kept", "dropped", "said")

    def __init__(self, field: str, kept: str, dropped: str, said: str):
        self.field, self.kept = field, kept
        self.dropped, self.said = dropped, said

    @property
    def whole(self) -> str:
        return self.kept + self.dropped

    def __repr__(self) -> str:
        return f"<Piece {self.said}: {excerpt(self.kept, 30)}>"


def piece(limits: Limits, field: str, value, said: str) -> Piece:
    """Значение, обрезанное по пределу поля из справочника."""
    kept, dropped = limits.cut(field, str(value))
    return Piece(field, kept, dropped, said)


def cut_note(one: Piece) -> str:
    """Замечание об отрезанном хвосте — одними словами у всех, кто его даёт.

    Слова общие потому, что сводит замечания `summarise` по их тексту: две
    редакции одной и той же жалобы не сойдутся и напечатаются порознь."""
    return (f"{one.said} «{excerpt(one.whole, 40)}»: за пределом Директа "
            f"осталось {len(one.dropped)} символов — "
            f"«{excerpt(one.dropped, 40)}»")


# --------------------------------------------------------------------------
# Шаблон страницы
# --------------------------------------------------------------------------

THEMES = ("auto", "light", "dark")

SLOT = re.compile(r"\{\{(\w+)\}\}")

_TEMPLATE = re.compile(
    r"[ \t]*<template\s+([^>]*?)>\n(.*?)\n[ \t]*</template>\n?", re.DOTALL)
_ATTRIBUTE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')
_COMMENT = re.compile(r"<!--.*?-->\n?", re.DOTALL)


class Placement:
    """Место показа: имя, подпись, ширина карточки и её разметка."""

    __slots__ = ("name", "title", "width", "callouts", "markup", "slots")

    def __init__(self, name, title, width, callouts, markup):
        self.name, self.title = name, title
        self.width, self.callouts = width, callouts
        self.markup = markup
        self.slots = tuple(dict.fromkeys(SLOT.findall(markup)))

    def shows(self, slot: str) -> bool:
        return slot in self.slots

    def __repr__(self) -> str:
        return f"<Placement {self.name}: {', '.join(self.slots)}>"


def _page_source() -> str:
    """Шаблон страницы с диска. Это ассет скилла, а не чужой ввод."""
    try:
        # свой файл читается напрямую:
        return TEMPLATE_FILE.read_text(encoding="utf-8")
    except OSError as failure:
        raise DirectFailure(
            f"Шаблон страницы {TEMPLATE_FILE.name} не прочитался: "
            f"{failure.strerror}. Без него рисовать нечем.") from failure


def skeleton() -> str:
    """Страница без шаблонов карточек и без заметок для правящего файл.

    Ни то, ни другое человеку, который согласовывает объявления, не нужно:
    шаблон с неподставленными `{{…}}` он прочтёт как часть объявления, а
    заметка о правилах подстановки адресована не ему."""
    return _COMMENT.sub("", _TEMPLATE.sub("", _page_source()))


def load(name: str) -> Placement:
    """Место показа по имени. Незнакомое имя — отказ, а не пустая карточка."""
    if name not in PLACEMENTS:
        raise DirectFailure(
            f"Места показа «{excerpt(name, 30)}» нет. Известны: "
            f"{', '.join(PLACEMENTS)}.")
    for attributes, markup in _TEMPLATE.findall(_page_source()):
        said = dict(_ATTRIBUTE.findall(attributes))
        if said.get("data-placement") != name:
            continue
        missing = [one for one in ("data-title", "data-width", "data-callouts")
                   if not said.get(one)]
        if missing:
            raise DirectFailure(
                f"Шаблон {name} не объявляет {', '.join(missing)}. Карточка "
                f"без подписи или ширины нарисовалась бы неизвестно какой.")
        return Placement(name, said["data-title"], said["data-width"],
                         said["data-callouts"], markup)
    raise DirectFailure(
        f"В {TEMPLATE_FILE.name} нет шаблона карточки для места показа "
        f"«{name}», хотя перечень мест его называет.")


def fill(markup: str, values: dict) -> str:
    """Разметка с подставленными значениями.

    Правило одно: строка с пустым значением выбрасывается целиком. Пустая
    визитка не должна оставлять после себя пустой абзац, а условие на каждый
    элемент в коде — это десяток условий, расходящихся с разметкой."""
    kept = []
    for line in markup.split("\n"):
        names = SLOT.findall(line)
        if names and not all(values.get(one) for one in names):
            continue
        kept.append(SLOT.sub(lambda found: values.get(found.group(1), ""),
                             line))
    return "\n".join(kept)


class Card:
    """Готовая карточка: разметка и замечания о ней."""

    __slots__ = ("body", "notes")

    def __init__(self, body: str, notes):
        self.body, self.notes = body, list(notes)


def _said(text) -> str:
    """Текст человека внутри разметки. Экранируется весь, без исключений."""
    return html.escape(str(text), quote=False)


def _marked(one: Piece) -> str:
    """Значение с зачёркнутым хвостом за пределом поля."""
    if not one.dropped:
        return _said(one.kept)
    return f'{_said(one.kept)}<s class="cut">{_said(one.dropped)}</s>'


def callout_line(limits: Limits, placement: Placement, callouts) -> tuple:
    """Уточнения, которые поместятся в строку показа: пара «показанные, нет».

    Числа опубликованы, и потому это единственное обрезание местом показа,
    которое превью себе позволяет: «отображается столько уточнений, сколько
    умещается в строку 66 символов», сумма — не более 132 символов на
    десктопах и 76 на мобильных (`limits.json`, `text.callouts_total`).

    В 66 символов считаются сами уточнения; про разделители между ними справка
    не говорит, и приписывать их к счёту значило бы показать на одно уточнение
    меньше по собственной догадке.

    Считается **принятое** Директом (`kept`), а не написанное: правило описывает
    показ, а показать он может только то, что принял. Хвост при этом остаётся
    при своём уточнении и рисуется зачёркнутым — выбросить его значило бы
    выдать негодное уточнение за годное и короткое."""
    rule = (limits.data.get("text") or {}).get("callouts_total") or {}
    line = rule.get("single_line")
    if placement.callouts == "none" or line is None:
        return list(callouts), []
    shown, spent = [], 0
    for one in callouts:
        if spent + len(one.kept) > line:
            break
        shown.append(one)
        spent += len(one.kept)
    return shown, list(callouts[len(shown):])


def callout_total(limits: Limits, placement: Placement) -> tuple:
    """Предел суммы уточнений для этого плейсмента: пара «предел, чей»."""
    rule = (limits.data.get("text") or {}).get("callouts_total") or {}
    return rule.get(placement.callouts), placement.callouts


def _image(kit) -> str:
    """Подпись места изображения. Само оно не рисуется — и это не пробел.

    Скачать его значило бы сходить наружу, а встроить скачанное — положить в
    файл мегабайты и потерять переносимость. Рисуется место и подпись с хешем:
    состав объявления видно, а картинку человек и так знает."""
    hashes = kit.images if isinstance(kit.images, list) else []
    if not hashes:
        return "изображение не задано"
    if len(hashes) == 1:
        return f"изображение {_said(hashes[0])}"
    return f"изображений {len(hashes)}, первое {_said(hashes[0])}"


# Откуда берётся состав и как он зовётся человеку. Таблица рядом с пределами,
# а не ветка в коде: состав, о котором забыли, молчит ровно так же, как
# состав, у которого предела нет, — и отличить одно от другого нельзя.
COLLECTION_OF = {
    "sitelinks": (lambda ad: ad.sitelinks, "быстрых ссылок"),
    "callouts": (lambda ad: ad.callouts, "уточнений"),
    "image": (lambda ad: ad.kit.images if isinstance(ad.kit.images, list)
              else [], "изображений"),
}


def card(placement: Placement, ad: Ad, title: str, text: str, *,
         limits: Limits) -> Card:
    """Карточка одной пары «заголовок × текст» для этого места показа."""
    kit = ad.kit
    notes = []
    pieces = {
        "title": piece(limits, TITLE_FIELD, title, "заголовок"),
        "text": piece(limits, TEXT_FIELD, text, "текст"),
    }

    # Состав расширений — тоже предел Директа, и проверить его больше некому:
    # `ads_generate.py` быстрых ссылок и уточнений не читает. Это замечание, а
    # не отказ: девять ссылок нарисуются все, и решать, какую убрать, человеку
    # проще по картинке, чем по числу.
    for slot, where in SLOT_COLLECTIONS.items():
        given, said = COLLECTION_OF[slot]
        count = len(given(ad))
        limit = collection_limit(limits, where)
        if limit is not None and count > limit:
            notes.append(
                f"{said} {count} при пределе {limit} — Директ примет не "
                f"больше")

    # Отображаемая ссылка: заданная либо подставленный Директом заголовок.
    if kit.display_url_path:
        shown, auto = display_path(kit.display_url_path, limits), False
    else:
        shown, auto = display_path(pieces["title"].whole, limits), True
    link = piece(limits, DISPLAY_FIELD, shown, "отображаемая ссылка")
    if auto and link.whole:
        notes.append(
            "отображаемая ссылка не задана — Директ подставит в неё заголовок "
            "(справка «Отображаемая ссылка»); обрезана она здесь по пределу "
            "поля в 20 символов, про этот случай справка не говорит")
    if not ad.domain:
        # Ссылки нет — и подставлять на её место правдоподобный домен нельзя:
        # человек согласовал бы цель, которой не просил.
        notes.append("ссылка объявления не задана — Директ такое объявление "
                     "не примет")

    sitelink_titles = [piece(limits, SITELINK_FIELD, one.title,
                             "быстрая ссылка") for one in ad.sitelinks]
    sitelink_texts = [
        None if one.description is None
        else piece(limits, SITELINK_TEXT_FIELD, one.description,
                   "описание быстрой ссылки")
        for one in ad.sitelinks]
    callouts = [piece(limits, CALLOUT_FIELD, one, "уточнение")
                for one in ad.callouts]
    # Адрес быстрой ссылки Директ проверяет, но не показывает: зачёркивать
    # нечего, а сказать есть о чём. Проверяет его тот же справочник, что и
    # запись, — второго вердикта по одному значению не заводится.
    for number, one in enumerate(ad.sitelinks, start=1):
        if one.href is None:
            continue
        for said in limits.text_problems(HIDDEN_FIELDS["sitelink_href"],
                                         one.href):
            notes.append(f"адрес быстрой ссылки {number}: {said}")

    checked = ([pieces["title"], pieces["text"], link]
               + sitelink_titles
               + [one for one in sitelink_texts if one is not None]
               + callouts)

    # Отбор уточнений в строку показа идёт по **принятому** Директом: правило
    # про 66 символов описывает показ, а показать он может только то, что
    # принял. Хвост при этом не выбрасывается — он рисуется рядом зачёркнутым,
    # иначе негодное уточнение выглядит годным и коротким.
    line, hidden = callout_line(limits, placement, callouts)
    if placement.shows("callouts") and callouts:
        if hidden:
            notes.append(
                f"уточнений не поместилось в строку показа: {len(hidden)} "
                f"(показывается столько, сколько умещается в 66 символов)")
        limit, whose = callout_total(limits, placement)
        spent = sum(len(one.kept) for one in callouts)
        if limit is not None and spent > limit:
            notes.append(
                f"сумма уточнений {spent} при пределе {limit} для места "
                f"«{whose}»")

    path = f"/{link.kept}" if link.kept else ""
    tail = (f'<s class="cut">{_said(link.dropped)}</s>' if link.dropped
            else "")
    values = {
        "label": "Реклама",
        "title": _marked(pieces["title"]),
        "text": _marked(pieces["text"]),
        "link": f"{_said(ad.domain + path)}{tail}" if ad.domain
                else "ссылки нет",
        "sitelinks": "".join(
            f'<li><span class="sitelink">{_marked(one)}</span>'
            + (f'<span class="sitetext">{_marked(two)}</span>'
               if two is not None else "")
            + "</li>"
            for one, two in zip(sitelink_titles, sitelink_texts)),
        "callouts": "".join(f'<span class="callout">{_marked(one)}</span>'
                            for one in line),
        "vcard": _said(ad.vcard or ""),
        "price": _said(ad.price or ""),
        "image": _image(kit),
    }
    unknown = [one for one in placement.slots if one not in values]
    if unknown:
        raise DirectFailure(
            f"Шаблон {placement.name} объявляет место «{unknown[0]}», "
            f"которого отрисовка не знает. Место, которое молча не "
            f"заполняется, — это пропавший элемент объявления.")

    # Замечание об обрезании говорится про всё, у чего предел есть. Зачёркнутый
    # хвост рисуется там, где элемент показан, — страница показывает это место
    # показа; замечание же про Директ, а он один на все места.
    notes += [cut_note(one) for one in checked if one.dropped]
    return Card(fill(placement.markup, values), notes)


# --------------------------------------------------------------------------
# Матрица
# --------------------------------------------------------------------------

def matrix_html(placement: Placement, ad: Ad, pairs, *, theme: str = "auto",
                limits=None, heading: str = "", footer=()) -> tuple:
    """Страница с матрицей карточек: строки — заголовки, столбцы — тексты.

    Пары приходят готовыми (`combinatorial.matrix`), а не собираются здесь:
    второе построение матрицы разошлось бы с первым ровно тогда, когда состав
    комплекта изменится, и превью показало бы не тот набор, который проверил
    разбор.

    Возвращает пару «страница, сведённые замечания»."""
    if theme not in THEMES:
        raise DirectFailure(
            f"Темы «{excerpt(theme, 20)}» нет. Известны: {', '.join(THEMES)}.")
    limits = limits or Limits.load()
    pairs = list(pairs)
    if not pairs:
        raise DirectFailure(
            "Матрица пуста: рисовать нечего. Комплект без заголовков или без "
            "текстов Директ не принимает, а пустая страница выглядела бы "
            "проверенной.")
    footer = list(footer)
    skipped = {}
    if not placement.shows("text"):
        seen = {}
        for one in pairs:
            seen.setdefault(one.title_place, one)
        kept = list(seen.values())
        if len(kept) < len(pairs):
            drawn_places = {one.text_place for one in kept}
            skipped = {one.text_place: one.text for one in pairs
                       if one.text_place not in drawn_places}
            footer.append(
                f"{placement.title} текста объявления не показывает: на все "
                f"тексты комплекта карточка одна, и нарисовано по одной на "
                f"заголовок.")
        pairs = kept
        at = {(one.title_place, None): one for one in pairs}
    else:
        at = {(one.title_place, one.text_place): one for one in pairs}
    titles = sorted({one for one, _ in at})
    texts = sorted({two for _, two in at})

    notes, cells = [], {}
    for key, pair in at.items():
        drawn = card(placement, ad, pair.title, pair.text, limits=limits)
        cells[key] = drawn.body
        notes += [(key, one) for one in drawn.notes]

    for place, text in sorted(skipped.items()):
        cut = piece(limits, TEXT_FIELD, text, "текст")
        if cut.dropped:
            # Заголовка у этого замечания нет: схлопнутый текст не нарисован
            # ни с одним из них.
            notes.append(((None, place), cut_note(cut)))

    shows_text = placement.shows("text")
    grid = [f'<div class="matrix" style="--columns: {len(texts)}; '
            f'--width: {placement.width}">']
    if shows_text:
        grid.append('<div class="corner"></div>')
        grid += [f'<div class="head">текст {one}</div>' for one in texts]
    for title in titles:
        grid.append(f'<div class="rowlabel">загл. {title}</div>')
        for text in texts:
            body = cells.get((title, text), "")
            grid.append(f'<div class="cell">{body}</div>')
    grid.append("</div>")

    said = (f"{placement.title} · "
            + (f"комбинаций {len(pairs)}" if shows_text
               else f"карточек {len(pairs)}"))
    # Сводятся замечания здесь, а не у вызывающего: число нарисованных карточек
    # знает только эта функция. Товарная галерея схлопывает двадцать одну пару
    # в семь карточек, и «во всех парах» считалось снаружи от двадцати одной.
    lines = summarise(notes, len(cells))
    parts = {
        "theme": "" if theme == "auto" else f' data-theme="{theme}"',
        "heading": _said(heading or placement.title),
        "subtitle": _said(said),
        "matrix": "\n".join(grid),
        "footer": "\n".join(f"<p>{_said(one)}</p>" for one in footer),
    }
    # Страница подставляется без правила «пустое выбрасывает строку»: пустая
    # тема — это `auto`, самый обычный случай, а строка с ней несёт открывающий
    # тег документа.
    page = SLOT.sub(lambda found: parts.get(found.group(1), ""), skeleton())
    return page, lines


def summarise(notes, total: int) -> list:
    """Замечания карточек, сведённые к строкам для человека.

    Одно и то же замечание приходит от каждой пары, где оно верно, и печатать
    его двадцать один раз нельзя: длина заголовка не зависит от текста, с
    которым его показали, и повтор превращает отчёт в стену, поверх которой
    перестают читать. Поэтому одинаковые сводятся, а адрес называется — целиком
    («во всех парах») либо перечнем мест."""
    where = {}
    for key, said in notes:
        where.setdefault(said, []).append(key)
    lines = []
    for said, keys in where.items():
        titles = sorted({one for one, _ in keys if one is not None})
        texts = sorted({two for _, two in keys if two is not None})
        if not texts:
            lines.append(f"заголовок {titles[0]}: {said}" if len(titles) == 1
                         else (f"во всех карточках: {said}"
                               if len(titles) >= total
                               else f"заголовки {_places(titles)}: {said}"))
            continue
        if not titles:
            lines.append(f"текст {texts[0]}: {said}" if len(texts) == 1
                         else f"тексты {_places(texts)}: {said}")
            continue
        if len(keys) >= total:
            # «Во всех парах» при одной паре — правда, которая ничего не
            # сообщает: разбор одной комбинации затем и зовут, чтобы услышать
            # про неё, а не про множество, в котором она единственная.
            lines.append(f"пара {keys[0][0]} × {keys[0][1]}: {said}"
                         if total == 1 else f"во всех парах: {said}")
            continue
        if len(keys) == len(titles) * len(texts) and len(titles) == 1:
            address = f"заголовок {titles[0]}"
        elif len(keys) == len(titles) * len(texts) and len(texts) == 1:
            address = f"текст {texts[0]}"
        else:
            shown = ", ".join(f"{one} × {two}" for one, two in sorted(keys)[:6])
            more = "" if len(keys) <= 6 else f" и ещё {len(keys) - 6}"
            address = f"пары {shown}{more}"
        lines.append(f"{address}: {said}")
    return lines


def _places(numbers) -> str:
    """Перечень мест комплекта: до шести, дальше числом."""
    shown = ", ".join(str(one) for one in numbers[:6])
    return shown if len(numbers) <= 6 else f"{shown} и ещё {len(numbers) - 6}"


# --------------------------------------------------------------------------
# Самодостаточность
# --------------------------------------------------------------------------

# Элементы, которые тянут содержимое извне по самому своему смыслу.
_OUTSIDE_TAGS = {"img", "script", "iframe", "object", "embed", "link",
                 "source", "video", "audio", "picture", "svg", "use", "a"}

# Атрибуты, значение которых — адрес. `href` в этом перечне не по ошибке:
# ссылок в превью нет вовсе, и появившаяся означала бы либо адрес наружу, либо
# клик из документа, который человек считает картинкой.
_ADDRESS_ATTRIBUTES = {"href", "src", "srcset", "data", "poster",
                       "background", "action", "formaction"}

# Чем стиль уходит наружу: чужая таблица, чужой шрифт, чужой ресурс в `url()`.
_OUTSIDE_STYLE = (
    (re.compile(r"@import\b", re.I), "@import в стилях"),
    (re.compile(r"@font-face\b", re.I), "@font-face"),
    (re.compile(r"url\(\s*['\"]?(?!#|data:)", re.I), "url() наружу"),
)


class _Scan(HTMLParser):
    """Разбор готовой страницы: имена элементов, адреса, содержимое стилей."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []
        self._style = False

    def handle_starttag(self, tag, attrs):
        if tag in _OUTSIDE_TAGS:
            self.found.append(f"элемент <{tag}>")
        self._style = tag == "style"
        for name, value in attrs:
            said, value = (name or "").lower(), value or ""
            if said in _ADDRESS_ATTRIBUTES:
                self.found.append(f"{said} наружу: {excerpt(value, 60)}")
            elif said == "style":
                self._look(value, "в атрибуте style")

    def handle_endtag(self, tag):
        self._style = False

    def handle_data(self, data):
        if self._style:
            self._look(data, "в <style>")

    def _look(self, said: str, where: str):
        for pattern, name in _OUTSIDE_STYLE:
            if pattern.search(said):
                self.found.append(f"{name} {where}")


def external_references(page: str) -> list:
    """Ссылки наружу, найденные в готовом файле. Пусто — файл самодостаточен.

    Разбирается **строение**, а не текст файла. Поиск по строке отказывал на
    законном объявлении: заголовок «Скидка по ссылке url(example.com)» уезжает
    в текстовый узел, скачать по нему нельзя ничего, — а проверка находила
    `url(`, и команда отказывалась записать превью. То же ждало объявление со
    словами `src=`, `@import` и `href=`: угловые скобки в тексте
    экранируются, а эти — нет. Ошибка тихая с другой стороны: человек видит
    отказ и не понимает, при чём тут его текст.

    Поэтому содержимое текстовых узлов не проверяется вовсе — по нему нельзя
    сходить наружу, — а проверяются имена элементов, значения атрибутов и
    содержимое `<style>`."""
    scan = _Scan()
    scan.feed(page)
    scan.close()
    return scan.found
