"""Матрица пар комплекта: построить полностью и донести пометки агента."""

from __future__ import annotations

from config import DirectFailure, excerpt
# Разбор целого — общее правило скилла, а не свойство матрицы пар: он
# нужен и командам, которые матриц не строят, а живя здесь, оставался
# им недоступным. Живёт он теперь в `incoming` (`F-15`).
from incoming import Shape, whole_number

TITLE_FIELD = "ResponsiveAd.Titles.item"
TEXT_FIELD = "ResponsiveAd.Texts.item"

PAIRS_SHOWN = 8


# --------------------------------------------------------------------------
# Форма брифа
# --------------------------------------------------------------------------

# Бриф у человека **один**. Он пишет туда заголовки, тексты, быстрые ссылки,
# уточнения и визитку и ждёт, что тем же файлом и проверит комплект, и
# посмотрит превью. Поэтому форма объявлена здесь ровно один раз, а команды её
# ввозят.
#
# Объявлений было два, и расходились они молча: перечень `ads_generate.py` не
# знал `callouts`, `vcard`, `price`, `sitelinks` и `images`, и бриф, прошедший
# превью, падал у разбора — «Бриф …: поля, которых команда не знает: callouts,
# images, price, sitelinks, vcard. Известные: …». Обратное направление тише и
# потому хуже: превью принимало `segments`, `phrases`, `group` и `marks` и
# молча ими не пользовалось. Каждое объявление проверяло свой файл, и разойтись
# им было не на чем (`P-03`).
#
# Перечень закрыт намеренно, и открыть его (`fields=None`) значило бы снять не
# расхождение, а проверку: опечатка в имени поля перестала бы быть отказом и
# стала бы молча потерянным разделом брифа.
#
# Значение словаря — как поле зовётся в подсказке `--brief`: перечень полей и
# перечень их имён для человека — один перечень, и второй разошёлся бы с первым
# тем же способом, каким разошлись формы.
BRIEF_FIELDS = {
    "titles": "заголовки",
    "texts": "тексты",
    "segments": "сегменты",
    "phrases": "фразы",
    "group": "группа, чьи фразы подставлять",
    "href": "ссылка",
    "display_url_path": "отображаемая ссылка",
    "note": "заметка",
    "marks": "пометки агента",
    "sitelinks": "быстрые ссылки",
    "callouts": "уточнения",
    "vcard": "визитка",
    "price": "цена",
    "images": "изображения",
}

# Поля пометки агента. Перечень был записан **пятью** носителями сразу: двумя
# формами команд, `__slots__` класса ниже, литералом множества в `marks_of` и
# текстом её же сообщения «Известные: …». Сведение двух форм оставило бы три,
# и обещание «одинаковый вердикт» на них бы и сломалось: превью принимало
# пометку с полем, которого разбор не знает, молча.
#
# Деление на два перечня — не украшение: `said`, `code` и `phrase` обязаны быть
# строкой, `title` и `text` — целым, потому что `int()` превратил бы `1.8` в
# `1` и **перенёс бы суждение на чужой заголовок**. Перечень допустимых полей
# из этого деления выводится, а не набирается третьим списком.
MARK_TEXT = ("said", "code", "phrase")
MARK_PLACES = ("title", "text")
MARK_FIELDS = MARK_TEXT + MARK_PLACES

BRIEF_MARK = Shape(fields=set(MARK_FIELDS), text=MARK_TEXT, whole=MARK_PLACES)

# Быстрая ссылка: заголовок и необязательное описание. Описания показываются
# только в расширенном формате на десктопах и планшетах — справка «Быстрые
# ссылки», — и это решает шаблон плейсмента, а не наличие поля.
BRIEF_SITELINK = Shape(
    fields={"title", "description", "href"},
    text=("title", "description", "href"),
    needed={"title": "Быстрая ссылка без текста не показывается: показывать "
                     "нечего."})

BRIEF = Shape(
    fields=set(BRIEF_FIELDS),
    # Скаляры — тот же чужой ввод, что и элементы списков. `123` на месте
    # отображаемой ссылки неявно стало бы строкой «123», и формат объявил бы
    # себя чистым.
    text=("href", "display_url_path", "note", "vcard", "price"),
    # `group` в брифе — идентификатор группы, чьи фразы подставлять, а не имя.
    # В строке набора данных поле с тем же именем означает другое — как
    # раскладывать строки по комплектам, — и там оно текст.
    whole=("group",),
    # `blank_absent` здесь пуст намеренно. Бриф приходит только объектом, и
    # пустое значение в нём — испорченная просьба, а не незаполненное поле:
    # пустую ссылку `responsive.Kit` отвергает отдельным сообщением про
    # `--clear href`, и приведённая к отсутствию она сошла бы за неназванную.
    text_lists=("titles", "texts", "segments", "phrases", "callouts",
                "images"),
    records={"sitelinks": BRIEF_SITELINK, "marks": BRIEF_MARK},
)


def brief_help(reads) -> str:
    """Подсказка к `--brief`: что команда читает и что примет молча.

    Поля брифа читают обе команды, но не одни и те же: `ads_generate.py` не
    рисует уточнений, `preview.py` не судит о комплекте и потому не смотрит на
    пометки. Поле, принятое разбором и никуда не девшееся, ничем не отличается
    от опечатки — человек написал, команда промолчала, — поэтому игнорируемое
    называется вслух, а не подразумевается.

    Собирается подсказка **по общей форме**, а не набирается руками: набранный
    руками перечень отстаёт от формы на первом же новом поле — тем же способом,
    каким разошлись сами формы. Название нечитаемого поля здесь и означает
    «видно обеим командам без второй правки»."""
    named = set(reads)
    unknown = sorted(named - set(BRIEF_FIELDS))
    if unknown:
        # Опечатка в перечне читаемого выключила бы упоминание поля молча: оно
        # переехало бы в «принимает, но не читает» и выглядело бы решением.
        raise DirectFailure(
            f"Команда объявляет прочитанными поля, которых нет в форме "
            f"брифа: {', '.join(unknown)}.")
    read = [one for one in BRIEF_FIELDS if one in named]
    idle = [one for one in BRIEF_FIELDS if one not in named]
    said = "бриф: " + ", ".join(BRIEF_FIELDS[one] for one in read)
    if idle:
        said += ("; принимает, но не читает — "
                 + ", ".join(BRIEF_FIELDS[one] for one in idle))
    return said


# --------------------------------------------------------------------------
# Пометка агента
# --------------------------------------------------------------------------

class Mark:
    """Суждение агента о месте комплекта: где и что не так.

    Приходит снаружи и здесь не рождается. Модуль отвечает за то, чтобы пометка
    доехала до своей пары и не уехала на чужую, — за содержание отвечает тот,
    кто её написал.

    Адресуется тремя способами, и разница в охвате:

    * `title` и `text` вместе — **пара**: «третий заголовок с первым текстом
      противоречат друг другу»;
    * только `title` либо только `text` — **элемент**: пометка достаётся всем
      парам, где этот элемент участвует, потому что показан он будет в каждой;
    * ни то, ни другое — **комплект** целиком: «не покрыт мотив цены».

    `phrase` сужает адрес до одной подстановки. У комплекта с шаблонами пара
    `3 × 1` существует столько раз, сколько в группе фраз, и это **разные**
    показы: заголовок ломается по одной фразе и читается по остальным. Пометка
    без фразы досталась бы всем — выгрузка утверждала бы, что править надо и
    исправные показы. Пустая фраза означает «на всех», и это правильное
    умолчание: суждение о самом тексте от подстановки не зависит."""

    # Не свой перечень, а тот же: атрибуты объекта и допустимые поля записи —
    # одно устройство, записанное дважды, расходится молча.
    __slots__ = MARK_FIELDS

    def __init__(self, said: str, *, title=None, text=None, code: str = "",
                 phrase=None):
        self.said = str(said)
        self.title = None if title is None else int(title)
        self.text = None if text is None else int(text)
        self.phrase = None if phrase is None else str(phrase)
        # Код нужен витрине `P-02`, чтобы подсветить пару, не разбирая русскую
        # фразу. Своего перечня кодов модуль не заводит: их называет тот, кто
        # судит, а заранее известного списка суждений не бывает.
        self.code = str(code) or "суждение"

    @property
    def where(self) -> str:
        if self.title is not None and self.text is not None:
            said = f"пара {self.title} × {self.text}"
        elif self.title is not None:
            said = f"заголовок {self.title}"
        elif self.text is not None:
            said = f"текст {self.text}"
        else:
            said = "комплект"
        if self.phrase is None:
            return said
        return f"{said} по фразе «{excerpt(self.phrase, 40)}»"

    def covers(self, title_place: int, text_place: int, phrase=None) -> bool:
        """Достаётся ли эта пометка названной паре этой подстановки."""
        if self.title is not None and self.title != title_place:
            return False
        if self.text is not None and self.text != text_place:
            return False
        if self.phrase is not None and self.phrase != phrase:
            return False
        return self.title is not None or self.text is not None

    def row(self) -> dict:
        return {"code": self.code, "where": self.where, "said": self.said,
                "title": self.title, "text": self.text, "phrase": self.phrase}

    def __repr__(self) -> str:
        return f"<Mark {self.where}>"


def marks_of(records, kit, phrases=()) -> list:
    """Пометки из записей, с проверкой, что адресат существует.

    Содержание пометки не проверяется вовсе: судит агент."""
    out = []
    for number, record in enumerate(records or (), start=1):
        if not isinstance(record, dict):
            raise DirectFailure(
                f"Пометка {number} — это {type(record).__name__}, а ожидается "
                f"объект с полем `said` и, если пометка адресная, `title` "
                f"и `text`.")
        unknown = sorted(set(record) - set(MARK_FIELDS))
        if unknown:
            raise DirectFailure(
                f"В пометке {number} поля, которых команда не знает: "
                f"{', '.join(unknown)}. Известные: "
                f"{', '.join(MARK_FIELDS)}.")
        said = str(record.get("said") or "").strip()
        if not said:
            raise DirectFailure(
                f"Пометка {number} без текста. Пометка — это то, что человек "
                f"прочитает; пустая помечает пару и ничего не говорит.")
        mark = Mark(said, title=_place(record.get("title"), number, "title"),
                    text=_place(record.get("text"), number, "text"),
                    code=record.get("code") or "",
                    phrase=record.get("phrase") or None)
        _addressed(mark, kit, number, phrases)
        out.append(mark)
    return out


def _place(value, number: int, name: str):
    """Номер места из записи. Целое — и только целое.

    `int()` здесь не годится, и это не педантизм. Он превращает `1.8` в `1`, а
    `true` — в единицу: опечатка не теряется явно, а **переносит суждение на
    чужой заголовок** и на все его пары. Молча уехавшая пометка хуже
    потерянной: потерянную человек хватится, уехавшую прочитает как верную.

    `bool` проверяется раньше `int` намеренно: в Python он его подкласс, и
    `isinstance(True, int)` истинно."""
    if value is None:
        return None
    return whole_number(value, f"В пометке {number} поле «{name}»")


def _addressed(mark: "Mark", kit, number: int, phrases=()) -> None:
    for name, place, have in (("title", mark.title, len(kit.titles)),
                              ("text", mark.text, len(kit.texts))):
        if place is None:
            continue
        said = "заголовков" if name == "title" else "текстов"
        if not 1 <= place <= have:
            raise DirectFailure(
                f"Пометка {number} адресована на {name} {place}, а в комплекте "
                f"{said} {have}. Место считается с единицы. Молча потерянная "
                f"пометка — это суждение, которое человек написал и не увидел.")
    if mark.phrase is None:
        return
    # Фраза — такая же часть адреса, как номер места, и проверяется так же.
    # Опечатка в ней не «почти верна»: `covers` не прицепит пометку ни к одной
    # паре, счётчик её посчитает, а текста человек не увидит нигде. Это тот же
    # молчаливый провал, от которого проверка номеров и заведена.
    known = [one.text if hasattr(one, "text") else str(one) for one in phrases]
    if mark.phrase is not None and known.count(mark.phrase) > 1:
        # Две строки набора с одним текстом различаются параметрами, а адрес у
        # пометки — текст. Прицепить её к обеим значило бы потребовать
        # исправить и тот показ, который в порядке; выбрать одну из двух —
        # угадать. Отказ здесь единственный честный исход.
        raise DirectFailure(
            f"Пометка {number} адресована фразе «{excerpt(mark.phrase, 40)}», "
            f"а таких строк в наборе {known.count(mark.phrase)}: они "
            f"различаются параметрами, а не текстом. Пометка досталась бы "
            f"всем, и выгрузка потребовала бы править исправные показы.")
    if not known:
        raise DirectFailure(
            f"Пометка {number} адресована фразе «{excerpt(mark.phrase, 40)}», "
            f"а подстановок здесь нет вовсе: в комплекте нет шаблонов либо "
            f"фразы не переданы. Адресовать нечему.")
    if mark.phrase not in known:
        shown = ", ".join(f"«{excerpt(one, 30)}»" for one in known[:3])
        tail = "" if len(known) <= 3 else f" и ещё {len(known) - 3}"
        raise DirectFailure(
            f"Пометка {number} адресована фразе «{excerpt(mark.phrase, 40)}», "
            f"а такой среди фраз нет. Есть: {shown}{tail}. Пометка с опечаткой "
            f"в адресе не досталась бы ни одной паре и пропала бы молча.")


# --------------------------------------------------------------------------
# Матрица пар
# --------------------------------------------------------------------------

class Pair:
    """Одна комбинация «заголовок × текст» — то, что увидит человек.

    `phrase` заполнен там, где в комплекте есть шаблон: пара складывается после
    подстановки, и у одного комплекта их столько же, сколько фраз в группе."""

    __slots__ = ("title_place", "text_place", "title", "text", "phrase",
                 "marks", "slice")

    def __init__(self, title_place: int, text_place: int, title: str,
                 text: str, *, phrase=None, marks=(), slice_of=None):
        self.title_place, self.text_place = title_place, text_place
        self.title, self.text = title, text
        self.phrase = phrase
        self.marks = list(marks)
        # Данные среза лежат у пары, а не только у матрицы. Разделение «срез
        # знает, пара не знает» уже дало находку: выгрузка несла подставленную
        # ссылку и параметры строки, а машинный отчёт — нет, потому что
        # собирался из пар.
        self.slice = dict(slice_of or {})

    @property
    def where(self) -> str:
        said = f"пара {self.title_place} × {self.text_place}"
        return said if self.phrase is None else f"{said} по фразе «{self.phrase}»"

    def row(self) -> dict:
        return dict(self.slice, **{
            "title_place": self.title_place, "text_place": self.text_place,
            "title": self.title, "text": self.text, "phrase": self.phrase,
            "marks": [one.row() for one in self.marks],
        })

    def __repr__(self) -> str:
        return f"<Pair {self.title_place}×{self.text_place}>"


class Matrix:
    """Все комбинации комплекта разом.

    Существует затем, что «посмотреть глазами» на комплект из семи заголовков
    нельзя: в списке полей комбинаций не видно, а их двадцать одна. Пара без
    пометок годной **не объявляется** — модуль о её смысле не знает ничего и
    знать не может; отсутствие пометки означает лишь, что судивший ничего о ней
    не сказал."""

    __slots__ = ("pairs", "titles", "texts", "phrase", "href",
                 "display_url_path")

    def __init__(self, pairs, titles, texts, *, phrase=None, href=None,
                 display_url_path=None):
        self.pairs = list(pairs)
        self.titles, self.texts = list(titles), list(texts)
        # Срез несёт и фразу целиком, и подставленные ссылки. Пары в нём — то,
        # что человек прочитает, а ссылка — то, куда он придёт: без неё две
        # строки набора с одной фразой и разной ценой в `{param1}` в выгрузке
        # неразличимы, хотя срез для них строился разный.
        self.phrase = phrase
        self.href, self.display_url_path = href, display_url_path

    @property
    def marked(self) -> list:
        return [one for one in self.pairs if one.marks]

    def summary(self) -> str:
        said = [f"комбинаций {len(self.pairs)}"]
        if self.marked:
            said.append(f"с пометками {len(self.marked)}")
        phrases = {one.phrase for one in self.pairs if one.phrase is not None}
        if phrases:
            said.append(f"фраз {len(phrases)}")
        return ", ".join(said)

    def rows(self) -> list:
        return [one.row() for one in self.pairs]

    def __len__(self) -> int:
        return len(self.pairs)

    def __repr__(self) -> str:
        return f"<Matrix {self.summary()}>"


def matrix(titles, texts, *, phrase=None, marks=(), href=None,
           display_url_path=None) -> Matrix:
    """Матрица пар одного среза комплекта.

    Пометки раздаются по адресу: парная достаётся своей паре, элементная — всем
    парам, где этот элемент участвует. Второе не для удобства: элемент будет
    показан в каждой своей комбинации, и суждение о нём касается их всех.

    Пометка на комплект целиком сюда не попадает — у неё нет пары, и приклеить
    её к двадцати одной строке значило бы двадцать один раз повторить одно и то
    же. Её показывает `Review`."""
    shown = {
        "param1": getattr(phrase, "param1", None),
        "param2": getattr(phrase, "param2", None),
        "phrase_id": getattr(phrase, "identifier", None),
        "href": href,
        "display_url_path": display_url_path,
    }
    pairs = []
    for title_place, title in enumerate(titles, start=1):
        for text_place, text in enumerate(texts, start=1):
            pairs.append(Pair(
                title_place, text_place, title, text,
                phrase=None if phrase is None else phrase_text(phrase),
                marks=[one for one in marks
                       if one.covers(title_place, text_place,
                                     phrase_text(phrase))],
                slice_of=shown))
    return Matrix(pairs, titles, texts, phrase=phrase, href=href,
                  display_url_path=display_url_path)


def phrase_text(phrase):
    """Текст фразы, чем бы она ни была передана."""
    if phrase is None:
        return None
    return phrase.text if hasattr(phrase, "text") else str(phrase)


# --------------------------------------------------------------------------
# Полный разбор комплекта
# --------------------------------------------------------------------------

class Review:
    """Что модуль может сказать о комплекте.

    Смешивать нельзя, потому что цена разная. Длина сверх предела — отказ
    Директа, и запись с ней не уйдёт. Пометка — суждение, и комплект с
    пометками записывается, если человек так решил."""

    __slots__ = ("kit", "lengths", "marks", "matrix", "composition")

    def __init__(self, kit, lengths, marks, matrices, composition=()):
        self.kit = kit
        self.lengths = lengths
        self.marks = list(marks)
        # Матриц бывает несколько — по одной на фразу, если есть шаблоны.
        self.matrix = list(matrices)
        # Состав комплекта: сколько в нём заголовков и текстов против предела
        # справочника. Отдельно от длин, потому что это другой вопрос: длина
        # про значение, состав — про их число.
        self.composition = list(composition)

    @property
    def blocking(self) -> bool:
        """Есть ли то, из-за чего запись не уйдёт. Пометки сюда не входят."""
        if self.composition:
            return True
        return self.lengths is not None and not self.lengths.ok

    @property
    def whole(self) -> list:
        """Пометки на комплект целиком: у них нет пары."""
        return [one for one in self.marks
                if one.title is None and one.text is None]

    @property
    def marked_pairs(self) -> list:
        return [one for grid in self.matrix for one in grid.marked]

    def summary(self) -> str:
        # «Заголовков 6, предел 7», а не «6 из 7»: второе читается как недобор
        # и зовёт дописать седьмой — ровно то, от чего предостерегает `AD-01`.
        said = [f"заголовков {len(self.kit.titles)}, предел {_titles_max()}",
                f"текстов {len(self.kit.texts)}, предел {_texts_max()}",
                f"комбинаций {sum(len(one) for one in self.matrix)}"]
        if self.marks:
            said.append(f"пометок агента {len(self.marks)}")
        if self.lengths is not None:
            said.append(self.lengths.summary())
        return ", ".join(said)

    def lines(self, *, detail: bool = False) -> list:
        """Человекочитаемая сводка. Печатает её вызывающий скрипт.

        `detail` дописывает под каждой помеченной парой её текст. Это параметр,
        а не повод писать свой цикл в команде: своих циклов было два, и оба
        по очереди теряли то адрес пометки, то её текст, то замечание к
        составу. Один источник строк — одно место, где это можно забыть."""
        said = [self.summary()]
        for one in self.composition:
            said.append(f"  ✗ {one}")
        if self.lengths is not None:
            # Вердикт справочника идёт первым: комплект, который Директ не
            # примет, обсуждать по смыслу рано.
            said += self.lengths.lines()[1:]
        for one in self.whole:
            # `one.where`, а не слово «комплект»: у пометки бывает адрес
            # подстановки, и «комплект по фразе «окна москва»» — другой дефект,
            # чем «комплект». В выгрузке это уже различалось, в тексте — нет.
            said.append(f"  · {one.where}: {one.said}")
        shown = self.marked_pairs
        for one in shown[:PAIRS_SHOWN]:
            said.append(f"  · {one.where}: "
                        f"{'; '.join(mark.said for mark in one.marks)}")
            if detail:
                said.append(f"      {excerpt(one.title, 60)} // "
                            f"{excerpt(one.text, 60)}")
        if len(shown) > PAIRS_SHOWN:
            said.append(f"  · … ещё помеченных пар: {len(shown) - PAIRS_SHOWN}")
        return said

    def row(self) -> dict:
        """Машиночитаемый отчёт. Его встраивает витрина `P-02`."""
        return {
            "titles": list(self.kit.titles),
            "texts": list(self.kit.texts),
            "summary": self.summary(),
            "blocking": self.blocking,
            "composition": list(self.composition),
            "marks": [one.row() for one in self.marks],
            "matrix": [one.row() for grid in self.matrix for one in grid.pairs],
            "lengths": None if self.lengths is None else {
                "ok": self.lengths.ok,
                "summary": self.lengths.summary(),
                "problems": list(self.lengths.problems),
                # Негодные варианты — отдельно от `problems`: там замечания к
                # самому шаблону, а сломаться подстановка может и у
                # безупречного. Без них машинный потребитель видел число
                # ошибок и не мог назвать ни поле, ни фразу, тогда как
                # человеку они показывались.
                "unfit": [one.row() for one in
                          getattr(self.lengths, "unfit", [])],
                "noted": [one.row() for one in
                          getattr(self.lengths, "noted", [])],
            },
        }

    def __repr__(self) -> str:
        return f"<Review {self.summary()}>"


def _titles_max() -> int:
    from responsive import titles_max

    return titles_max()


def _texts_max() -> int:
    from writer import Limits

    rule = Limits.load().collections.get("ResponsiveAd.Texts") or {}
    limit = rule.get("max")
    if not isinstance(limit, int) or limit < 1:
        raise DirectFailure(
            "В справочнике лимитов нет предела состава «ResponsiveAd.Texts». "
            "Без него достройка комплекта дописывала бы тексты вслепую."
        )
    return limit


def review(kit, phrases=(), *, marks=(), limits=None) -> Review:
    """Разбор комплекта: длины по справочнику, матрица, пометки агента.

    Матрица считается по-разному, и разница существенная. Есть шаблоны —
    по каждой фразе: пара складывается после подстановки. Нет шаблонов — одна
    матрица, потому что подставлять нечего и двадцать одна пара по каждой фразе
    была бы двадцатью одной копией."""
    from templates import check_kit, kit_items

    people = list(phrases)
    items = kit_items(kit)
    templated = any(depends_on_phrase(field, value, limits)
                    for field, _place, value in items)
    if templated and not people:
        raise DirectFailure(
            "В комплекте есть шаблоны подстановки, а фраз, на которых их "
            "проверять, не передано. Матрица по тексту шаблона показала бы "
            "пары, которых человек не увидит: пара складывается после "
            "подстановки. Передайте фразы группы."
        )

    lengths = None
    if people:
        lengths = check_kit(kit, people, limits=limits)
    elif items:
        # Без фраз проверяется само значение — примет ли его Директ при
        # записи. Это другой вопрос, чем «что покажется», и движок отвечает на
        # него отдельной функцией именно потому, что фраз для ответа не нужно.
        lengths = _without_phrases(items, limits)

    said = list(marks)
    grids = []
    composition = composition_problems(kit)
    if templated:
        for phrase in people:
            shown = _shown_for(lengths, phrase)
            grids.append(matrix(
                shown.get(TITLE_FIELD) or list(kit.titles),
                shown.get(TEXT_FIELD) or list(kit.texts),
                phrase=phrase, marks=said,
                href=(shown.get("Href") or [kit.href])[0],
                display_url_path=(shown.get("DisplayUrlPath")
                                  or [kit.display_url_path])[0]))
    else:
        grids.append(matrix(kit.titles, kit.texts, marks=said,
                            href=kit.href,
                            display_url_path=kit.display_url_path))
    _landed(said, grids)
    return Review(kit, lengths, said, grids, composition=composition)


def _landed(marks, grids) -> None:
    """Каждая адресная пометка обязана достаться хотя бы одной паре.

    Общее правило вместо перечня частных. Адрес проверялся по частям — номер
    заголовка по числу заголовков, фраза по набору фраз, — и каждая проверка
    по отдельности проходила, а пометка всё равно не доставалась никому:
    комплект без шаблонов строит одну матрицу с `phrase=None`, и `3x1@фраза`
    не совпадает ни с чем. Счётчик её считал, текста человек не видел.

    Проверка **по результату** закрывает и такие случаи, и те, которых мы ещё
    не придумали: у следующего компонента адреса своя частная проверка может
    быть забыта, а эта — общая, и забыть её нельзя, не сняв целиком.

    Пометка на комплект пары не имеет по устройству, но адрес подстановки у
    неё бывает — «весь комплект не работает по этой фразе». Такой адрес тоже
    обязан указывать на построенный срез: иначе он показывается человеку и не
    означает ничего."""
    landed = {id(one) for grid in grids for pair in grid.pairs
              for one in pair.marks}
    built = {grid.pairs[0].phrase for grid in grids if grid.pairs}
    for number, one in enumerate(marks, start=1):
        if one.title is None and one.text is None:
            if one.phrase is not None and one.phrase not in built:
                raise DirectFailure(
                    f"Пометка {number} ({one.where}) названа подстановкой, "
                    f"которой в матрице нет. Комплект без шаблонов строится "
                    f"одной матрицей на весь набор, и адресовать в нём "
                    f"подстановку нечему.")
            continue
        if id(one) in landed:
            continue
        raise DirectFailure(
            f"Пометка {number} ({one.where}) не досталась ни одной "
            f"комбинации. Адрес разобрался, но такой пары в матрице нет — "
            f"чаще всего это фраза у комплекта без шаблонов: подстановки там "
            f"не делаются, и матрица одна на весь комплект. Посчитанная "
            f"счётчиком и не показанная нигде, такая пометка — потерянное "
            f"суждение.")


def composition_problems(kit) -> list:
    """Число элементов комплекта против пределов справочника.

    Это факт, а не суждение: восьмой заголовок Директ не примет, и захотеть
    обратного нельзя — значит, проверяет код.

    Правило до этого стояло на одном пути из двух: `filled` перебор отвергала,
    а комплект, пришедший восемью заголовками прямо из брифа, доезжал до
    чистого отчёта с `blocking: false`. Ровно тот дефект, который в этом
    репозитории повторялся трижды, — правило поставлено на одну ветку и забыто
    на другой.

    Считает справочник через `writer.Limits.collection_problems`: второго
    счётчика не заводится, и числа берутся оттуда же, откуда их берёт конвейер."""
    from writer import Limits

    known = Limits.load()
    said = []
    for path, values, name in (
            ("ResponsiveAd.Titles", kit.titles, "заголовков"),
            ("ResponsiveAd.Texts", kit.texts, "текстов")):
        for one in known.collection_problems(path, list(values)):
            said.append(f"{name}: {one}")
    return said


def depends_on_phrase(field, value, limits=None) -> bool:
    """Меняется ли значение поля от фразы показа.

    Механизмов два, и решётками дело не исчерпывается. Шаблон `#…#` подставляет
    саму фразу; `{param1}` и `{param2}` подставляют значения уровня фразы —
    `Keyword.UserParam1` и `2`, — и у каждой строки набора они свои.

    Макрос Директа сюда не относится: его подставляет сам Директ при показе, и
    от строки набора он не зависит."""
    from templates import PARAM, Template, tokens

    if Template.parse(field, value, limits=limits).has_templates:
        return True
    return any(kind == PARAM for _name, kind in tokens(value))


def _shown_for(report, phrase) -> dict:
    """Комплект в том виде, в каком он покажется по **этой** фразе.

    Похоже на `templates.Report.shown_for`, но отбирает варианты по самому
    объекту фразы, а не по её тексту, и это не придирка. Набор данных —
    таблица, и одна и та же фраза встречается в ней дважды с разными
    параметрами уровня фразы: тот же запрос, другая цена в `{param1}`. Отбор по
    тексту вернул бы варианты обеих строк, и комплект `1 × 1` превратился бы в
    четыре заголовка и четыре текста."""
    out = {}
    for one in report.variants:
        if one.phrase is not phrase:
            continue
        out.setdefault(one.field, []).append((one.place, one.shown))
    return {field: [shown for _place, shown in sorted(
        group, key=lambda pair: (pair[0] is None, pair[0]))]
        for field, group in out.items()}


class _LengthsOnly:
    """Отчёт по длинам без набора фраз: проверка значения без подстановки.

    Отдельный класс, а не `templates.Report` с пустым списком вариантов:
    у того `ok` считается и по вариантам, и отчёт без единого варианта выходил
    бы чистым — ровно та беда, от которой `templates._some_phrases`
    отказывается работать без фраз. Здесь список вариантов пуст **законно**, и
    сказать об этом надо явно."""

    __slots__ = ("problems",)

    def __init__(self, problems):
        self.problems = list(problems)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        said = ("замечаний к длинам нет" if self.ok
                else f"замечаний к длинам {len(self.problems)}")
        return f"{said}, подстановка не проверялась — фраз не передано"

    def lines(self) -> list:
        return [self.summary()] + [f"  ✗ {one}" for one in self.problems]

    def __repr__(self) -> str:
        return f"<Lengths {self.summary()}>"


def _place_name(field, place) -> str:
    """Как назвать место человеку: «заголовок 6», «ссылка»."""
    from templates import FIELD_NAMES

    name = FIELD_NAMES.get(field, field)
    return name if place is None else f"{name} {place}"


def _without_phrases(items, limits) -> "_LengthsOnly":
    from templates import field_problems

    problems = []
    for field, place, value in items:
        for one in field_problems(field, value, limits=limits):
            problems.append(f"{_place_name(field, place)}: {one}")
    return _LengthsOnly(problems)


# --------------------------------------------------------------------------
# Достройка неполного комплекта
# --------------------------------------------------------------------------

class Slot:
    """Незанятое место комплекта и ограничения справочника на него.

    Не «предложенный заголовок»: слов модуль не пишет и о смысле не судит.
    Здесь только то, что новому элементу придётся выполнить по справочнику."""

    __slots__ = ("kind", "place", "limit", "narrow_max", "word_max")

    def __init__(self, kind: str, place: int, *, limit, narrow_max, word_max):
        self.kind, self.place = kind, place
        self.limit, self.narrow_max, self.word_max = limit, narrow_max, word_max

    @property
    def where(self) -> str:
        said = "заголовок" if self.kind == "titles" else "текст"
        return f"{said} {self.place}"

    def row(self) -> dict:
        return {"kind": self.kind, "place": self.place, "limit": self.limit,
                "narrow_max": self.narrow_max, "word_max": self.word_max}

    def line(self) -> str:
        said = [f"не длиннее {self.limit}"]
        if self.narrow_max is not None:
            said.append(f"узких не более {self.narrow_max}")
        if self.word_max is not None:
            said.append(f"слово не длиннее {self.word_max}")
        return f"  {self.where}: {', '.join(said)}"

    def __repr__(self) -> str:
        return f"<Slot {self.where}>"


def slots(kit, *, limits=None) -> list:
    """Чего в комплекте не хватает до предела — с ограничениями на каждое место.

    **Это предел, а не норма.** Семь заголовков — максимум формата, и правило
    `AD-01` разрешает урезанный пул намеренно: там, где трафика мало, алгоритму
    нечего оптимизировать. Три сильных заголовка лучше семи, из которых четыре
    написаны для заполнения, — слабый элемент будет показан наравне с
    остальными.

    Поэтому функция отвечает на вопрос «сколько мест осталось», а не «сколько
    надо дописать». Ответ на второй даёт человек."""
    from templates import limits_for

    known = limits_for(limits)
    out = []
    for kind, values, field, ceiling in (
            ("titles", kit.titles, TITLE_FIELD, _titles_max()),
            ("texts", kit.texts, TEXT_FIELD, _texts_max())):
        rule = known.texts.get(field) or {}
        for place in range(len(values) + 1, ceiling + 1):
            out.append(Slot(kind, place,
                            limit=rule.get("max_length"),
                            narrow_max=rule.get("narrow_max"),
                            word_max=rule.get("max_word_length")))
    return out


def filled(kit, *, titles=(), texts=()):
    """Комплект, дописанный предложенными элементами. Исходные — нетронуты.

    Сохранность исходных здесь не пожелание, а требование `S-05`: сценарий
    доводит до полного комплекта объявления после автоконвертации, и заголовок,
    потерянный при достройке, — это текст, который человек писал и который
    исчез молча. Дописанное идёт **в хвост**, порядок прежних не меняется.

    Переполнение — отказ, а не обрезка. Обрезанный молча хвост выглядел бы
    успешной достройкой, а до Директа доехало бы не то, что показали."""
    ceiling = {"titles": _titles_max(), "texts": _texts_max()}
    out = {}
    for kind, have, more in (("titles", kit.titles, titles),
                             ("texts", kit.texts, texts)):
        grown = list(have) + [str(one) for one in more]
        if len(grown) > ceiling[kind]:
            said = "заголовков" if kind == "titles" else "текстов"
            raise DirectFailure(
                f"В комплекте стало бы {said} {len(grown)} при пределе "
                f"{ceiling[kind]}: было {len(have)}, дописывают {len(more)}. "
                f"Директ отказал бы всему пакету; какие из них лишние, "
                f"решает человек, а не обрезка по счётчику."
            )
        out[kind] = grown
    return kit.but(titles=out["titles"], texts=out["texts"])
