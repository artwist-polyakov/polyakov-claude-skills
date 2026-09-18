"""Ключевые фразы, минус-фразы и автотаргетинг: разбор, пределы, операции."""

from __future__ import annotations

import re
import unicodedata

import policy as policies
from config import DirectFailure, excerpt, short
from writer import CLEAR, LIMITS_PATH, WHOLE_ACCOUNT, Limits, Operation

# Сервисы, к которым обращается модуль. Имена канонические — те же, что у
# `protocol.service_key`.
KEYWORDS = "keywords"

# Как объект зовётся человеку. Автотаргетинг живёт тем же сервисом, что и
# фразы, но фразой не является: подписанный ею, он получает согласие, данное
# не ему. Подпись поэтому берётся по признаку, а не по имени операции.
PHRASE_SAID = "фраза"
AUTOTARGETING_SAID = "автотаргетинг"


def said_of(text) -> str:
    """Как назвать объект в предпросмотре: фразой или автотаргетингом."""
    return AUTOTARGETING_SAID if is_autotargeting(text) else PHRASE_SAID
SHARED_SETS = "negativekeywordsharedsets"
CAMPAIGNS = "campaigns"
GROUPS = "adgroups"

# Текст, которым Директ обозначает автотаргетинг в сервисе `Keywords`.
AUTOTARGETING = "---autotargeting"

# Уровни минус-фраз и ключ суммарной длины у каждого в `limits.json`.
# Раздельно, потому что числа разные: см. док-строку модуля.
NEGATIVE_TOTALS = {
    "adgroup": "adgroup_total_length",
    "campaign": "campaign_total_length",
    "shared_set": "shared_set_total_length",
}

# Сколько наборов из библиотеки допускает уровень.
SHARED_SET_LIMITS = {
    "adgroup": "shared_sets_per_adgroup",
    "campaign": "shared_sets_per_campaign",
}

# Что не входит в суммарную длину минус-фраз: пробелы, дефисы и операторы
# (`limits.json`, `keywords.negative_keywords`, `length_comment`).
NOT_COUNTED = set(' -!+"[]')

# Операторы, которые могут стоять перед словом внутри ключевой фразы.
OPERATORS = '!+"[]'

# Пути, где значение сравнивается как минус-фраза: Директ нормализует их при
# записи — `чертёж` возвращается как `чертеж`, а перед фразой появляется
# оператор фиксации формы. Дословное сравнение
# объявило бы это потерей данных и остановило бы пакет на исправной записи.
CAMPAIGN_PHRASE_PATHS = ("NegativeKeywords.Items",)
GROUP_PHRASE_PATHS = ("NegativeKeywords.Items",)
SHARED_SET_PHRASE_PATHS = ("NegativeKeywords",)

# Состояния и статусы фразы (`API_OBJECTS.md`, раздел 7; перечень взят у самого
# Директа заведомо неверным именем в `FieldNames`, замер 29.08.2026).
STATES = ("ON", "OFF", "SUSPENDED")
STATUSES = ("ACCEPTED", "DRAFT", "REJECTED", "UNKNOWN")

# Категории автотаргетинга и признаки упоминания брендов — современный набор
# (`AutotargetingSettings`). Устаревшую структуру `AutotargetingCategories`
# скилл не пишет: справочник прямо говорит, что вместе с новой её присылать
# нельзя, и что старая скоро перестанет поддерживаться.
CATEGORIES = ("Exact", "Narrow", "Alternative", "Accessory", "Broader")
BRAND_OPTIONS = ("WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand")

# Во что Директ переводит фразу операцией жизненного цикла.
#
# У фразы три состояния, и `OFF` среди них — не «выключена», а «ещё не прошла
# модерацию или отклонена ею» (справочник, «Статус и состояние фразы»).
# Поэтому:
#
# `suspend` детерминирована — остановленная фраза становится `SUSPENDED`, и
# это замерено на `api-artwist-test` 29.08.2026: `ON → SUSPENDED`.
#
# `resume` — нет. Принятая модерацией фраза вернётся в `ON` (тем же замером:
# `SUSPENDED → ON`, причём кампания в это время стояла остановленной, то есть
# состояние родителя на `State` фразы не переносится). А фраза-черновик или
# отклонённая вернётся в `OFF`, потому что это её собственное состояние по
# модерации. Подставить одно из двух молча значило бы либо ложно останавливать
# пакет, либо — что хуже — объявить успехом переход, которого не было.
TRANSITIONS = {
    "suspend": ("State", "SUSPENDED"),
    "resume": ("State", None),
}

WHY_UNDETERMINED = {
    "resume": "у фразы `OFF` означает не «выключена», а «не прошла модерацию»; "
              "принятая модерацией фраза вернётся в ON, черновик и "
              "отклонённая — в OFF",
}

UNDETERMINED = frozenset(name for name, (_, value) in TRANSITIONS.items()
                         if value is None)
assert UNDETERMINED == set(WHY_UNDETERMINED), (
    "у каждой недетерминированной операции должна быть названа причина"
)

LIFECYCLE_RU = {
    "suspend": "остановка",
    "resume": "возобновление",
    "delete": "удаление",
}

# Служебные слова русского языка: внутри фразы они не несут смысла, и
# кросс-минусовка их не учитывает — ни как обязательные при сравнении, ни как
# кандидаты в минус-слова.
#
# Перечень намеренно **короткий**. Ошибка в одну сторону дешёвая: незамеченное
# служебное слово делает фразу строже, и минус-слов добавится меньше, чем
# могло бы. Ошибка в другую сторону — дорогая: слово, объявленное служебным
# зря, выбрасывается из набора значащих, под фразу подходит больше соседей, и
# минус-слов добавится **больше**, чем нужно. Именно так кросс-минусовка и
# обнуляет показы у широкой группы.
#
# Поэтому здесь нет вопросительных слов — `как`, `где`, `почему`, `зачем`. Директ
# считает стоп-словами и их, но для нас они значащие: `KW-01` их как раз
# минусует, и приравнять «как выбрать дымоход» к «дымоход» значит заминусовать
# коммерческую фразу информационной.
#
# У рабочей кросс-минусовки `zoomkit-ru` на этом месте стоит морфологический
# разбор `mystem`: он отбрасывает союзы, междометия, частицы и предлоги по
# грамматическим признакам. Внешней зависимости здесь нет (`CONVENTIONS.md`),
# поэтому её заменяет список, и разница названа: список короче разбора, значит
# кросс-минусовка получается осторожнее, а не смелее.
STOP_WORDS = frozenset("""
и а но или да же ли бы б не ни
в во на за к ко с со из изо от ото до по о об обо при про для у над под перед
через без между около вокруг вместо кроме сквозь ради
я ты мы вы он она оно они мой моя моё мои твой твоя твои наш наша наши ваш
ваша ваши свой своя свои его её их себя
чтобы если хотя ибо либо нибудь то это эта этот эти тот та те
""".split())


def is_autotargeting(item) -> bool:
    """Автотаргетинг ли это — по тексту, а не по типу объекта.

    Признака типа у `Keywords.get` нет вовсе: сервис отдаёт фразы и
    автотаргетинги одним списком с одинаковым набором полей. Отличает их текст
    `---autotargeting` — замерено на живом кабинете, записано в
    исходная проверка API.

    Принимает и прочитанный объект, и голую строку: вызывающему коду
    попадается то и другое, а две проверки на один признак разошлись бы."""
    text = item.get("Keyword") if isinstance(item, dict) else item
    return isinstance(text, str) and text.strip() == AUTOTARGETING


def fits_capacity(group, inside, adding: int) -> None:
    """Влезут ли добавляемые фразы в группу: `objects.keywords_per_adgroup`.

    Считается конечное состояние — сколько в группе станет, а не сколько
    добавляют. Поштучная проверка тут бессмысленна: каждая фраза по
    отдельности влезает всегда, и предел переступает только их сумма с уже
    лежащими.

    Отказ Директа поэлементный: пакет, перешагнувший потолок, применяется до
    него, а остаток отвергается. Группа остаётся заполненной наполовину, и
    какой именно половиной — Директ не выбирает осмысленно.

    В счёт идут **все** строки, которые вернул `Keywords.get`, включая
    автотаргетинг: он живёт в той же таблице объектов и отдаётся тем же
    списком. Считает ли его сам Директ, замером
    не проверено — на 200 фраз замер не ставился. Ошибка возможна на единицу и
    только в сторону отказа на фразу раньше, а это безопасная сторона у
    предела, который иначе применяет половину пакета."""
    rule = (Limits.load().data.get("objects") or {}).get("keywords_per_adgroup")
    limit = rule.get("default") if isinstance(rule, dict) else None
    if not limit:
        raise DirectFailure(
            f"В {short(LIMITS_PATH)} нет предела числа фраз на группу "
            f"(`objects.keywords_per_adgroup`). Без него запись шла бы вслепую, "
            f"а отказ приходил бы от Директа уже за баллы."
        )
    total = len(inside) + int(adding)
    if total > int(limit):
        raise DirectFailure(
            f"В группе {group} фраз стало бы {total} при пределе {limit} "
            f"(справочник лимитов, `objects.keywords_per_adgroup`); сейчас их "
            f"{len(inside)}, добавляют {int(adding)}. Отказ Директа "
            f"поэлементный: пакет применился бы до потолка, а остаток был бы "
            f"отвергнут — и в группе осталась бы половина списка."
        )


def keywords_rule() -> dict:
    """Раздел `keywords` справочника лимитов."""
    rule = (Limits.load().data.get("keywords") or {})
    if not isinstance(rule, dict) or not rule:
        raise DirectFailure(
            f"В {short(LIMITS_PATH)} нет раздела ограничений на ключевые "
            f"фразы. Без него запись шла бы вслепую, а отказ приходил бы от "
            f"Директа уже за баллы."
        )
    return rule


def counted_length(phrase: str) -> int:
    """Длина минус-фразы так, как её считает Директ.

    Пробелы, дефисы и операторы в суммарную длину не входят — так сказано в
    самом справочнике (`limits.json`, `keywords.negative_keywords`,
    `length_comment`)."""
    return sum(1 for character in phrase if character not in NOT_COUNTED)


def negative_problems(items, level: str) -> list:
    """Что не так с минус-фразами этого уровня — по справочнику и до запроса.

    Уровень называется явно: предел суммарной длины у группы и набора 4096, а
    у кампании 20 000, и один счётчик на все уровни отвергал бы законный
    список кампании впятеро раньше предела."""
    if level not in NEGATIVE_TOTALS:
        raise DirectFailure(
            f"Уровень минус-фраз «{excerpt(level, 32)}» неизвестен. Их четыре, "
            f"и предел суммарной длины у каждого свой: "
            f"{', '.join(sorted(NEGATIVE_TOTALS))}."
        )
    rule = keywords_rule().get("negative_keywords")
    if not isinstance(rule, dict):
        raise DirectFailure(
            f"В {short(LIMITS_PATH)} нет ограничений на минус-фразы. Без них "
            f"запись шла бы вслепую, а отказ приходил бы от Директа за баллы."
        )
    said = []
    total = 0
    for phrase in items:
        if not isinstance(phrase, str) or not phrase.strip():
            said.append("пустая минус-фраза")
            continue
        if phrase.lstrip().startswith("-"):
            # Минус перед первым словом Директ не ждёт: он и так знает, что
            # фраза минусовая. Присланный, он становится частью слова, и
            # минус-фраза `-бесплатно` не срабатывает вовсе.
            said.append(f"«{excerpt(phrase, 40)}»: минус перед первым словом — "
                        f"минус-фраза указывается без него")
        total += counted_length(phrase)
        words = phrase.split()
        if rule.get("max_words") and len(words) > rule["max_words"]:
            said.append(f"«{excerpt(phrase, 40)}»: слов {len(words)} при "
                        f"пределе {rule['max_words']}")
        long = [one for one in words
                if rule.get("max_word_length")
                and len(one.strip(OPERATORS + "-")) > rule["max_word_length"]]
        if long:
            said.append(f"«{excerpt(phrase, 40)}»: слово длиннее "
                        f"{rule['max_word_length']} символов")
    limit = rule.get(NEGATIVE_TOTALS[level])
    if limit and total > limit:
        said.append(f"суммарная длина {total} при пределе {limit} на уровне "
                    f"«{level}» (пробелы, дефисы и операторы не в счёт)")
    return said


def fits_negative(items, level: str) -> None:
    """Отказ, если минус-фразы уровня не проходят по справочнику."""
    said = negative_problems(items, level)
    if said:
        raise DirectFailure(
            "Минус-фразы не проходят по справочнику: " + "; ".join(said[:5])
            + ". Директ откажет, и отказ уронит весь пакет целиком."
        )


def fits_shared_sets(ids, level: str) -> None:
    """Сколько наборов минус-фраз допускает уровень — по справочнику."""
    if level not in SHARED_SET_LIMITS:
        raise DirectFailure(
            f"Наборы минус-фраз подключаются к кампании и к группе, а не к "
            f"«{excerpt(level, 32)}»."
        )
    limit = keywords_rule().get(SHARED_SET_LIMITS[level])
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise DirectFailure(
            f"В {short(LIMITS_PATH)} нет предела наборов минус-фраз на уровне "
            f"«{level}». Без него запись шла бы вслепую, а отказ приходил бы "
            f"от Директа уже за баллы."
        )
    if len(ids) > limit:
        raise DirectFailure(
            f"Наборов минус-фраз {len(ids)} при пределе {limit} на уровне "
            f"«{level}». Директ откажет, и отказ уронит весь пакет целиком."
        )


def keyword_problems(text) -> list:
    """Что не так с ключевой фразой — по справочнику и до запроса.

    Считается так же, как считает Директ: длина — со всеми минус-словами и
    операторами, но последовательность `-!` идёт за один символ; слова — без
    стоп-слов и минус-слов."""
    rule = keywords_rule()
    if not isinstance(text, str) or not text.strip():
        return ["пустая ключевая фраза"]
    said = []
    length = len(text.replace("-!", "-"))
    limit = rule.get("keyword_max_length")
    if limit and length > limit:
        said.append(f"длина {length} при пределе {limit}")
    words, minuses = split_phrase(text)
    counted = [word for word in words if bare(word) not in STOP_WORDS]
    if rule.get("keyword_max_words") and len(counted) > rule["keyword_max_words"]:
        said.append(f"значащих слов {len(counted)} при пределе "
                    f"{rule['keyword_max_words']} (стоп-слова и минус-слова "
                    f"не в счёт)")
    long = [word for word in words + minuses
            if rule.get("keyword_max_word_length")
            and len(bare(word)) > rule["keyword_max_word_length"]]
    if long:
        said.append(f"слово длиннее {rule['keyword_max_word_length']} "
                    f"символов: {excerpt(bare(long[0]), 40)}")
    return said


def fits_keyword(text) -> None:
    # OR-последовательность отвергается отдельно от справочника, и причина у
    # неё другая. Правило `KW-11`: в интерфейсе такая запись не работает, в
    # выгрузке Excel работает, а как отвечает `Keywords.add` — не проверено ни
    # на одном кабинете. Сказать здесь «Директ откажет» значило бы утверждать
    # ровно то, чего никто не знает; беда не в отказе, а в том, что ответ
    # неизвестен, и «примет и покажет непонятно по чему» хуже отказа.
    # Прочитанную OR-последовательность скилл сохраняет как есть — запрет
    # только на запись.
    if isinstance(text, str) and OR_SEQUENCE.search(text):
        raise DirectFailure(
            f"Фраза «{excerpt(text, 60)}» содержит OR-последовательность вида "
            f"`(а|б)`. Скилл такие не пишет, пока не проверено, как на них "
            f"отвечает API: в интерфейсе кабинета запись не работает, в "
            f"выгрузке Excel работает, а ответ `Keywords.add` не замерен ни "
            f"разу (references/PLAYBOOK.md). Уже стоящие в кабинете "
            f"сохраняются как есть — запрет только на запись."
        )
    said = keyword_problems(text)
    if said:
        raise DirectFailure(
            f"Фраза «{excerpt(text, 60)}» не проходит по справочнику: "
            + "; ".join(said[:5])
            + ". Директ откажет, и отказ уронит весь пакет целиком."
        )


# --------------------------------------------------------------------------
# Разбор фразы
# --------------------------------------------------------------------------

# Знаки, которые к слову не относятся и при сравнении со словарём снимаются.
# Операторы и минус — сюда же: `!печь`, `-печь` и `печь` — одно слово, и
# считать их разными значило бы заминусовать уже заминусованное.
TRIMMED = OPERATORS + "-.,:;?="

# OR-последовательность: круглые скобки с вертикальной чертой внутри. Правило
# `KW-11`: скилл такие фразы читает и сохраняет, но не пишет.
OR_SEQUENCE = re.compile(r"\([^()]*\|[^()]*\)")


def bare(word: str) -> str:
    """Слово без операторов и без минуса: то, что сравнивается со словарём."""
    return word.strip(TRIMMED).lower()


def split_phrase(text: str):
    """Слова фразы и её минус-слова, каждое со своими операторами.

    Минус-словом считается всё после пробела с минусом. Отделять их
    обязательно: минус-слово в наборе значащих сделало бы фразу с минусами
    непохожей на себя же без них, и кросс-минусовка добавляла бы уже
    добавленное."""
    words, minuses = [], []
    for token in re.split(r"\s+", str(text).strip()):
        if not token:
            continue
        if token.startswith("-") and len(token) > 1:
            minuses.append(token)
        else:
            words.append(token)
    return words, minuses


def significant(text: str) -> dict:
    """Значащие слова фразы: `{слово: форма минус-слова}`.

    Пустой словарь означает, что фразу трогать нельзя, и это не то же самое,
    что «значащих слов нет». Так помечены два случая:

    * **автотаргетинг** — не фраза вовсе, дописывать ему минус-слова некуда;
    * **фраза в кавычках** — оператор `"…"` уже отсекает всё лишнее, и
      кросс-минусовка ей ничего не добавит, зато сломает синтаксис, дописав
      минус-слово за закрывающей кавычкой. Так же поступает рабочая
      кросс-минусовка `zoomkit-ru`."""
    # OR-последовательность сюда же: дописать ей минус-слово значит переписать
    # фразу, которую писать нельзя вовсе (`KW-11`). Пропуск здесь — это её
    # сохранение как есть, а отказ остановил бы разведение всей группы из-за
    # одной фразы, которую всё равно не трогают.
    if is_autotargeting(text) or '"' in text or OR_SEQUENCE.search(text):
        return {}
    words, _ = split_phrase(text)
    found = {}
    for word in words:
        # Оператор фиксации формы переносится в минус-слово: `!печь` и `+для`
        # заминусовываются как `-!печь`, иначе Директ снимет фиксацию, а без
        # неё стоп-слово из минус-фразы выпадает (`KW-03`).
        fixed = word[:1] in ("!", "+")
        naked = bare(word)
        if not naked or naked in STOP_WORDS:
            continue
        found[naked] = ("-!" if fixed else "-") + naked
    return found


def already_minused(text: str, word: str) -> bool:
    """Стоит ли это слово во фразе минус-словом.

    Проверяется по разобранным минус-словам, а не поиском подстроки: `-бани`
    внутри `-банинг` подстрокой находится, а минус-словом не является, и
    кросс-минусовка сочла бы работу сделанной."""
    _, minuses = split_phrase(text)
    return any(bare(one) == bare(word) for one in minuses)


def cross_minus(records, *, single_word_only: bool = False) -> dict:
    """Кросс-минусовка списка фраз: `{идентификатор: новый текст}`.

    Правило то же, что у рабочей кросс-минусовки `zoomkit-ru`: если соседняя
    фраза содержит **все** значащие слова этой, она уводит у неё часть трафика,
    и лишние слова соседа дописываются этой фразе минус-словами.

    `single_word_only` сужает правило до соседей, отличающихся ровно на одно
    слово, — так же, как это делает `KeywordsResearch.deduplicate` с
    операцией `ELIMINATE_OVERLAPPING`. Полное правило шире и потому опаснее:
    у широкой фразы соседей много, и минус-слов ей достанется столько, что
    показов может не остаться вовсе. Ровно поэтому кросс-минусовка отнесена к
    необратимому по последствиям.

    Возвращаются только **изменившиеся** фразы: запись без изменения прошла бы
    конвейер целиком и отчиталась успехом, ничего не сделав."""
    known = []
    for record in records:
        text = record.get("Keyword")
        known.append((record, significant(text or ""), text))
    changed = {}
    for record, mine, text in known:
        if not mine:
            continue
        additions = []
        for other, theirs, _ in known:
            if other is record or not theirs:
                continue
            if not set(mine) <= set(theirs):
                continue
            extra = [theirs[word] for word in theirs if word not in mine]
            if single_word_only and len(extra) != 1:
                continue
            additions += extra
        wanted = text
        for addition in sorted(set(additions)):
            if not already_minused(wanted, addition):
                wanted = f"{wanted} {addition}"
        if wanted != text:
            changed[record["Id"]] = wanted
    return changed


def separated(records, changed: dict) -> list:
    """Какие группы разводит кросс-минусовка: тройки «где, с кем, сколько фраз».

    Считаются **фразы**, а не пары: одной фразе минус-слова достаются от
    нескольких соседей сразу, и счёт по парам показал бы больше правок, чем
    их есть. Своя группа от чужой не отделяется здесь — это делает тот, кто
    печатает: разведение внутри группы и разведение между группами читаются
    по-разному, а считаются одинаково."""
    place = {record["Id"]: record.get("AdGroupId") for record in records}
    words = {record["Id"]: significant(record.get("Keyword") or "")
             for record in records}
    pairs = {}
    for identifier, wanted in changed.items():
        mine = words.get(identifier) or {}
        added = {bare(one) for one in split_phrase(wanted)[1]}
        for other, theirs in words.items():
            if other == identifier or not theirs or not set(mine) <= set(theirs):
                continue
            if not {word for word in theirs if word not in mine} & added:
                continue
            pairs.setdefault((place.get(identifier), place.get(other)),
                             set()).add(identifier)
    return [(here, there, len(found)) for (here, there), found in sorted(
        pairs.items(), key=lambda item: (str(item[0][0]), str(item[0][1])))]


# --------------------------------------------------------------------------
# Чтение перед записью
# --------------------------------------------------------------------------

# Поля фразы, которые скилл читает до и после записи. Перечень взят у самого
# Директа: заведомо неверное имя в `FieldNames`, и он называет допустимые в
# тексте отказа (замер 29.08.2026).
#
# `Productivity` не запрашивается: справочник говорит, что параметр всегда
# возвращает `null`, и место в срезе он занимал бы зря.
KEYWORD_FIELDS = (
    "Id", "Keyword", "AdGroupId", "CampaignId", "State", "Status",
    "ServingStatus", "Bid", "ContextBid", "AutotargetingSearchBidIsAuto",
    "StrategyPriority", "UserParam1", "UserParam2",
)


# Значения подстановочных переменных фразы: имя человеку → имя поля Директа.
# Имя человеку — то же, каким параметр записан в адресе (`{param1}`), и то же,
# каким его знает движок подстановок (`templates.PHRASE_PARAMS`). Третьего
# написания у одного механизма быть не должно: разойдясь, они дадут аргумент,
# который принимается и не делает ничего.
USER_PARAMS = {"param1": "UserParam1", "param2": "UserParam2"}

PARAM_NAMES = {field: name for name, field in USER_PARAMS.items()}

PARAM_TEXTS = {"UserParam1": "Keyword.UserParam1",
               "UserParam2": "Keyword.UserParam2"}

assert set(PARAM_TEXTS) == set(USER_PARAMS.values()), (
    "у каждого параметра фразы должно быть названо правило справочника"
)


def fits_params(named) -> dict:
    """Значения параметров, проверенные до запроса: имя поля → значение.

    `CLEAR` доходит сюда как есть и уходит в запрос `null`: у поля три
    состояния, и свести «снять» с «не трогать» нельзя — Директ отвечает на них
    противоположным.

    Пустая строка отвергается. Молча считать её «оставить как было» нельзя:
    команда отчиталась бы успехом, а старое значение осталось бы в кабинете.
    Считать очисткой — тоже: признак, который получается случайным пустым
    аргументом, две просьбы отличать перестаёт.

    Порядок в ответе постоянный — `param1`, потом `param2`, — а не порядок
    аргументов: перечень уходит человеку в предпросмотр, и «то же самое, но в
    другом порядке» читается как другая правка."""
    unknown = sorted(set(named or ()) - set(USER_PARAMS))
    if unknown:
        raise DirectFailure(
            f"Параметров фразы «{', '.join(unknown)}» нет: их два — "
            f"{', '.join(USER_PARAMS)}."
        )
    found = {}
    for name in USER_PARAMS:
        if name not in (named or {}):
            continue
        value = named[name]
        if value is CLEAR:
            found[USER_PARAMS[name]] = CLEAR
            continue
        if not isinstance(value, str) or not value.strip():
            raise DirectFailure(
                f"Пустое значение `{{{name}}}`. Очистка называется явно: "
                f"`--clear {name}`. Чтобы оставить прежнее значение, уберите "
                f"аргумент — пропущенное поле Директ не трогает."
            )
        found[USER_PARAMS[name]] = value
    if not found:
        raise DirectFailure(
            "Не сказано, какие значения параметров ставить: назовите "
            "`--param1`, `--param2` или `--clear`."
        )
    return found


def param_texts(items) -> dict:
    """Правила справочника — только на пути, где есть что проверять.

    Правило, объявленное на отсутствующий путь, движок отвергает, и он прав:
    от опечатки в пути оно неотличимо, а опечатка молча не проверяет ничего.
    Очищаемый путь сюда не попадает по той же причине, что и у комплекта
    объявления: `null` — не значение, у которого бывает длина, правило на нём
    жалуется на всё, и законная очистка вставала бы «остановлено валидацией»."""
    present = {field for item in items for field, value in item.items()
               if field in PARAM_TEXTS and value is not None}
    return {field: rule for field, rule in PARAM_TEXTS.items()
            if field in present}


def read_params() -> dict:
    """Параметры `Keywords.get` для чтения до и после записи.

    Настройки автотаргетинга запрашиваются **всегда**, обоими списками. Иначе
    правка категорий сверялась бы с пустотой: поле не вернулось и поле не
    менялось читаются одинаково, а `Keywords.update` заменяет набор целиком."""
    return {
        "FieldNames": list(KEYWORD_FIELDS),
        "AutotargetingSettingsCategoriesFieldNames": list(CATEGORIES),
        "AutotargetingSettingsBrandOptionsFieldNames": list(BRAND_OPTIONS),
    }


# --------------------------------------------------------------------------
# Операции конвейера: фразы
# --------------------------------------------------------------------------

# Как называются половины настроек автотаргетинга человеку и под каким путём
# они сверяются. Путь второго уровня, а не корень: списками полей читается
# каждая половина своим (`AutotargetingSettingsCategoriesFieldNames` и
# `…BrandOptionsFieldNames`), а общего имени у поддерева нет вовсе.
SETTINGS_PARTS = (("Categories", "категории автотаргетинга"),
                  ("BrandOptions", "упоминание брендов"))


def settings_changes(name, body, *, service: str = "фраза") -> list:
    """Обещания по настройкам автотаргетинга — по одному на половину.

    Одно на две половины было бы удобнее в записи и хуже во всём остальном:
    сверять его не с чем, а человек читал бы «настройки» там, где менялись
    только категории."""
    if not body:
        return []
    return [policies.Change(object_id=name, what=what,
                            field=f"AutotargetingSettings.{part}",
                            after=body[part], service=service)
            for part, what in SETTINGS_PARTS if part in body]


def fits_settings(body) -> dict:
    """Настройки автотаргетинга, проверенные по перечням справочника.

    Имена категорий и признаков — закрытые перечни, и опечатка в них доезжает
    до Директа: он отвечает отказом, но уже за баллы и уже после того, как
    человек согласился. Проверка одна на оба пути записи — создание и правку:
    поставленная на один, она обходится другим."""
    if not body:
        return {}
    found = {}
    for part, allowed in (("Categories", CATEGORIES),
                          ("BrandOptions", BRAND_OPTIONS)):
        named = body.get(part) or {}
        unknown = sorted(set(named) - set(allowed))
        if unknown:
            raise DirectFailure(
                f"{'Категории автотаргетинга' if part == 'Categories' else 'Признака упоминания брендов'} "
                f"«{', '.join(unknown)}» нет: допустимы {', '.join(allowed)}."
            )
        wrong = sorted(one for one, value in named.items()
                       if value not in ("YES", "NO"))
        if wrong:
            raise DirectFailure(
                f"У настроек автотаргетинга «{', '.join(wrong)}» значение не "
                f"`YES` и не `NO`. Другого перечень не принимает."
            )
        if named:
            found[part] = {name: named[name] for name in allowed if name in named}
    if not found:
        raise DirectFailure(
            "Не сказано, что менять в автотаргетинге: назовите категории или "
            "признаки упоминания брендов."
        )
    return found


def add_operation(group: int, texts, *, settings=None, params=None,
                  guard) -> Operation:
    """Создание фраз в группе: `Keywords.add`.

    `guard` обязателен и умолчания не имеет. Создание конвейер не сторожит
    перечитыванием: снимка у него нет, объекта ещё не существует, и сравнивать
    нечего. Значит окно между чтением команды и записью закрывать больше
    нечем — только условием, которое читает группу заново само. Через него же
    проверяется потолок числа фраз: он тоже про состояние, а не про присланное.

    Автотаргетинг заводится тем же методом и тем же массивом — текстом
    `---autotargeting`, — поэтому отдельной операции у него нет. Настройки
    категорий принимает только он: у обычной фразы их нет, и присланные ей они
    дали бы отказ на весь пакет.

    `params` — значения `{param1}` и `{param2}`, общие на все создаваемые
    объекты: команда задаёт их одним аргументом на вызов, и разложить их по
    фразам поимённо ей нечем. Автотаргетингу они достаются наравне с фразами —
    подставлять их ему некуда, но это суждение, а не факт, и запретом в коде
    просьбу не обходят.

    Имя объекта в плане для этого не годится и годиться не может: фраза
    зовётся «текст · группа», чтобы одна и та же фраза в двух группах не
    слилась в одно создание, — и такого значения в кабинете нет ни у одного
    поля. Поиск сверяет прочитанное с отправленным, а не с именем плана.

    Автотаргетинг попадает в тот же поиск и находится всегда — его заводит
    сам Директ при создании группы, а `Keywords.add` с текстом
    `---autotargeting` второго объекта не создаёт, а правит существующий. Отчёту это не мешает: находка у него и так
    называется находкой, требующей глаз, а не подтверждённой записью, — а
    настройки, применились они или нет, человек сравнит на найденном объекте,
    чего «проверьте кабинет» ему не даёт вовсе."""
    if not texts:
        raise DirectFailure("Не сказано, какие фразы добавлять.")
    # Настройки принимает только автотаргетинг. Присланные вместе с одними
    # обычными фразами, они бы молча пропали: фразы записались бы, команда
    # отчиталась успехом, а половина просьбы не исполнилась. Проверка стоит
    # здесь, а не у вызывающего кода, потому что молчание случается здесь — и
    # случалось бы у каждого следующего вызывающего заново.
    if settings and not any(is_autotargeting(one) for one in texts):
        raise DirectFailure(
            "Настройки категорий и упоминания брендов принимает только "
            "автотаргетинг, а среди добавляемых фраз его нет. Обычной фразе "
            "они не достанутся, и записалась бы половина просьбы: фразы без "
            "настроек, о которых просили."
        )
    cleared = [name for name in USER_PARAMS if (params or {}).get(name) is CLEAR]
    if cleared:
        raise DirectFailure(
            f"Создаваемая фраза просит очистить "
            f"{', '.join('`{' + one + '}`' for one in cleared)}. При создании "
            f"очищать нечего — в `KeywordAddItem` эти поля не `nillable`, и "
            f"`null` Директ там не примет. Просто не передавайте их."
        )
    values = fits_params(params) if params else {}
    settings = fits_settings(settings) if settings else None
    items, labels, changes = [], [], []
    for text in texts:
        auto = is_autotargeting(text)
        if not auto:
            fits_keyword(text)
        item = {"AdGroupId": int(group), "Keyword": text}
        if auto and settings:
            item["AutotargetingSettings"] = settings
        # Значения достаются и автотаргетингу, если о нём просили. Подставлять
        # их ему некуда — текста фразы нет, — но это суждение, а не факт, и
        # запретом в коде просьбу не обходят. Настройки выше устроены
        # иначе не по прихоти: там правило справочника, а не мнение — обычной
        # фразе `AutotargetingSettings` не принадлежит вовсе.
        item.update(values)
        # Имя создаваемого объекта уникально по всей задаче: одна и та же фраза
        # в двух группах — это два разных объекта, и слитые в один они показали
        # бы человеку одно создание вместо двух.
        label = f"{text} · группа {group}"
        items.append(item)
        labels.append(label)
        changes += [
            policies.Change(object_id=label, what="группа", field="AdGroupId",
                            after=item["AdGroupId"], service=said_of(text)),
            policies.Change(object_id=label, what=said_of(text),
                            field="Keyword", after=text, service=said_of(text)),
        ]
        # Обещание — на каждую половину настроек отдельно, как и у правки:
        # общего имени у поддерева нет, читается каждая половина своим списком
        # полей, и обещание про `AutotargetingSettings` целиком сверять было бы
        # не с чем. Правило то же, что в `autotargeting_operation`, и живёт оно
        # в двух местах ровно потому, что путей записи два — создание и правка.
        changes += settings_changes(label, item.get("AutotargetingSettings"))
        changes += [
            policies.Change(object_id=label,
                            what=f"значение {{{PARAM_NAMES[field]}}}",
                            field=field, after=value, service=said_of(text))
            for field, value in values.items()
        ]
    return Operation(
        KEYWORDS, "add", params_key="Keywords", items=items, labels=labels,
        changes=changes, read=read_params(), texts=param_texts(items),
        search=("Keyword", {"AdGroupIds": [int(group)]}), guard=guard,
    )


def params_operation(values: dict, known, *, guard=None) -> Operation:
    """Значения `{param1}` и `{param2}` на фразах: `Keywords.update`.

    `values` — `{идентификатор: {имя параметра: значение или `CLEAR`}}`.

    `known` — прочитанные объекты, `{идентификатор: ответ}`. Обязательный: в
    запрос уходит только то, что прочитано, а по одному идентификатору
    автотаргетинг от фразы неотличим — и подписан в плане он был бы фразой.
    Согласие даётся на показанное, поэтому имя объекта берётся у прочитанного,
    а не у метода.

    Отдельная операция, а не поле в `update_operation`: правка текста и правка
    значений — разные просьбы, и объединять их в одну операцию нельзя. Две
    операции по одному объекту конвейер отвергает, а одна, делающая и то и
    другое, показала бы человеку правку текста там, где он просил параметр."""
    if not values:
        raise DirectFailure(
            "Не сказано, каким фразам ставить значения параметров. Запись без "
            "изменения прошла бы конвейер целиком и отчиталась успехом, ничего "
            "не сделав."
        )
    seen = {int(number): record for number, record in (known or {}).items()}
    items, changes, cleared = [], [], set()
    for identifier, named in values.items():
        number = int(identifier)
        if number not in seen:
            raise DirectFailure(
                f"Фраза {number}: её нет среди прочитанных, и что это за "
                f"объект — неизвестно. Значение параметра принимает фраза, а "
                f"по одному идентификатору она неотличима от автотаргетинга."
            )
        # Автотаргетинг отсюда не изгоняется, и это не недосмотр. Подставлять
        # значение ему некуда — текста фразы у него нет, — но это суждение, а
        # не факт: `KeywordUpdateItem` принимает `UserParam1` у любого объекта
        # сервиса, и захотеть такого можно осознанно. Запретом в коде просьбу
        # не обходят; называет цену команда, а сюда объект приходит
        # уже названным своим именем — «автотаргетинг», а не «фраза», — чтобы
        # человек соглашался на то, что видит.
        said = said_of(seen[number])
        item = {"Id": number}
        for field, value in fits_params(named).items():
            item[field] = None if value is CLEAR else value
            if value is CLEAR:
                cleared.add(field)
            changes.append(policies.Change(
                object_id=number,
                what=("снятие значения" if value is CLEAR else "значение")
                     + f" {{{PARAM_NAMES[field]}}}",
                field=field, after=item[field], service=said))
        items.append(item)
    return Operation(
        KEYWORDS, "update", params_key="Keywords", items=items,
        changes=changes, read=read_params(), texts=param_texts(items),
        clears=tuple(sorted(cleared)), guard=guard,
    )


def update_operation(texts: dict, known, *, batch=None,
                     guard=None) -> Operation:
    """Правка текста фраз: `Keywords.update`.

    `known` — прочитанные объекты, `{идентификатор: ответ}`. Обязательный:
    текст автотаргетинга изменению не подлежит, а узнать автотаргетинг можно
    только по прочитанному объекту — по одному идентификатору он неотличим от
    фразы. Проверка стоит **здесь**, а не у вызывающего кода, потому что
    вызывающих несколько: правка одной фразы, кросс-минусовка, а дальше —
    массовая замена из `S-04`. Правило, поставленное у одного из них,
    обходится теми, где его забыли.

    `batch` задаёт размер пакета. Кросс-минусовка ставит его в единицу: она
    необратима по последствиям, а отказ Директа поэлементный — в пачке
    половина применяется, половина нет."""
    if not texts:
        raise DirectFailure(
            "Не сказано, что менять во фразах. Запись без изменения прошла бы "
            "конвейер целиком и отчиталась успехом, ничего не сделав."
        )
    seen = {int(number): record for number, record in (known or {}).items()}
    items, changes = [], []
    for identifier, text in texts.items():
        lost = int(identifier) not in seen
        if lost or is_autotargeting(seen[int(identifier)]) or is_autotargeting(text):
            raise DirectFailure(
                f"Фраза {identifier}: "
                + ("её нет среди прочитанных, и что это за объект — неизвестно"
                   if lost else
                   "текст автотаргетинга изменению не подлежит — так сказано в "
                   "справочнике `Keywords.update`. Автотаргетинг заводится и "
                   "снимается, а не переписывается")
                + "."
            )
        fits_keyword(text)
        items.append({"Id": int(identifier), "Keyword": text})
        changes.append(policies.Change(
            object_id=int(identifier), what="фраза", field="Keyword",
            after=text, service=PHRASE_SAID))
    return Operation(
        KEYWORDS, "update", params_key="Keywords", items=items,
        changes=changes, read=read_params(), batch_limit=batch, guard=guard,
    )


def all_categories_off(read, identifier: int, categories):
    """Условие: после правки хоть одна категория автотаргетинга останется `YES`.

    Правка идёт `Keywords.update`, и метод здесь назван не для порядка. У него
    запись настроек — правка **на месте**: категории, которых в запросе нет,
    сохраняют прежние значения (`API_OBJECTS.md`, раздел 7.1). У соседнего
    `Keywords.add` поведение обратное — не названное возвращается к умолчанию
    `YES`, — и итог, посчитанный ниже, на том пути был бы неверен.
    Значит вопрос «не выключаются ли все пять» решается не присланным, а итогом
    присланного и того, что уже стоит, — а стоящее меняется независимо. Директ
    отвечает на все пять `NO` кодом 5005.

    Читает условие само и зовётся дважды, как и остальные: между расчётом и
    записью соседнюю категорию успевают выключить."""
    def check(_known) -> list:
        found = read()
        record = found.get(int(identifier)) or {}
        inside = ((record.get("AutotargetingSettings") or {})
                  .get("Categories") or {})
        if not inside:
            # Настроек не прочиталось — судить об итоге не по чему. Молчать
            # здесь безопаснее, чем отказывать: чтение категорий требует своего
            # списка полей, и его отсутствие — вопрос вызывающего кода, а не
            # состояния кабинета.
            return []
        after = {name: (categories or {}).get(name, inside.get(name))
                 for name in CATEGORIES if name in inside or name in (categories or {})}
        if after and all(value == "NO" for value in after.values()):
            # Что именно гасит эта правка: категории, которые были включены и
            # выключаются сейчас. Без них человеку непонятно, чего он лишается —
            # «все выключены» звучит как состояние, а не как следствие правки.
            dying = sorted(name for name in (categories or {})
                           if (categories or {})[name] == "NO"
                           and inside.get(name) == "YES")
            return [
                f"После правки у автотаргетинга {identifier} все категории "
                f"оказались бы выключены, а Директ это запрещает кодом 5005. "
                f"`Keywords.update` пишет настройки правкой на месте: "
                f"неназванные категории сохраняют прежние значения. "
                + (f"Включёнными оставались {', '.join(dying)} — их и "
                   f"выключает эта правка." if dying
                   else "Остальные уже выключены.")
            ]
        return []
    return check


def autotargeting_operation(identifier: int, *, categories=None,
                            brands=None, guard=None) -> Operation:
    """Категории автотаргетинга и признаки упоминания брендов.

    Пишется современной структурой `AutotargetingSettings`. Устаревшую
    `AutotargetingCategories` скилл не отправляет: справочник запрещает
    присылать их вместе и обещает снять старую с поддержки.

    Названо обязано быть хоть что-то: пустая правка прошла бы конвейер целиком
    и отчиталась успехом, ничего не изменив."""
    body = fits_settings({"Categories": categories or {},
                          "BrandOptions": brands or {}})
    item = {"Id": int(identifier), "AutotargetingSettings": body}
    # Обещание даётся на каждую половину отдельно, а не на `AutotargetingSettings`
    # целиком. Причина не в красоте: списком читается каждая половина своим
    # (`AutotargetingSettingsCategoriesFieldNames` и `…BrandOptionsFieldNames`),
    # общего имени у поддерева нет вовсе — и обещание про него сверять было бы
    # не с чем. Заодно человек видит категории и бренды порознь, как они и
    # заданы.
    changes = settings_changes(int(identifier), body, service="автотаргетинг")
    return Operation(
        KEYWORDS, "update", params_key="Keywords", items=[item],
        changes=changes, read=read_params(), guard=guard,
    )


def lifecycle_operation(method: str, ids, *, state=None,
                        autotargetings=(), guard=None) -> Operation:
    """Остановка, возобновление и удаление фраз.

    Все три принимают отбор по идентификаторам, а не массив объектов.
    Ожидаемое состояние называется прямо: без него перечитывание подтвердило
    бы лишь то, что фраза существует, а перешла она или нет — осталось бы
    непроверенным.

    `Keywords.resume` заведён здесь не для полноты. Список, кончающийся
    остановкой, оставляет остановленную фразу без обратного хода: включить её
    обратно было бы нечем, и человеку осталось бы завести фразу заново — с
    новым идентификатором и потерянной статистикой."""
    gone = method == "delete"
    ids = [int(one) for one in ids]
    if not ids:
        raise DirectFailure(f"{LIFECYCLE_RU[method]}: фразы не названы.")
    expect, value = None, None
    if not gone:
        field, value = TRANSITIONS[method]
        value = state or value
        if value is None:
            raise DirectFailure(
                f"Операция «{LIFECYCLE_RU[method]}» не задаёт итогового "
                f"состояния сама: {WHY_UNDETERMINED[method]}. Назовите "
                f"ожидаемое состояние аргументом `--expect-state` — без него "
                f"сверка либо остановит пакет на верном переходе, либо примет "
                f"неверный."
            )
        expect = [{"Id": one, field: value} for one in ids]
    # Подпись — по признаку объекта: те же три операции обслуживают и фразы, и
    # автотаргетинги, и «остановка фразы 205793225524» вместо «остановка
    # автотаргетинга» показывает человеку не тот объект, которому он
    # соглашается.
    apart = {int(one) for one in autotargetings}
    changes = [policies.Change(
        object_id=one, what=LIFECYCLE_RU[method],
        field=None if gone else TRANSITIONS[method][0],
        after=None if gone else value,
        service=AUTOTARGETING_SAID if one in apart else PHRASE_SAID)
        for one in ids]
    return Operation(
        KEYWORDS, method, selection="Ids", items=[{"Id": one} for one in ids],
        expect=expect, changes=changes, read=read_params(), expect_gone=gone,
        guard=guard,
    )


# --------------------------------------------------------------------------
# Операции конвейера: минус-фразы уровня кампании и группы
# --------------------------------------------------------------------------

# Общие поля кампании, которые читает правка минус-фраз. Набор узкий
# намеренно: конвейер сравнивает **весь** прочитанный срез, когда сторожит
# окно между подтверждением и записью, и лишнее поле — это лишний повод
# остановить исправную задачу.
CAMPAIGN_FIELDS = ("Id", "Name", "NegativeKeywords")

CABINET_STATES = ("ARCHIVED", "CONVERTED", "ENDED", "OFF", "ON", "SUSPENDED")

# Типы кампаний и имя их типовой структуры. Живёт здесь, а не у команды ставок:
# перечень наборов минус-фраз лежит внутри той же структуры, и две копии
# таблицы разошлись бы при первом же новом типе.
CAMPAIGN_TYPES = {
    "UNIFIED_CAMPAIGN": "UnifiedCampaign",
    "TEXT_CAMPAIGN": "TextCampaign",
    "MOBILE_APP_CAMPAIGN": "MobileAppCampaign",
    "DYNAMIC_TEXT_CAMPAIGN": "DynamicTextCampaign",
    "CPM_BANNER_CAMPAIGN": "CpmBannerCampaign",
    "SMART_CAMPAIGN": "SmartCampaign",
}

# Медийные кампании: у них корректировки бывают только на уровне группы
# (`API_OBJECTS.md`, раздел 9). Два из трёх типов доступны вообще только на
# чтение статистики — но назвать их аргументом человек может, и отказ должен
# быть один и понятный, а не разный на каждый тип.
MEDIA_CAMPAIGNS = ("CPM_BANNER_CAMPAIGN", "CPM_DEALS_CAMPAIGN",
                   "CPM_FRONTPAGE_CAMPAIGN")

# У каких типов в типовой структуре есть перечень наборов минус-фраз. Замер
# 30.08.2026: `CpmBannerCampaign` и `SmartCampaign` отвечают кодом 8000 —
# поля у них нет вовсе, и запрошенное оно роняет **весь** вызов, а не одну
# кампанию. Поэтому перечень именной, а не «все типы подряд».
SHARED_SET_CAMPAIGNS = ("UnifiedCampaign", "TextCampaign",
                        "MobileAppCampaign", "DynamicTextCampaign")


def campaign_negatives_read() -> dict:
    """Чтение кампании ради минус-фраз: свои и перечень подключённых наборов."""
    params = {"FieldNames": ["Id", "Type", "NegativeKeywords"]}
    for name in SHARED_SET_CAMPAIGNS:
        params[f"{name}FieldNames"] = ["NegativeKeywordSharedSetIds"]
    return params

GROUP_FIELDS = ("Id", "CampaignId", "Name", "NegativeKeywords",
                "NegativeKeywordSharedSetIds")


def replaced_from(before: dict):
    """Условие: список, из которого собрана замена, ещё тот же.

    `Campaigns.update` и `AdGroups.update` пишут минус-фразы и перечень наборов
    **массивом целиком** — это замена, а не дополнение. Замена, собранная по
    устаревшему чтению, молча стирает всё, что добавили между чтением и
    записью: «добавить одну фразу» превращается в «оставить ровно те, что были
    видны мне».

    Сторож окна этого не ловит по построению. Он сличает снимок конвейера с
    перечитыванием, а чтение команды было **до** снимка: к моменту снимка
    чужая правка уже на месте, расхождения нет, и в журнал она уйдёт как
    прежнее состояние.

    Поэтому список, из которого собрана замена, приносится сюда и сверяется:
    разошёлся — задача останавливается. Пересобрать замену молча нельзя:
    человек согласился на показанное, а показано было другое.

    **Имя поля бывает путём.** Минус-фразы кампании лежат у объекта сверху, а
    перечень подключённых наборов — внутри типовой структуры
    (`UnifiedCampaign.NegativeKeywordSharedSetIds`), и та же просьба к тому же
    методу адресуется разной глубиной. Поиск только по верхнему уровню давал
    бы на вложенном поле пустоту, а пустота здесь читается как «список
    вычистили между чтением и записью»: сторож останавливал бы задачу на
    нетронутом кабинете — и останавливал бы **всегда**, то есть выключал бы
    саму операцию."""
    def check(known: dict) -> list:
        said = []
        for identifier, record in sorted((known or {}).items()):
            for field, source in sorted(before.items()):
                now = [str(one) for one in items_of(_dig(record, field))]
                if now != [str(one) for one in source]:
                    said.append(
                        f"объект {identifier}: поле «{field}» изменилось между "
                        f"чтением и записью — было {len(source)} значений, "
                        f"стало {len(now)}. Запись идёт заменой массива целиком, и "
                        f"чужая правка была бы стёрта молча. Повторите команду: "
                        f"замена соберётся по свежему списку."
                    )
        return said
    return check


def _dig(record, field: str):
    """Значение поля по пути с точками. Нет пути — `None`, а не исключение.

    `None` тут законный ответ: поле, которого у объекта нет, и поле, которого
    нет **у этого типа** объекта, снаружи неразличимы, а сторож сравнивает
    списки — пустой список против пустого сходится."""
    value = record
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def all_named(found, named, what: str) -> None:
    """Отказ, если что-то из названного поимённо не прочиталось.

    Правило одно на все команды: человек назвал три объекта, прочиталось два —
    и молча сделать работу над двумя значит отчитаться успехом о том, чего он
    не просил. Причин у пропажи много — объекта нет, он в другом кабинете, он
    закрыт правами, — а следствие одно, и различать их здесь незачем.

    Показу это нужно не меньше, чем записи: перечень, из которого молча выпал
    названный набор, человек прочтёт как полную опись — и решит по ней.

    Живёт здесь, а не у одной из команд, потому что путей несколько, а правило,
    поставленное на один из них, обходится тем, где его забыли."""
    inside = {int(one) for one in (found or ())}
    lost = [one for one in (named or ()) if int(one) not in inside]
    if lost:
        raise DirectFailure(
            f"Не прочитались названные {what}: "
            f"{', '.join(str(one) for one in lost[:10])}"
            + ("…" if len(lost) > 10 else "")
            + ". Их нет, они в другом кабинете или закрыты правами. Работать "
              "над частью названного молча нельзя."
        )


def campaign_here(client, account, accounts, ids) -> None:
    """Отказ, если названная кампания принадлежит не этому кабинету.

    Отдельным вызовом, а не полем ответа, и это замер, а не осторожность.
    Владельца `Campaigns.get` не называет вовсе: из 23 имён `FieldNames` его
    не даёт ни одно, `ClientInfo` — необязательное отображаемое имя, которого
    у единой перфоманс-кампании не бывает совсем, а `RepresentedBy` называет
    агентство, а не кабинет клиента. Проверка «по ответу» поэтому не «хуже», а
    невыполнима: ответ на чужую кампанию побайтно тот же, что на свою.

    Цена — вызов на каждое чтение по идентификаторам; на замере срез кабинета
    стоил 14 баллов против 11 у чтения по `Ids`. У сторожа это вдвое: условие
    конвейер спрашивает дважды — по снимку и по свежему чтению перед записью, —
    и принадлежность спрашивается обоими проходами. Дешевле не выходит:
    добавить `States` к тому же запросу нельзя, замерено. Платится она не за
    строгость: без неё Директ отвечает на запись `8800` «Объект не найден» уже
    **после** подтверждения человеком и тоже за баллы."""
    wanted = [int(one) for one in (ids or ())]
    if not wanted:
        return
    # Объектов вернётся столько, сколько в кабинете кампаний, и до ответа это
    # неизвестно: цена считается по пределу ответа и выходит сверху.
    need = Limits.load().units_cost(CAMPAIGNS, "get")
    here = {int(one["Id"]) for one in client.get_all(
        CAMPAIGNS,
        {"SelectionCriteria": {"States": list(CABINET_STATES)},
         "FieldNames": ["Id"]},
        account=account,
        use_operator_units=lambda: accounts.use_operator_units(
            account, need=need))
        if one.get("Id") is not None}
    strangers = [one for one in wanted if one not in here]
    if strangers:
        raise DirectFailure(
            "Не этого кабинета кампании: "
            + ", ".join(str(one) for one in strangers[:10])
            + ("…" if len(strangers) > 10 else "")
            + f". Прочитались они потому, что отбор по идентификатору "
              f"заголовком `Client-Login` не сужается, а в срезе кабинета "
              f"«{account}» их нет. Назовите тот кабинет, которому они "
              f"принадлежат, — `--account`: под нынешним Директ ответит на "
              f"запись отказом 8800 «Объект не найден»."
        )


def every(*guards):
    """Несколько условий как одно: жалобы складываются, ни одно не теряется.

    Отдельные условия проверяют разное — что владелец существует, что соседи
    позволяют, что потолок не перейдён, — и каждое из них конвейер зовёт на
    обоих проходах. Собранные вместе, они остаются одним аргументом операции:
    иначе пришлось бы выбирать, какое из них приложить, а выбранное одно
    обходится тем, которого не приложили."""
    kept = [one for one in guards if one is not None]
    if not kept:
        return None

    def check(known) -> list:
        said = []
        for one in kept:
            said += list(one(known))
        return said
    return check


def exists_guard(client, account, accounts, service, read, ids, what):
    """Условие: названные объекты существуют и доступны — по свежему чтению.

    Пустой ответ о **детях** ничего не говорит о родителе: `Keywords.get` по
    несуществующей группе и по пустой группе отвечает одинаково — пустотой, и
    принять её за «группа есть, фраз нет» значит показать человеку
    подтверждение и заплатить за заведомо негодную запись. Спрашивать надо сам
    объект."""
    def check(_known) -> list:
        wanted = [int(one) for one in ids]
        request = dict(read)
        request["SelectionCriteria"] = {"Ids": wanted}
        need = Limits.load().units_cost(service, "get", len(wanted))
        found = list(client.get_all(
            service, request, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)))
        try:
            all_named([one.get("Id") for one in found], wanted, what)
        except DirectFailure as failure:
            return [str(failure)]
        return []
    return check


def items_of(value) -> list:
    """Массив Директа: либо `{"Items": [...]}`, либо голый список, либо ничего.

    Обе формы встречаются в одном ответе: `NegativeKeywords` у кампании и
    группы приходит объектом с `Items`, у набора библиотеки — голым списком.
    Разбор, знающий одну форму, молча вернул бы пустоту на другой."""
    if isinstance(value, dict):
        value = value.get("Items")
    return list(value) if isinstance(value, list) else []


def campaign_negative_operation(campaign: int, items, *, was) -> Operation:
    """Минус-фразы кампании: `Campaigns.update`, поле `NegativeKeywords`.

    `was` обязателен и умолчания не имеет: это список, из которого собрана
    замена, и без него запись идёт вслепую поверх чужой правки. Забыть его —
    единственный способ вернуть этот класс обратно, и пусть забытый он будет
    ошибкой вызова, а не тихой дырой. Пустой словарь означает «замены нет»,
    и это осознанный ответ, а не пропуск.

    Значение заменяется целиком, поэтому текущее читается до записи, а
    вызывающий код присылает сюда **итоговый** список, а не добавку."""
    items = list(items)
    fits_negative(items, "campaign")
    item = {"Id": int(campaign), "NegativeKeywords": {"Items": items}}
    changes = [policies.Change(
        object_id=int(campaign), what="минус-фразы кампании",
        field="NegativeKeywords", after=item["NegativeKeywords"],
        service="кампания")]
    return Operation(
        CAMPAIGNS, "update", params_key="Campaigns", items=[item],
        changes=changes, read={"FieldNames": list(CAMPAIGN_FIELDS)},
        phrases=CAMPAIGN_PHRASE_PATHS,
        guard=replaced_from(was) if was is not None else None,
    )


def group_negative_operation(group: int, *, items=None, shared=None, was,
                             arriving=None) -> Operation:
    """Минус-фразы группы и подключение наборов из библиотеки."""
    item = {"Id": int(group)}
    said = []
    if items is not None:
        items = list(items)
        fits_negative(items, "adgroup")
        item["NegativeKeywords"] = {"Items": items}
        said.append(("NegativeKeywords", "минус-фразы группы"))
    if shared is not None:
        shared = [int(one) for one in shared]
        fits_shared_sets(shared, "adgroup")
        item["NegativeKeywordSharedSetIds"] = {"Items": shared}
        said.append(("NegativeKeywordSharedSetIds", "наборы минус-фраз группы"))
    if not said:
        raise DirectFailure(
            "Не сказано, что менять в группе: назовите минус-фразы или наборы. "
            "Запись без изменения прошла бы конвейер целиком и отчиталась "
            "успехом, ничего не сделав."
        )
    changes = [policies.Change(object_id=int(group), what=what, field=field,
                               after=item[field], service="группа")
               for field, what in said]
    return Operation(
        GROUPS, "update", params_key="AdGroups", items=[item], changes=changes,
        read={"FieldNames": list(GROUP_FIELDS)}, phrases=GROUP_PHRASE_PATHS,
        guard=every(replaced_from(was) if was is not None else None, arriving),
    )


# --------------------------------------------------------------------------
# Операции конвейера: библиотека наборов минус-фраз
# --------------------------------------------------------------------------

SHARED_SET_FIELDS = ("Id", "Name", "NegativeKeywords", "Associated")

# Правило справочника для названия набора. Объявляется конвейеру, а не
# считается здесь: длина у названия — обычное текстовое поле, и для таких у
# движка есть своя проверка по `limits.json`. Своей она была бы третьим
# счётчиком длины в одном модуле.
SHARED_SET_TEXTS = {"Name": "NegativeKeywordSharedSet.Name"}

# Что Директ принимает в названии набора: «буквы латинского, турецкого,
# русского, украинского, белорусского и казахского алфавитов, а так же цифры и
# знаки пунктуации» — его собственный текст в отказе 5002 (исходная проверка API,
# раздел 4). Замер там же: точку-разделитель `·` (U+00B7) знаком пунктуации он
# не считает.
#
# Проверяется это по **категориям** символа, а не по диапазонам блоков.
# Диапазон блока пропускает не буквы: `×` (U+00D7) и `÷` (U+00F7) лежат внутри
# латинского блока, `҂` (U+0482) — внутри кириллического, и буквами не
# являются ни те, ни другой. Категория различает их сразу: у буквы она
# начинается на `L`, у знака — на `S`.
#
# Письменности две — латиница и кириллица: они покрывают все шесть названных
# алфавитов, включая турецкие `ı` и `ş` и казахские `қ` и `ә`. Определяются по
# имени символа в Юникоде, потому что таблицы письменностей в стандартной
# библиотеке нет.
#
# «Знаки пунктуации» читаются осторожно, как **ASCII**-пунктуация: по крайней
# мере один типографский знак Директ отверг, а какие ещё — не замерено. Цена
# ошибки несимметрична. Лишний отказ стоит человеку одного символа в названии и
# виден сразу; пропущенный — балла за отказ Директа и невыполненной работы.
# Название при этом не данные: переписать его дёшево.
SET_NAME_SCRIPTS = ("LATIN ", "CYRILLIC ")
SET_NAME_ASCII = set(
    "0123456789 !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def fits_name_char(one: str) -> bool:
    """Годится ли символ для названия набора: буква названной письменности,
    цифра ASCII, пробел или знак пунктуации ASCII."""
    if one in SET_NAME_ASCII or ("a" <= one <= "z") or ("A" <= one <= "Z"):
        return True
    if not unicodedata.category(one).startswith("L"):
        return False
    try:
        named = unicodedata.name(one)
    except ValueError:
        return False
    return named.startswith(SET_NAME_SCRIPTS)


def fits_name(name) -> None:
    said = name_problems(name)
    if said:
        raise DirectFailure(
            f"Название набора «{excerpt(str(name), 60)}» Директ не примет: "
            + "; ".join(said)
            + ". Отказ стоит баллов, а название переписать дёшево."
        )


def name_problems(name) -> list:
    """Что не так с названием набора минус-фраз — по замеру, а не по догадке."""
    if not isinstance(name, str) or not name.strip():
        return ["пустое название"]
    odd = sorted({one for one in name if not fits_name_char(one)})
    if not odd:
        return []
    return [
        "недопустимые символы: "
        + ", ".join(f"«{one}» (U+{ord(one):04X})" for one in odd[:5])
        + (f" и ещё {len(odd) - 5}" if len(odd) > 5 else "")
        + ". Директ принимает буквы латинского, турецкого, русского, "
          "украинского, белорусского и казахского алфавитов, цифры и знаки "
          "пунктуации; типографские знаки в их число не входят — отказ 5002 "
          "вызвала точка-разделитель `·`"
    ]


def shared_set_read() -> dict:
    return {"FieldNames": list(SHARED_SET_FIELDS)}


def sets_capacity(read):
    """Условие: набор ещё влезает в кабинет — `shared_sets_per_client`.

    Пересчитать наборы можно: `NegativeKeywordSharedSets.get` перечисляет их,
    когда `SelectionCriteria` в запросе **нет вовсе** (замер 30.08.2026;
    пустой `SelectionCriteria` даёт 8000, отсюда прежнее «не перечисляет»).
    Значит предел проверяется до записи, а не отказом Директа за баллы."""
    def check(_known) -> list:
        rule = (Limits.load().data.get("keywords") or {})
        limit = rule.get("shared_sets_per_client")
        if not limit:
            return []
        found = read()
        if len(found) + 1 > int(limit):
            return [
                f"Наборов минус-фраз в кабинете {len(found)} при пределе "
                f"{limit}: новый не влезет. Освободите место — удалите "
                f"неиспользуемый набор `set delete`."
            ]
        return []
    return check


def shared_set_add_operation(name: str, items, *, guard=None) -> Operation:
    """Создание набора минус-фраз в библиотеке.

    Набор живёт в библиотеке и привязывается к кампаниям и группам; сам по
    себе он показов не меняет. Это **не** минус-фразы кампании: те лежат в
    самой кампании, а набор — общий, и правка набора меняет все объекты, к
    которым он подключён.

    Обход кабинета целиком тут дёшев и границу имеет: наборов у клиента не
    больше `keywords.shared_sets_per_client` — тридцати по справочнику, — то
    есть 15 баллов за вызов плюс балл за набор. Дороже выходит не выборка, а
    оценка: цена берётся по пределу ответа, и решение об оплате видит 10 015
    баллов там, где спишется 45."""
    items = list(items)
    fits_negative(items, "shared_set")
    fits_name(name)
    item = {"Name": name, "NegativeKeywords": items}
    changes = [
        policies.Change(object_id=name, what="название набора", field="Name",
                        after=name, service="набор минус-фраз"),
        policies.Change(object_id=name, what="минус-фразы набора",
                        field="NegativeKeywords", after=items,
                        service="набор минус-фраз"),
    ]
    return Operation(
        SHARED_SETS, "add", params_key="NegativeKeywordSharedSets", guard=guard,
        items=[item], labels=[name], changes=changes, read=shared_set_read(),
        search=("Name", WHOLE_ACCOUNT),
        texts=dict(SHARED_SET_TEXTS), phrases=SHARED_SET_PHRASE_PATHS,
    )


def shared_set_update_operation(identifier: int, *, name=None,
                                items=None, was) -> Operation:
    """Правка набора: название, состав или то и другое.

    Состав заменяется целиком, поэтому вызывающий код присылает итоговый
    список, а не добавку."""
    item = {"Id": int(identifier)}
    said = []
    if name is not None:
        fits_name(name)
        item["Name"] = name
        said.append(("Name", "название набора"))
    if items is not None:
        items = list(items)
        fits_negative(items, "shared_set")
        item["NegativeKeywords"] = items
        said.append(("NegativeKeywords", "минус-фразы набора"))
    if not said:
        raise DirectFailure(
            "Не сказано, что менять в наборе минус-фраз: назовите название "
            "или состав."
        )
    changes = [policies.Change(object_id=int(identifier), what=what,
                               field=field, after=item[field],
                               service="набор минус-фраз")
               for field, what in said]
    # Правило объявляется только на то, что в элементе есть: движок отвергает
    # правило, которому нечего проверять, и он прав — правило на отсутствующий
    # путь неотличимо от опечатки в пути.
    return Operation(
        SHARED_SETS, "update", params_key="NegativeKeywordSharedSets",
        items=[item], changes=changes, read=shared_set_read(),
        texts={path: rule for path, rule in SHARED_SET_TEXTS.items()
               if path in item},
        phrases=SHARED_SET_PHRASE_PATHS,
        guard=replaced_from(was) if was is not None else None,
    )


def unattached(known: dict, owners=None) -> list:
    """Подключённый набор не удаляется — условие по снимку конвейера.

    Проверяется по снимку **конвейера**, а не отдельным чтением команды.
    Чтение до сборки задачи оставляет окно: набор, подключённый между ним и
    снимком, снимку достаётся уже подключённым, и сторож окна расхождения не
    видит — он сличает снимок с перечитыванием, а требование не проверяет
    никто.

    Признака привязки одного мало. Замер 30.08.2026: `Associated` отвечает
    **только за группы**. Набор `75523182`, подключённый к кампании `713908311`
    и ни к одной группе, приходит с `Associated: NO`; набор `75523181`,
    подключённый и к кампании, и к группе, — с `YES`. Тот же дефект у поля
    описан у видеокреативов, так что это свойство
    поля, а не случайность одного сервиса.

    Поэтому кампании обходятся отдельно — их считает `owners`, и считает
    **при каждой проверке**, а не однажды до сборки задачи: готовый словарь
    стареет ровно так же, как признак, и набор, подключённый к кампании между
    обходом и записью, в нём не значится. Обход именно кампаний, а не групп:
    кампаний у клиента не больше трёх тысяч и стоят они балл за штуку, тогда
    как групп бывает тысяча на кампанию, и обход всех стоил бы миллионы.
    Группы же покрывает признак, и платить за них не надо.

    Цена честная: обход идёт дважды — по снимку и перед записью. Дешевле
    нельзя, не потеряв смысла второго прохода, а речь об удалении, которое
    человек запросил явно.

    Владельцев-кампании при этом **видно**, и отказ их называет: согласие
    даётся на показанное, а показать теперь есть что. Группы остаются
    неназванными — их API не отдаёт вовсе."""
    found = owners() if callable(owners) else (owners or {})
    named = {}
    for number, record in sorted(known.items()):
        said = []
        if (record or {}).get("Associated") == "YES":
            said.append("к группам")
        where = found.get(number) or found.get(int(number))
        if where:
            said.append("к кампаниям "
                        + ", ".join(str(one) for one in sorted(where)[:5]))
        if said:
            named[number] = (f"{(record or {}).get('Id', number)} "
                             f"«{(record or {}).get('Name', '')}» — привязан "
                             f"{' и '.join(said)}")
    if not named:
        return []
    return [
        "Подключённые наборы минус-фраз не удаляются: "
        + "; ".join(named[one] for one in sorted(named)[:5])
        + ". Удаление сняло бы набор со всех кампаний и групп, к которым он "
          "привязан. Кампании названы выше; групп API не отдаёт вовсе, и "
          "показать их нечем. Отвяжите набор сначала: у группы это "
          "`negative group --shared-set … --remove`, у кампании — команда "
          "scripts/campaign_write.py."
    ]


def shared_set_delete_operation(ids, *, owners) -> Operation:
    """Удаление наборов минус-фраз. Разрушающее: уходит по одному за вызов.

    Набор, подключённый к кампании или группе, удаление снимает и с них —
    признак `Associated` для того и читается до записи, а проверяет его
    `unattached` по снимку конвейера.

    `owners` обязателен и умолчания не имеет: признак отвечает только за
    группы, и без обхода кампаний запрет пропускает набор, подключённый к
    кампании. Умолчание `None` означало бы «проверить половину», и забытый
    аргумент выглядел бы как рабочий вызов."""
    ids = [int(one) for one in ids]
    if not ids:
        raise DirectFailure("Удаление набора минус-фраз: наборы не названы.")
    changes = [policies.Change(object_id=one, what="удаление набора минус-фраз",
                               service="набор минус-фраз") for one in ids]
    return Operation(
        SHARED_SETS, "delete", selection="Ids",
        items=[{"Id": one} for one in ids], changes=changes,
        read=shared_set_read(), expect_gone=True,
        guard=lambda known: unattached(known, owners),
    )
