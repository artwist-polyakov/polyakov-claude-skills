"""Шаблоны подстановки, параметры фразы и макросы: разбор и длины после подстановки."""

from __future__ import annotations

import re
from urllib.parse import quote

from config import DirectFailure, excerpt

# Ограничитель шаблона. Символ один и тот же с обеих сторон, поэтому
# «незакрытого» шаблона не бывает — бывает непарная решётка.
HASH = "#"

# Параметры уровня фразы. Значения живут в `Keyword.UserParam1` и `UserParam2`
# и задаются на фразу, а не на объявление: одно объявление показывается по
# многим фразам, и у каждой значение своё.
PHRASE_PARAMS = ("param1", "param2")

MACROS = frozenset({
    "ad_id", "banner_id", "campaign_name", "campaign_name_lat",
    "campaign_type", "campaign_id", "creative_id", "device_type", "gbid",
    "keyword", "phrase_id", "retargeting_id", "coef_goal_context_id",
    "match_type", "matched_keyword", "adtarget_name", "adtarget_id",
    "position", "position_type", "source", "source_type", "region_name",
    "region_id", "yclid",
})

# Макросы, которые справка приводит с собственной пометкой «параметр больше не
# поддерживается». Из перечня они не убраны намеренно: встреченные в чужой
# ссылке, они опечаткой не являются и требуют другого разговора с человеком.
WITHDRAWN_MACROS = frozenset({"adtarget_name"})

# Поля, значение которых Директ считает адресом. Решётка в них двусмысленна:
# она и ограничитель шаблона, и разделитель якоря (`naming.md`, `UTM-05`).
URL_FIELDS = frozenset({"Href", "Sitelink.Href"})

# Как поле зовётся человеку. Только для сообщений: границу подстановки задаёт
# справочник лимитов, а не этот перечень.
FIELD_NAMES = {
    "ResponsiveAd.Titles.item": "заголовок",
    "ResponsiveAd.Texts.item": "текст",
    "Href": "ссылка",
    "DisplayUrlPath": "отображаемая ссылка",
    "Sitelink.Title": "заголовок быстрой ссылки",
    "Sitelink.Description": "описание быстрой ссылки",
    "Sitelink.Href": "адрес быстрой ссылки",
}

# Предел длины адреса, за которым Директ передаёт значения только для `yclid` и
# openstat (`url_macros.md`, раздел 5). Считается в **байтах**: кириллица
# кодируется в UTF-8, и символов в таком адресе заметно меньше. Отваливается не
# длинный хвост разметки, а все макросы разом.
URL_BYTES_MAX = 4096

# Виды токенов в фигурных скобках.
MACRO = "macro"
PARAM = "param"
UNKNOWN = "unknown"

_TOKEN = re.compile(r"\{([^{}]*)\}")


# --------------------------------------------------------------------------
# Справочник лимитов
# --------------------------------------------------------------------------

def limits_for(limits=None):
    """Справочник лимитов: переданный или загруженный по требованию."""
    if limits is not None:
        return limits
    from writer import Limits

    return Limits.load()


def rule_for(field: str, limits=None) -> dict:
    """Правило поля из справочника. Незнакомое имя — отказ, а не пропуск.

    Слово в слово причина та же, что у `writer.Limits.text_problems`: проверка,
    молча пропускающая незнакомое имя поля, не проверяет ничего, а опечатка в
    имени неотличима от поля, у которого правил нет."""
    rule = limits_for(limits).texts.get(field)
    if rule is None:
        raise DirectFailure(
            f"В справочнике лимитов нет поля «{excerpt(field, 64)}», по "
            f"которому просили разобрать шаблон. Разбор, молча пропускающий "
            f"незнакомое имя, не проверяет ничего."
        )
    return rule


def substitutes(field: str, limits=None) -> bool:
    """Подставляет ли Директ ключевую фразу в это поле.

    Ответ берётся из справочника: у поля, где шаблон работает, решётки не
    входят в длину (`template_hash_excluded`), потому что они — ограничители, а
    не текст. Там, где флага нет, решётка считается наравне с буквами, то есть
    она обыкновенный символ, а шаблона в поле не существует."""
    return bool(rule_for(field, limits).get("template_hash_excluded"))


# --------------------------------------------------------------------------
# Фраза набора данных
# --------------------------------------------------------------------------

def positive(text: str) -> str:
    """Что подставляется вместо шаблона: фраза без минус-слов и операторов.

    Регистр сохраняется — «регистр букв в ключевой фразе не меняется при
    подстановке» (справка). Поэтому `phrases.bare`, приводящий слово к нижнему
    регистру для сравнения со словарём, здесь не годится: он отвечает на другой
    вопрос.

    Основание модели разобрано в шапке модуля: справка о шаблоне про минус-слова
    молчит, у макроса `{keyword}` сказано «без минус-слов», и здесь взято то же."""
    from phrases import OPERATORS, split_phrase

    words, _minuses = split_phrase(text)
    kept = [word.strip(OPERATORS) for word in words]
    return " ".join(word for word in kept if word)


def encoded(text: str) -> str:
    """Фраза, подставленная в адрес: пробел — `%20`, кириллица — UTF-8.

    Обе замены названы справкой прямо. `safe=""` кодирует и `/`, и `?`: фраза
    попадает в адрес как значение, а не как его разметка, и слеш из фразы,
    оставленный собой, увёл бы посетителя на другой путь сайта."""
    return quote(text, safe="")


class Phrase:
    """Фраза набора данных: текст, что подставится и параметры уровня фразы.

    Две строки, а не одна, потому что вопросов тоже два. `text` отвечает «что
    записано в кабинете» — по нему фраза ищется в `Keywords.get` и узнаётся
    человеком. `shown` отвечает «что подставится» — по нему считается длина.
    Свести их в одну значило бы либо считать длину по операторам, которых в
    показе нет, либо потерять фразу, которую человек ищет глазами."""

    __slots__ = ("text", "shown", "param1", "param2", "identifier",
                 "unmeasurable")

    def __init__(self, text, *, param1=None, param2=None, identifier=None):
        self.text = str(text)
        self.shown = positive(self.text)
        self.unmeasurable = _unmeasurable(self.text)
        self.param1 = None if param1 is None else str(param1)
        self.param2 = None if param2 is None else str(param2)
        self.identifier = identifier
        if not self.shown.strip():
            raise DirectFailure(
                f"Фраза «{excerpt(text, 60)}» состоит из одних операторов и "
                f"минус-слов: подставлять вместо шаблона нечего."
            )

    @classmethod
    def of(cls, record: dict) -> "Phrase":
        """Фраза из записи `Keywords.get`.

        Автотаргетинг сюда не попадает: у него нет текста фразы, и подставлять
        вместо шаблона нечего. Отбирает его вызывающий код —
        `phrases.is_autotargeting` умеет это по тому же ответу."""
        return cls(record.get("Keyword") or "",
                   param1=record.get("UserParam1"),
                   param2=record.get("UserParam2"),
                   identifier=record.get("Id"))

    def param(self, name: str):
        return self.param1 if name == "param1" else self.param2

    @property
    def measurable(self) -> bool:
        return self.unmeasurable is None

    def __repr__(self) -> str:
        return f"<Phrase {excerpt(self.text, 40)!r}>"


def _unmeasurable(text: str):
    """Почему по этой фразе длину подстановки посчитать нельзя. `None` — можно.

    Случай пока один — OR-последовательность вида `(а|б)`. Правило `KW-11`
    плейбука: в интерфейсе такая запись не работает, в выгрузке Excel работает,
    а как отвечает `Keywords.add`, не проверено ни на одном кабинете. Писать их
    скилл не станет (`phrases.fits_keyword`), но **читает и сохраняет как есть**
    — значит `Keywords.get` такую фразу вернуть может, и в набор она попадёт.

    Что подставится вместо шаблона при показе по такой фразе, не знает никто:
    неизвестно даже, состоится ли показ. Считать длину по строке со скобками и
    чертой значило бы померить то, чего человек не увидит, и выдать это за
    проверку. Отсюда не отказ и не молчание, а третий ответ — «проверить
    нельзя»: набор проверяется дальше, а по этой фразе отчёт говорит прямо.

    Признак берётся у `phrases`, а не пишется здесь второй раз: разошедшись,
    два выражения одного правила разойдутся молча."""
    from phrases import OR_SEQUENCE

    if not OR_SEQUENCE.search(text):
        return None
    return (
        "фраза содержит OR-последовательность вида `(а|б)` — что подставится "
        "вместо шаблона, неизвестно. Длина по этой фразе не рассчитана."
    )


def phrases_of(records) -> list:
    """Набор фраз из ответа `Keywords.get`, без автотаргетинга.

    Отбор здесь, а не у вызывающего: автотаргетинг приходит тем же массивом,
    что и фразы, и забытая проверка превращается в `Phrase` без текста —
    отказом посреди сборки предпросмотра, а не понятной строкой."""
    from phrases import is_autotargeting

    return [Phrase.of(one) for one in records if not is_autotargeting(one)]


# --------------------------------------------------------------------------
# Разбор шаблона
# --------------------------------------------------------------------------

class Span:
    """Один шаблон внутри значения: границы и текст по умолчанию."""

    __slots__ = ("start", "end", "default")

    def __init__(self, start: int, end: int, default: str):
        self.start, self.end, self.default = start, end, default

    def __repr__(self) -> str:
        return f"<Span {self.start}:{self.end} {self.default!r}>"


class Template:
    """Разобранное значение поля: строка, шаблоны в ней и что с ними не так.

    Разбор **терпимый**: негодное значение не роняет разбор исключением, а
    записывает замечание в `problems`. Причина не в мягкости. Человеку
    показывают предпросмотр целиком — все семь заголовков и три текста разом, —
    и разбор, падающий на первом же испорченном поле, превращает один показ в
    семь заходов, на каждом из которых видно по одной беде.

    Отказом остаётся ровно одно — незнакомое имя поля (`rule_for`): это ошибка
    вызывающего кода, а не данных, и показывать её человеку незачем."""

    __slots__ = ("field", "source", "spans", "anchor", "problems")

    def __init__(self, field: str, source: str, spans, anchor, problems):
        self.field = field
        self.source = source
        self.spans = list(spans)
        # Позиция непарной решётки в адресе — та, которую мы читаем как якорь.
        self.anchor = anchor
        self.problems = list(problems)

    # -- разбор -------------------------------------------------------------

    @classmethod
    def parse(cls, field: str, value, *, limits=None) -> "Template":
        """Разобрать значение поля. Решётки парятся слева направо.

        Пары — первая со второй, третья с четвёртой. Иначе быть не может:
        ограничитель у шаблона один и тот же с обеих сторон, и вложенности он не
        выражает. Оставшаяся непарная решётка означает разное в разных полях,
        и это единственное место, где поле на разбор влияет."""
        allowed = substitutes(field, limits)
        text = "" if value is None else str(value)
        places = [index for index, one in enumerate(text) if one == HASH]
        problems, spans = [], []
        anchor = None
        for number, (left, right) in enumerate(
                zip(places[::2], places[1::2]), start=1):
            default = text[left + 1:right]
            spans.append(Span(left, right, default))
            # Номер шаблона называется всегда, даже когда он один. В поле их
            # бывает несколько, и два одинаковых замечания без номера читаются
            # как одно повторённое — а чинить надо оба.
            said = f"шаблон {number}"
            if not default.strip():
                problems.append(
                    f"{said} без текста по умолчанию: `##` показывает пустое "
                    f"место, когда подстановка не помещается"
                )
            elif _looks_like_another_mechanism(default):
                problems.append(
                    f"{said}: текст по умолчанию «{excerpt(default, 40)}» "
                    f"похож на макрос или параметр фразы, а между решётками "
                    f"пишется то, что человек увидит вместо фразы"
                )
        if len(places) % 2:
            anchor = places[-1]
            if field not in URL_FIELDS:
                problems.append(
                    "непарная решётка: у шаблона ограничитель один и тот же с "
                    "обеих сторон, и одиночная решётка остаётся обычным "
                    "символом"
                )
            elif spans:
                problems.append(
                    "в адресе и шаблон, и якорь пишутся решёткой; как Директ "
                    "их различает, справка не говорит — проверьте получившийся "
                    "адрес глазами"
                )
        if spans and not allowed:
            problems.append(
                "подстановка в этом поле не производится — решётки останутся "
                "видимыми символами. Справка называет четыре места: заголовок, "
                "текст, ссылка на сайт и отображаемая ссылка"
            )
        problems.extend(_token_problems(field, text, allowed))
        return cls(field, text, spans, anchor, problems)

    # -- три вида одного значения -------------------------------------------

    @property
    def stored(self) -> str:
        """Что уходит в поле Директа — значение как есть, с решётками."""
        return self.source

    @property
    def default_variant(self) -> str:
        """Что показывается, когда подстановка не годится.

        Убираются **ограничители шаблонов**, а не все решётки подряд. Разница
        видна на адресе: непарная решётка в нём — разделитель якоря, и
        `https://site.ru/p#sec`, из которого её вычистили, ведёт на другую
        страницу. Своё же сообщение разбора говорит про непарную решётку в
        тексте «остаётся обычным символом» — здесь то же самое и сделано.

        Счётчик длины при этом снимает решётки все: правило
        `template_hash_excluded` записано в справочнике одной строкой на поле и
        шаблонов не разбирает. Отличить ограничитель от якоря может только этот
        модуль — и обязан, потому что ошибка тут в опасную сторону: адрес из
        1024 символов с якорем на конце длиннее предела, а счётчик, снявший его
        решётку, насчитает ровно предел и пропустит. Поэтому в справочник
        уходит значение, где оставшиеся решётки заменены обычным символом
        (`_for_limits`).

        Свойство отвечает и на второй вопрос, ради которого заведено: Директ
        проверяет при записи длину именно этого варианта, значит запись,
        прошедшая валидацию, гарантирует годным **только** запасной вариант, а
        про подставленные не говорит ничего."""
        return self._assemble(lambda span: span.default)

    @property
    def has_templates(self) -> bool:
        return bool(self.spans)

    def _assemble(self, value_of) -> str:
        """Собрать значение, заменив каждый шаблон на `value_of(span)`."""
        out, cursor = [], 0
        for span in self.spans:
            out.append(self.source[cursor:span.start])
            out.append(value_of(span))
            cursor = span.end + 1
        out.append(self.source[cursor:])
        return "".join(out)

    def render(self, phrase) -> str:
        """Значение с подставленной фразой.

        Подставляется `Phrase.shown`; в адресе — он же, но закодированный.
        Параметры фразы подставляются тем же проходом: `{param1}` и `{param2}`
        — механизм другой, но момент подстановки тот же, и предпросмотр,
        показавший фразу и оставивший `{param1}`, показал бы адрес, которого не
        бывает."""
        one = phrase if isinstance(phrase, Phrase) else Phrase(phrase)
        url = self.field in URL_FIELDS
        value = encoded(one.shown) if url else one.shown
        return _with_params(self._assemble(lambda _span: value), one, url=url)

    def __repr__(self) -> str:
        return (f"<Template {self.field} шаблонов {len(self.spans)}: "
                f"{excerpt(self.source, 40)}>")


def _looks_like_another_mechanism(default: str) -> bool:
    """Похож ли текст по умолчанию на имя макроса или параметра фразы.

    Ловится ровно путаница механизмов: `#keyword#`, `#{param1}#`. Обычный текст
    по умолчанию с фигурными скобками внутри сюда не попадает — сравнение идёт
    по всей строке целиком, а не по вхождению."""
    bare = default.strip().strip("{}").casefold()
    return bare in MACROS or bare in PHRASE_PARAMS


def tokens(value) -> list:
    """Токены в фигурных скобках: пары «имя, вид».

    Вид — `MACRO`, `PARAM` или `UNKNOWN`. Регистр имени сохраняется: справка
    пишет макросы строчными, и `{Keyword}` — это не макрос, а вопрос, который
    стоит задать человеку."""
    found = []
    for match in _TOKEN.finditer("" if value is None else str(value)):
        name = match.group(1)
        if name in MACROS:
            found.append((name, MACRO))
        elif name in PHRASE_PARAMS:
            found.append((name, PARAM))
        else:
            found.append((name, UNKNOWN))
    return found


def _brace_problems(text: str) -> list:
    """Фигурные скобки, которые ни в один токен не вошли.

    Разбор токенов ищет **готовые пары**, и обе беды проходят мимо него молча.
    `?utm_term={keyword` токена не образует вовсе — перечень макросов
    спрашивать не о чем, и адрес выглядит чистым. `{{keyword}}` даёт токен
    изнутри, и внутренний `{keyword}` объявляется законным макросом, хотя
    Директу достаётся запись с лишней парой скобок.

    Проверяется поэтому не токен, а остаток: скобка, не попавшая ни в одну
    границу токена, — лишняя. Один проход ловит оба случая, и ловит по
    определению, а не по перечню известных опечаток.

    Спрашивается это только у адреса: там фигурная скобка означает механизм. В
    заголовке она обычный символ, и жаловаться на неё было бы выдумкой."""
    edges = set()
    for match in _TOKEN.finditer(text):
        edges.add(match.start())
        edges.add(match.end() - 1)
    stray = [index for index, one in enumerate(text)
             if one in "{}" and index not in edges]
    if not stray:
        return []
    around = excerpt(text[max(stray[0] - 12, 0):stray[0] + 13], 40)
    return [
        f"фигурная скобка вне макроса — «{around}». Так выглядят и незакрытый "
        f"`{{keyword`, и лишняя пара `{{{{keyword}}}}`: Директ такую запись "
        f"макросом не считает, а в трафик уйдёт испорченная разметка"
    ]


def _token_problems(field: str, text: str, allowed: bool) -> list:
    """Что не так с фигурными скобками в этом поле."""
    said = FIELD_NAMES.get(field, field)
    url = field in URL_FIELDS
    problems = _brace_problems(text) if url else []
    for name, kind in tokens(text):
        if kind == MACRO and name in WITHDRAWN_MACROS:
            problems.append(
                f"`{{{name}}}` справка помечает как больше не поддерживаемый и "
                f"советует убрать его из ссылок"
            )
            continue
        if kind == MACRO and not url:
            problems.append(
                f"`{{{name}}}` — динамический параметр Директа, и справка "
                f"описывает его только для адреса объявления; для поля «{said}» "
                f"механизма подстановки она не называет"
            )
        elif kind == PARAM and not url:
            problems.append(
                f"`{{{name}}}` — параметр уровня фразы, и справка описывает его "
                f"подстановку только в адрес объявления; для поля «{said}» "
                f"механизма подстановки она не называет"
            )
        elif kind == UNKNOWN:
            near = name.casefold()
            hint = (" — похоже на макрос, записанный не строчными"
                    if near in MACROS or near in PHRASE_PARAMS else "")
            problems.append(
                f"`{{{excerpt(name, 40)}}}` — ни макрос из перечня "
                f"`references/url_macros.md`, ни параметр фразы{hint}"
            )
    return problems


def _with_params(text: str, phrase: Phrase, *, url: bool) -> str:
    """Подставить `{param1}` и `{param2}` значениями фразы.

    Спецсимволы значения кодируются в UTF-8 — так говорит справка, и пример там
    же: слеш превращается в `%2F`. Вне адреса подстановка не производится: там
    её справка не описывает, и придумывать её за Директ нельзя."""
    if not url:
        return text
    for name in PHRASE_PARAMS:
        token = "{" + name + "}"
        if token not in text:
            continue
        value = phrase.param(name)
        text = text.replace(token, "" if value is None else encoded(value))
    return text


# --------------------------------------------------------------------------
# Подставленные варианты
# --------------------------------------------------------------------------

class Variant:
    """Один подставленный вариант поля — единица предпросмотра."""

    __slots__ = ("field", "place", "phrase", "substituted", "default",
                 "templated", "problems", "notes")

    def __init__(self, field, place, phrase, substituted, default, problems,
                 notes=(), templated=True):
        self.field = field
        self.place = place
        self.phrase = phrase
        self.substituted = substituted
        self.default = default
        # Есть ли в поле шаблон вообще. Признак, а не сравнение строк:
        # подстановка фразы «окна» в шаблон `#окна#` даёт то же значение, что и
        # текст по умолчанию, и по равенству такое поле не отличить от поля, где
        # подставлять нечего.
        self.templated = bool(templated)
        self.problems = list(problems)
        self.notes = list(notes)

    @property
    def fits(self) -> bool:
        return not self.problems

    @property
    def shown(self) -> str:
        """Что увидит человек в показе по этой фразе — по модели справки.

        Справка описывает подмену для одного случая: «если после подстановки
        ключевой фразы в шаблон ограничение будет превышено, вместо ключевой
        фразы будет использоваться фраза по умолчанию». Остальные несоответствия
        справочнику — длинное слово, перебор узких символов — сведены сюда же,
        и это перенос: чем кончается каждый из них, справка не говорит.
        Наблюдением не подтверждено ни то, ни другое (шапка модуля)."""
        return self.default if self.fell_back else self.substituted

    @property
    def fell_back(self) -> bool:
        """Покажется ли вместо подстановки текст по умолчанию.

        Подмена бывает **только у шаблона**: подменять нечем там, где его нет.
        Негодное значение без шаблона — это негодное значение, и оно тоже
        случается: `{param1}` в двести пятьдесят пять символов выносит адрес за
        предел и в поле, где решёток нет вовсе. Считать это откатом значило бы
        показать человеку безопасный исход, которого не бывает: «покажется текст
        по умолчанию» — а никакого другого текста у поля нет.

        Отсюда и `shown`: без шаблона он отдаёт то самое негодное значение.
        Что с ним сделает Директ, здесь не предполагается — сказано только, что
        подставится именно оно."""
        return self.templated and not self.fits

    def row(self) -> dict:
        """Строка предпросмотра. Отрисовкой занимается `P-01`."""
        return {
            "field": self.field,
            "place": self.place,
            "phrase": self.phrase.text,
            "substituted_from": self.phrase.shown,
            "substituted": self.substituted,
            "default": self.default,
            "shown": self.shown,
            "templated": self.templated,
            "fell_back": self.fell_back,
            "problems": list(self.problems),
            "notes": list(self.notes),
        }

    def __repr__(self) -> str:
        mark = "по умолчанию" if self.fell_back else "подстановка"
        return f"<Variant {self.field} {mark}: {excerpt(self.shown, 40)}>"


def field_problems(field: str, value, *, limits=None) -> list:
    """Что не так с самим шаблоном — без оглядки на набор фраз.

    Сюда входит и проверка справочником: правило `template_hash_excluded`
    снимает решётки, то есть меряет ровно вариант с текстом по умолчанию — его
    и видит Директ при записи. Проверка длины подстановки к ней добавляется, а
    не заменяет её: запись, не прошедшую валидацию, никакая подстановка не
    спасёт."""
    known = limits_for(limits)
    parsed = Template.parse(field, value, limits=known)
    problems = list(parsed.problems)
    said = known.text_problems(field, _for_limits(parsed.default_variant))
    if said and parsed.has_templates and substitutes(field, known):
        # Отдельная новость, а не украшение к длине: не помещается сам запасной
        # вариант, то есть годного варианта у поля нет ни одного.
        problems.append(
            f"текст по умолчанию не помещается ({'; '.join(said)}) — "
            f"подстановке подменяться нечем"
        )
    else:
        problems.extend(said)
    return problems


def variants(field: str, value, phrases, *, place=None, limits=None) -> list:
    """Подставленные варианты поля по всем фразам набора.

    По **всем**, а не по самой длинной. «Самая длинная подстановка» — это не
    одна фраза: у заголовка предел один на все символы, у текста два счётчика
    сразу, и фраза из сплошных запятых бьёт по второму, оставаясь короткой по
    первому. Перебор набора отвечает на вопрос точно, а порядок показа выбирает
    уже `Report.longest`.

    Поле, в котором подстановки не бывает, разбирается тем же перебором и с тем
    же исходом на каждой фразе. Пропускать его нельзя: предпросмотр показывает
    комплект целиком, и поле, пропавшее из показа, читается как пустое, а
    быстрая ссылка с решётками именно в предпросмотре и должна быть видна —
    ровно такой, какой её увидит человек в объявлении.

    **Запасной вариант тоже свой у каждой фразы.** Параметры подставляются в
    него наравне с подстановкой фразы, и длина у него от этого своя: `{param1}`
    в 255 символов выносит за предел и тот адрес, который в шаблоне помещался.
    Один запасной вариант на весь набор показывал бы человеку буквальный
    `{param1}` вместо адреса и не проверялся бы вовсе."""
    known = limits_for(limits)
    people = list(phrases)
    _some_phrases(people)
    parsed = Template.parse(field, value, limits=known)
    allowed = substitutes(field, known)
    url = field in URL_FIELDS
    templated = allowed and parsed.has_templates
    # Параметры фразы разбираются здесь, а не в `field_problems`: их значения
    # принадлежат фразе, и «параметр не задан» — свойство пары «поле, фраза», а
    # не поля. Незаполненный параметр в адресе оставляет пустое место — из
    # `https://site.ru/{param1}/` получается `https://site.ru//`, и это ровно та
    # ссылка, которая выглядит правильной и ведёт не туда.
    asked = [name for name, kind in tokens(parsed.source) if kind == PARAM]
    found = []
    for phrase in people:
        one = phrase if isinstance(phrase, Phrase) else Phrase(phrase)
        empty = [name for name in asked if one.param(name) is None]
        notes = [
            f"у фразы не задан `{{{name}}}` (`Keyword.UserParam"
            f"{name[-1]}`) — в адресе останется пустое место"
            for name in empty
        ] if url else []
        # Параметры подставляются и там, где шаблона нет: `allowed` отвечает про
        # решётки, а параметры — другой механизм со своими границами. У адреса
        # быстрой ссылки шаблон не работает, а макросы работают
        # (`url_macros.md`, раздел 5) — выключить заодно и их значило бы
        # смешать ровно то, что этот модуль и разделяет.
        default = _with_params(
            parsed.default_variant if allowed else parsed.stored, one, url=url)
        substituted = parsed.render(one) if templated else default
        if one.unmeasurable:
            # Неизвестна тут ровно одна вещь — что подставится вместо шаблона.
            # Всё, что от неё не зависит, проверяется как обычно: параметры
            # фразы подставляются независимо от шаблона, и адрес, который они
            # вынесли за предел, негоден при любой подстановке. Пропустить
            # заодно и это значило бы отпустить проверяемое вместе с
            # непроверяемым.
            #
            # Показывается поэтому вариант с текстом по умолчанию: он известен
            # целиком, и вердикт относится именно к нему.
            found.append(Variant(
                field, place, one, default, default,
                _value_problems(field, default, parsed.default_variant, known),
                notes + [one.unmeasurable], templated=False))
            continue
        problems = _value_problems(field, substituted, parsed.default_variant,
                                   known)
        if default != substituted:
            notes.extend(
                f"запасной вариант не помещается: {said}"
                for said in _value_problems(field, default,
                                            parsed.default_variant, known))
        notes.extend(_substituted_notes(field, substituted))
        found.append(Variant(field, place, one, substituted, default, problems,
                             notes, templated=templated))
    return found


def _value_problems(field: str, value: str, baseline: str, limits) -> list:
    """Что не так со значением после подстановки — без повтора за `field_problems`.

    Значение, совпавшее с вариантом по умолчанию, здесь молчит: его уже проверил
    `field_problems`, и повторить ту же беду на каждой фразе значило бы умножить
    одну строку на размер набора. Всё, что от него отличается, проверяется
    заново — и это не педантизм: подстановка меняет длину, а параметр фразы в
    двести пятьдесят пять символов меняет её и там, где шаблона нет вовсе."""
    if value == baseline:
        return []
    said = limits.text_problems(field, _for_limits(value))
    return said


# Чем подменяется решётка, которую справочник считать обязан. Обычный символ, и
# именно обычный: `#` не входит в перечень узких, длину даёт ту же, и правило
# длины слова от замены не меняется. Годился бы любой такой символ — важно, что
# правило `template_hash_excluded` его не снимет.
HASH_STAND_IN = "0"


def _for_limits(value: str) -> str:
    """Значение в том виде, в каком его считает справочник.

    Правило `template_hash_excluded` снимает решётки все: оно записано на поле и
    шаблонов не разбирает. К моменту вызова ограничители уже убраны сборкой, и
    решётка, оставшаяся в строке, — якорь адреса или одиночный символ текста;
    справочник снял бы и её, насчитав меньше настоящего. Ошибка в опасную
    сторону — значение, которое Директ отвергнет, прошло бы валидацию, — поэтому
    оставшиеся решётки подменяются обычным символом."""
    return value.replace(HASH, HASH_STAND_IN)


def _substituted_notes(field: str, value: str) -> list:
    """Что стоит сказать про подставленный адрес, не объявляя это отказом."""
    if field not in URL_FIELDS:
        return []
    size = len(value.encode("utf-8"))
    if size <= URL_BYTES_MAX:
        return []
    return [
        f"адрес занимает {size} байт при пределе {URL_BYTES_MAX}: за ним "
        f"Директ передаёт значения только для `yclid` и openstat, то есть "
        f"отваливаются все макросы разом. Оценка снизу — значения самих "
        f"макросов до показа неизвестны"
    ]


# --------------------------------------------------------------------------
# Отчёт по комплекту
# --------------------------------------------------------------------------

class Report:
    """Что даст шаблон на этом наборе фраз — поэлементно по всему комплекту.

    Единица отчёта — поле комплекта, а не объявление: у комбинаторного семь
    заголовков и три текста, и подстановка ломает их по отдельности. Отчёт,
    посмотревший на первый заголовок, промолчит о шестом, а показан будет любой
    из них."""

    __slots__ = ("items", "variants", "problems")

    def __init__(self, items, variants, problems):
        # `items` — что проверяли: тройки «поле, место, значение». Нужны, чтобы
        # отчёт мог сказать и про поле, у которого варианты не собрались.
        self.items = list(items)
        self.variants = list(variants)
        self.problems = list(problems)

    @property
    def ok(self) -> bool:
        """Нечего сказать человеку: ни замечаний к шаблонам, ни к подстановкам.

        Замечания к подстановке считаются наравне с отказом по длине, хотя
        `shown` от них не меняется. Разница между ними — в модели, а не в цене:
        подмену текстом по умолчанию справка описывает только для длины, а
        незаполненный `{param1}` даёт адрес, который выглядит правильным и ведёт
        не туда. Сложить их в одну кучу нельзя, промолчать про вторую — тоже."""
        return not self.problems and not self.unfit and not self.noted

    @property
    def unfit(self) -> list:
        """Варианты, которые справочнику не годятся, — все.

        Шире, чем `broken`, и намеренно: подмена бывает только у шаблона, а
        негодным значение бывает и без него. Отчёт, считавший негодные варианты
        по подмене, промолчал бы про адрес, который вынес за предел параметр
        фразы."""
        return [one for one in self.variants if not one.fits]

    @property
    def broken(self) -> list:
        """Варианты, вместо которых покажется текст по умолчанию."""
        return [one for one in self.variants if one.fell_back]

    @property
    def noted(self) -> list:
        """Варианты с замечанием, которое подмены не вызывает."""
        return [one for one in self.variants if one.notes]

    def of(self, field, place=None) -> list:
        return [one for one in self.variants
                if one.field == field and (place is None or one.place == place)]

    def always_default(self) -> list:
        """Поля, у которых подстановка не сработает ни на одной фразе."""
        seen, said = {}, []
        for one in self.variants:
            key = (one.field, one.place)
            seen.setdefault(key, []).append(one)
        for (field, place), group in seen.items():
            if not all(one.templated for one in group):
                continue  # шаблона в поле нет: показывать по-разному не обещали
            if all(one.fell_back for one in group):
                said.append((field, place, len(group)))
        return said

    def longest(self, field, place=None):
        """Самый длинный подставленный вариант поля — для показа человеку.

        Длиной здесь выбирается **порядок**, а не вердикт. Вердикт даёт
        справочник по каждой фразе отдельно (`variants`), и фраза, короткая по
        символам, но тяжёлая по узким, из него не выпадает. Если выбирать по
        длине ещё и вердикт, такая фраза окажется невидимой ровно потому, что
        она короткая."""
        group = self.of(field, place)
        if not group:
            return None
        return max(group, key=lambda one: len(one.substituted))

    def shown_for(self, phrase) -> dict:
        """Комплект в том виде, в каком он будет показан по этой фразе."""
        text = phrase.text if isinstance(phrase, Phrase) else str(phrase)
        out = {}
        for one in self.variants:
            if one.phrase.text != text:
                continue
            out.setdefault(one.field, []).append((one.place, one.shown))
        return {field: [shown for _place, shown in sorted(
            group, key=lambda pair: (pair[0] is None, pair[0]))]
            for field, group in out.items()}

    def preview(self, *, only_unfit: bool = False) -> list:
        """Строки предпросмотра. Отрисовкой занимается `P-01`."""
        rows = self.unfit if only_unfit else self.variants
        return [one.row() for one in rows]

    def summary(self) -> str:
        phrases = len({one.phrase.text for one in self.variants})
        places = len({(one.field, one.place) for one in self.variants})
        said = [f"полей {places}", f"фраз {phrases}"]
        if self.problems:
            said.append(f"замечаний к шаблонам {len(self.problems)}")
        # Две разные новости, и складывать их в одну нельзя: подмена бывает
        # только у шаблона, а негодным значение бывает и без него.
        broken = self.broken
        if broken:
            said.append(f"не поместится подстановок {len(broken)}")
        others = len(self.unfit) - len(broken)
        if others:
            said.append(f"негодных значений без шаблона {others}")
        if self.noted:
            said.append(f"замечаний к подстановкам {len(self.noted)}")
        always = self.always_default()
        if always:
            said.append(f"полей всегда по умолчанию {len(always)}")
        return ", ".join(said)

    def lines(self) -> list:
        """Человекочитаемая сводка. Печатает её вызывающий скрипт через `outline`."""
        said = [self.summary()]
        for problem in self.problems:
            said.append(f"  ✗ {problem}")
        for field, place, count in self.always_default():
            said.append(
                f"  ✗ {_where(field, place)}: подстановка не помещается ни для "
                f"одной из {count} фраз — поле всегда покажет текст по умолчанию"
            )
        for one in self.unfit:
            # Исход называется по тому, есть ли чем подменять. Обещать откат
            # там, где запасного варианта не существует, — это придумать
            # человеку безопасный конец несуществующей истории.
            said.append(
                f"  · {_where(one.field, one.place)} по фразе "
                f"«{excerpt(one.phrase.text, 40)}»: {'; '.join(one.problems)}"
                + ("" if one.fell_back
                   else " — подменять нечем, шаблона в поле нет")
            )
        for one in self.noted:
            said.append(
                f"  · {_where(one.field, one.place)} по фразе "
                f"«{excerpt(one.phrase.text, 40)}»: {'; '.join(one.notes)}"
            )
        return said

    def __repr__(self) -> str:
        return f"<Report {self.summary()}>"


def check(items, phrases, *, limits=None) -> Report:
    """Проверить набор полей на наборе фраз.

    `items` — тройки «имя правила справочника, место, значение». Место нужно
    там, где поле повторяется: заголовок третий и заголовок седьмой — разные
    места одного поля, и человеку надо назвать, какое именно не помещается."""
    known = limits_for(limits)
    people = [one if isinstance(one, Phrase) else Phrase(one) for one in phrases]
    _some_phrases(people)
    collected, problems = [], []
    for field, place, value in items:
        said = field_problems(field, value, limits=known)
        problems.extend(f"{_where(field, place)}: {one}" for one in said)
        collected.extend(variants(field, value, people, place=place,
                                  limits=known))
    return Report(items, collected, problems)


def _some_phrases(people) -> None:
    """Пустой набор фраз — отказ, а не чистый отчёт.

    Случай не выдуманный: в группе с одним автотаргетингом `phrases_of` пустой
    список и вернёт — текста фразы у автотаргетинга нет. Что Директ подставит в
    шаблон при таком показе, справка не говорит вовсе, и молча ответить за неё
    «ничего страшного» нельзя.

    Кому нужна проверка одного значения без набора фраз, тому есть
    `field_problems`: он отвечает на другой вопрос — примет ли это Директ при
    записи, — и фраз для ответа не требует."""
    if people:
        return
    raise DirectFailure(
        "Проверять подстановку не на чем: набор фраз пуст. Отчёт без единого "
        "варианта вышел бы чистым, и отсутствие данных выглядело бы "
        "выполненной валидацией. Группа с одним автотаргетингом даёт ровно "
        "такой набор. Проверка самого значения без фраз — `field_problems`."
    )


def _where(field, place) -> str:
    """Как назвать место человеку: «заголовок 6», «ссылка».

    Место называется всегда, когда оно есть. Отчёт, сказавший «заголовок не
    помещается» про комплект из семи, отправляет человека искать, какой именно."""
    name = FIELD_NAMES.get(field, field)
    return name if place is None else f"{name} {place}"


def kit_items(kit) -> list:
    """Разложить комплект в тройки для `check`.

    Все семь заголовков и все три текста, каждый своим местом, плюс ссылка и
    отображаемая ссылка. Перечень собирается из самого комплекта, а не из
    списка имён: комплект бывает неполным, а заголовок, забытый в перечне, —
    это тот самый шестой, о котором отчёт промолчит.

    Очищаемые поля (`responsive.CLEAR`) пропускаются: `null` в запросе — это не
    значение, у которого бывает длина или шаблон."""
    from responsive import CLEAR

    items = []
    for place, title in enumerate(kit.titles, start=1):
        items.append(("ResponsiveAd.Titles.item", place, title))
    for place, text in enumerate(kit.texts, start=1):
        items.append(("ResponsiveAd.Texts.item", place, text))
    for field, value in (("Href", kit.href),
                         ("DisplayUrlPath", kit.display_url_path)):
        if value and value is not CLEAR:
            items.append((field, None, value))
    return items


def check_kit(kit, phrases, *, limits=None) -> Report:
    """Проверить комплект целиком: каждый заголовок и каждый текст отдельно."""
    return check(kit_items(kit), phrases, limits=limits)


# --------------------------------------------------------------------------
# Разметка в самой ссылке
# --------------------------------------------------------------------------

def _without_anchor(text: str) -> tuple:
    """Адрес и его якорь по отдельности: пара «адрес, якорь».

    Якорем считается **непарная** решётка, а не первая попавшаяся: парные —
    ограничители шаблона, и адрес `?q=#окна#&utm_source=ya`, отрезанный по
    первой, потерял бы всё, что стоит после шаблона. Пустой якорь означает, что
    его в адресе нет.

    Как Директ различает их сам, справка не говорит — на это разбор шаблона
    жалуется отдельно (`Template.parse`). Здесь выбрано то же прочтение, что и
    там: иначе два места одного файла разошлись бы в понимании одного адреса."""
    places = [index for index, one in enumerate(text) if one == HASH]
    if not len(places) % 2:
        return text, ""
    cut = places[-1]
    return text[:cut], text[cut:]


def marks_of(href) -> dict:
    """Метки `utm_*`, зашитые в адрес: имя → значение.

    Вопрос, на который отвечает правило `UTM-04`: стоит ли разметка в самой
    ссылке. Смотреть надо именно на ключи `utm_*`, а не на «параметры вообще» —
    `ref=direct` в адресе разметкой не является, и правило, принявшее его за
    разметку, промолчит о ссылке, у которой атрибуции нет вовсе.

    Якорь отрезается так же, как в `mark`: по **непарной** решётке, а не по
    первой попавшейся. Отрезанная по первой, ссылка
    `?q=#окна#&utm_source=ya` осталась бы без единой метки — и кабинет,
    размеченный прямо в адресах, выглядел бы неразмеченным ровно там, где
    шаблон и стоит."""
    text, _anchor = _without_anchor("" if href is None else str(href))
    if "?" not in text:
        return {}
    found = {}
    for pair in text.split("?", 1)[1].split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        if name.startswith("utm_"):
            found[name] = value
    return found


def mark(href, query: str) -> str:
    """Подключить разметку к адресу по правилу `UTM-05`.

    Три случая правила: адрес без параметров подключает метки через `?`, с
    параметрами — через `&`, а якорь переносится в самый конец, после всех
    меток. Последнее не косметика: якорь, оставленный в середине, отрезает все
    параметры после себя.

    Решётка шаблона от якоря здесь не отличается — их и Директ различает сам, а
    справка про это молчит (`Template.parse`). Поэтому якорем считается **не**
    решётка внутри пары: сначала из адреса вынимаются шаблоны, и якорь ищется в
    том, что осталось.

    Длину результата функция не проверяет и проверить не может: поля она не
    знает, а предел у `ResponsiveAd.Href` и `Sitelink.Href` разный только по
    имени правила. Разметка легко добавляет к адресу три сотни символов, и
    проверить получившееся обязан тот, кто его записывает, — тем же
    `field_problems` или `variants`, что и всякое другое значение поля."""
    query = str(query).lstrip("?&")
    if not query:
        return "" if href is None else str(href)
    text, anchor = _without_anchor("" if href is None else str(href))
    joiner = "&" if "?" in text else "?"
    return f"{text}{joiner}{query}{anchor}"
