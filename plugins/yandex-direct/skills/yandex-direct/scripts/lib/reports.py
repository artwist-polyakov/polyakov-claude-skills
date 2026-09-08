"""Сборка запросов Reports, получение и разбор отчётов."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import money
from cache import signature
from config import SKILL_DIR, DirectFailure, excerpt
from errors import TransportFailure

# Показатели, для которых нужны выбранные пользователем цели.
CONVERSION_FIELDS = {"Conversions", "ConversionRate", "CostPerConversion", "GoalsRoi",
                     "Revenue", "Profit", "PurchaseRevenue", "PurchaseProfit",
                     "PurchaseGoalsRoi", "PurchaseGoals"}


PRESETS_PATH = SKILL_DIR / "references" / "report_presets.json"

LAYER = "reports"

WAIT_DEFAULT = 300

POLL_DEFAULT = 5

POLL_MAX = 60

QUOTA_WINDOW = 10.0

PAGES_MAX = 20

TOTAL_ROWS = "Total rows: "

SETTLING_DAYS = 3

DAY = 24 * 60 * 60

QUARTER = 15 * 60

BEHIND = timedelta(hours=12)

SETTLED_TTL = DAY

TODAY_INSIDE = frozenset({
    "TODAY", "THIS_WEEK_MON_TODAY", "THIS_WEEK_SUN_TODAY", "THIS_MONTH",
    "ALL_TIME",
})

DASH = "--"

RUN_BYTES = 4
RUN_PATTERN = re.compile(r"^[0-9a-f]{8}$")

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PERIOD_SPLIT = re.compile(r"\.\.|:")

GOAL_COLUMN = re.compile(r"^(?P<field>.+)_(?P<goal>[0-9]+)_(?P<model>[A-Z]+)$")

UNSIGNED = ("ReportName", "Page")


def _part(document, key: str, kind, where: str):
    """Раздел справочника отчётов. Испорченный обязан назваться, а не молчать."""
    value = document.get(key) if isinstance(document, dict) else None
    if isinstance(value, bool) or not isinstance(value, kind):
        raise DirectFailure(
            f"{where}: раздел «{key}» отсутствует или испорчен. Проверьте файл references/report_presets.json."
        )
    return value


class Reference:
    """Машиночитаемый срез `references/REPORTS.md`."""

    __slots__ = ("document", "where")

    def __init__(self, document, where: str = ""):
        self.document = document
        self.where = where or PRESETS_PATH.name

    @classmethod
    def load(cls, path=PRESETS_PATH):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as failure:
            raise DirectFailure(
                f"Справочник отчётов {path.name} не читается: {failure}."
            ) from None
        except ValueError as failure:
            raise DirectFailure(
                f"Справочник отчётов {path.name} не разбирается как JSON: "
                f"{excerpt(str(failure), 120)}."
            ) from None
        return cls(document, where=path.name)


    @property
    def presets(self) -> dict:
        return _part(self.document, "presets", dict, self.where)

    @property
    def fields(self) -> dict:
        return _part(self.document, "fields", dict, self.where)

    @property
    def report_types(self) -> dict:
        return _part(self.document, "report_types", dict, self.where)

    @property
    def defaults(self) -> dict:
        return _part(self.document, "defaults", dict, self.where)

    @property
    def limits(self) -> dict:
        return _part(self.document, "limits", dict, self.where)

    @property
    def enums(self) -> dict:
        return _part(self.document, "enums", dict, self.where)

    @property
    def goal_scoped(self) -> list:
        return _part(self.document, "goal_scoped_fields", list, self.where)

    @property
    def attribution_models(self) -> dict:
        return _part(self.document, "attribution_models", dict, self.where)

    @property
    def incompatible(self) -> list:
        return _part(self.document, "incompatible_fields", list, self.where)

    @property
    def implied(self) -> dict:
        return _part(self.document, "implied_groupings", dict, self.where)

    @property
    def checked_at(self) -> str:
        return _part(self.document, "checked_at", str, self.where)


    def preset(self, name: str) -> dict:
        found = self.presets
        if name not in found:
            raise DirectFailure(
                f"Пресета «{excerpt(name, 48)}» нет. Есть: "
                f"{', '.join(self.preset_names)}."
            )
        return _part(found, name, dict, f"{self.where}: пресет {name}")

    @property
    def preset_names(self) -> list:
        return sorted(self.presets)

    @property
    def type_names(self) -> list:
        return sorted(self.report_types)

    def field(self, name: str):
        """Строка поля из таблицы допустимости или None, если поля нет."""
        found = self.fields
        if name not in found:
            return None
        return _part(found, name, dict, f"{self.where}: поле {name}")

    def report_type(self, name: str):
        found = self.report_types
        if name not in found:
            return None
        return _part(found, name, dict, f"{self.where}: тип отчёта {name}")

    def role(self, name: str, report_type: str):
        """Роль поля в этом типе отчёта: сегмент, атрибут, метрика, фильтр."""
        described = self.field(name)
        if described is None:
            return None
        roles = _part(described, "roles", dict, f"{self.where}: поле {name}")
        return roles.get(report_type) if isinstance(roles, dict) else None

    def flag(self, name: str, key: str) -> bool:
        described = self.field(name)
        if described is None:
            return False
        return bool(described.get(key)) if isinstance(described, dict) else False

    def operators(self, name: str):
        """Операторы фильтра поля."""
        described = self.field(name)
        if described is None:
            return []
        return described.get("filter_operators") if isinstance(described, dict) else []

    def enum(self, name: str):
        found = self.enums
        return found.get(name) if isinstance(found, dict) else None

    def limit(self, key: str, fallback: int) -> int:
        value = self.limits.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else fallback

    def default(self, key: str, fallback):
        value = self.defaults.get(key)
        return fallback if value is None else value

    @property
    def money_columns(self) -> tuple:
        """Денежные поля: задокументированные и те, про которые молчат."""
        section = _part(self.document, "money_fields", dict, self.where)
        return (tuple(_part(section, "documented", list, self.where)),
                tuple(_part(section, "undocumented", list, self.where)))

    def column_base(self, column: str) -> str:
        """Имя поля, из которого собран столбец отчёта."""
        match = GOAL_COLUMN.match(column)
        if match is None:
            return column
        base, model = match.group("field"), match.group("model")
        if base in self.goal_scoped and model in self.attribution_models:
            return base
        return column


def one_date(value: str, what: str) -> date:
    text = str(value).strip()
    if not DATE_PATTERN.match(text):
        raise DirectFailure(
            f"{what}: «{excerpt(value, 32)}» — не дата. Пишется ГГГГ-ММ-ДД."
        )
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise DirectFailure(
            f"{what}: даты {text} не существует."
        ) from None


def period(value: str, reference: Reference, what: str = "--period") -> dict:
    """Период отчёта: `DateRangeType` либо пара дат."""
    text = str(value or "").strip()
    if not text:
        raise DirectFailure(f"{what}: период не задан.")
    parts = [item for item in PERIOD_SPLIT.split(text) if item.strip()]
    if len(parts) == 2:
        since, until = one_date(parts[0], what), one_date(parts[1], what)
        if since > until:
            raise DirectFailure(
                f"{what}: начало периода {since} позже конца {until}."
            )
        return {"kind": "CUSTOM_DATE", "from": since.isoformat(),
                "to": until.isoformat()}
    if DATE_PATTERN.match(text):
        day = one_date(text, what)
        return {"kind": "CUSTOM_DATE", "from": day.isoformat(),
                "to": day.isoformat()}
    kind = text.upper()
    known = reference.enum("DateRangeType") or []
    if kind not in known:
        raise DirectFailure(
            f"{what}: «{excerpt(value, 48)}» — ни дата, ни период. Даты "
            f"пишутся ГГГГ-ММ-ДД или ГГГГ-ММ-ДД:ГГГГ-ММ-ДД; периоды: "
            f"{', '.join(known)}."
        )
    if kind == "CUSTOM_DATE":
        raise DirectFailure(
            f"{what}: CUSTOM_DATE — это пара дат, а не имя периода. Напишите "
            f"ГГГГ-ММ-ДД:ГГГГ-ММ-ДД."
        )
    return {"kind": kind, "from": None, "to": None}


def preceding(chosen: dict, what: str = "--compare") -> dict:
    """Период такой же длины, вплотную перед заданным."""
    if chosen["kind"] != "CUSTOM_DATE":
        raise DirectFailure(
            f"{what}: «предыдущий период» считается только от пары дат. У "
            f"периода {chosen['kind']} длину знает Директ, а не скилл: она "
            f"зависит от сегодняшней даты, а часовой пояс отчёта сервис не "
            f"сообщает. Назовите второй период датами."
        )
    since = date.fromisoformat(chosen["from"])
    until = date.fromisoformat(chosen["to"])
    length = (until - since).days + 1
    return {"kind": "CUSTOM_DATE",
            "from": (since - timedelta(days=length)).isoformat(),
            "to": (since - timedelta(days=1)).isoformat()}


def freshness(chosen: dict, now=None):
    """Сколько секунд запись отчёта годится в ответ. `None` — срок слоя."""
    moment = now if now is not None else datetime.now(timezone.utc)
    if chosen["kind"] in TODAY_INSIDE:
        return 0
    if chosen["kind"] != "CUSTOM_DATE":
        return seconds_to_boundary(moment)
    until = date.fromisoformat(chosen["to"])
    if until >= (moment - BEHIND).date():
        return 0
    return (None if ((moment - BEHIND).date() - until).days <= SETTLING_DAYS
            else SETTLED_TTL)


def marker_freshness(chosen: dict, now=None):
    """Сколько живёт метка начатого отчёта."""
    ttl = freshness(chosen, now)
    return seconds_to_boundary(now) if ttl == 0 else ttl


def seconds_to_boundary(now=None) -> int:
    """До ближайшей смены суток где бы то ни было, в секундах."""
    moment = now if now is not None else datetime.now(timezone.utc)
    spent = (moment.minute % 15) * 60 + moment.second
    return max(QUARTER - spent, 1)


def period_said(chosen: dict) -> str:
    if chosen["kind"] != "CUSTOM_DATE":
        return chosen["kind"]
    if chosen["from"] == chosen["to"]:
        return chosen["from"]
    return f"{chosen['from']} … {chosen['to']}"


def parse_filter(text: str, what: str = "--filter") -> dict:
    """Фильтр из строки вида «Поле ОПЕРАТОР значение,значение»."""
    parts = str(text or "").strip().split(None, 2)
    if len(parts) != 3:
        raise DirectFailure(
            f"{what}: «{excerpt(text, 64)}» — не фильтр. Пишется «Поле "
            f"ОПЕРАТОР значение» или «Поле ОПЕРАТОР значение,значение»."
        )
    name, operator, raw = parts
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise DirectFailure(f"{what}: у фильтра по полю {name} нет значений.")
    return {"Field": name, "Operator": operator.upper(), "Values": values}


def money_values(item: dict, reference: Reference) -> dict:
    """Денежный фильтр в микро: единственное место, где ввод не в валюте."""
    documented, undocumented = reference.money_columns
    name = item.get("Field") if isinstance(item, dict) else None
    if name not in documented + undocumented:
        return item
    converted = []
    for value in item.get("Values") or []:
        try:
            converted.append(str(money.to_api(value)))
        except money.MoneyError as failure:
            raise DirectFailure(
                f"Фильтр {name} {item.get('Operator')}: {failure}."
            ) from None
    return dict(item, Values=converted)


def request_params(
    reference: Reference,
    *,
    report_type: str,
    field_names,
    chosen_period: dict,
    filters=(),
    goals=(),
    attribution=(),
    include_vat=None,
    order_by=None,
    limit=None,
    offset: int = 0,
) -> dict:
    """Тело запроса без имени отчёта: имя приписывается после подписи."""
    criteria: dict = {}
    if chosen_period["kind"] == "CUSTOM_DATE":
        criteria["DateFrom"] = chosen_period["from"]
        criteria["DateTo"] = chosen_period["to"]
    prepared = [money_values(item, reference) for item in filters]
    if prepared:
        criteria["Filter"] = prepared
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": list(field_names),
        "ReportType": report_type,
        "DateRangeType": chosen_period["kind"],
        "Format": reference.default("format", "TSV"),
        "IncludeVAT": include_vat or reference.default("include_vat", "YES"),
    }
    if goals:
        params["Goals"] = [str(item) for item in goals]
        params["AttributionModels"] = list(
            attribution or reference.default("attribution_models", ["LC"]))
    elif attribution:
        params["AttributionModels"] = list(attribution)
    if order_by:
        params["OrderBy"] = list(order_by)
    params["Page"] = {
        "Limit": int(limit or reference.default("page_limit", 100000)),
        "Offset": int(offset),
    }
    return params


def key_of(params: dict) -> str:
    """Подпись запроса: ключ кэша скилла и середина имени отчёта."""
    page = params.get("Page") or {}
    signed = {name: value for name, value in params.items()
              if name not in UNSIGNED}
    signed["PageLimit"] = page.get("Limit")
    return signature(signed)


def page_name(prefix: str, params: dict, run: str) -> str:
    """Имя отчёта: `<пресет>-<хеш параметров>-<идентификатор запуска>`."""
    return f"{prefix}-{signature(params)}-{run}"


def new_run() -> str:
    return os.urandom(RUN_BYTES).hex()


def run_for(cache, key: str, *, make=None, warn=None, ttl=None) -> str:
    """Идентификатор запуска: продолжаем начатое или начинаем заново."""
    entry = cache.read(run_key(key), LAYER, ttl=ttl)
    if entry is not None and RUN_PATTERN.match(str(entry.data)):
        return str(entry.data)
    if entry is not None and warn is not None:
        warn(f"Запись начатого отчёта {run_key(key)} испорчена — отчёт "
             f"заказывается заново.")
    run = (make or new_run)()
    cache.write(run_key(key), LAYER, run)
    return run


def forget_run(cache, key: str) -> None:
    cache.forget(run_key(key))


def run_key(key: str) -> str:
    return f"{LAYER}/{key}-run"


def check(reference: Reference, params: dict) -> list:
    """Что не так с запросом. Пустой список — можно отправлять."""
    problems = []
    report_type = params.get("ReportType")
    described = reference.report_type(report_type)
    if described is None:
        problems.append(
            f"Тип отчёта «{excerpt(report_type, 48)}» неизвестен. Есть: "
            f"{', '.join(reference.type_names)}.")
        return problems

    names = list(params.get("FieldNames") or [])
    if not names:
        problems.append("FieldNames пуст: отчёт без столбцов не существует.")
    seen = set()
    for name in names:
        if name in seen:
            problems.append(f"Поле {name} названо в FieldNames дважды.")
        seen.add(name)
        if reference.field(name) is None:
            problems.append(
                f"Поля {name} нет в таблице допустимых полей (справочник "
                f"сверен {reference.checked_at}). Одно негодное имя роняет "
                f"отчёт целиком, а не один столбец.")
            continue
        if not reference.flag(name, "in_field_names"):
            problems.append(
                f"Поле {name} в FieldNames не выводится — это фильтр. "
                f"Отбирать по нему можно, показывать нечего.")
            continue
        if reference.role(name, report_type) is None:
            problems.append(
                f"Поле {name} недопустимо в {report_type}.")

    required = described.get("requires_field_names") or []
    for name in required:
        if name not in names:
            problems.append(
                f"{report_type} требует {name} в FieldNames.")

    problems += _incompatible(reference, names, report_type)
    problems += _filters(reference, params, report_type)
    problems += _goals(reference, params)
    problems += _order(reference, params, report_type)
    problems += _page(reference, params)
    return problems


def _incompatible(reference: Reference, names: list, report_type: str) -> list:
    """Взаимоисключающие наборы полей из справочника."""
    problems = []
    chosen = set(names)
    for rule in reference.incompatible:
        if not isinstance(rule, dict) or rule.get("scope") != "FieldNames":
            continue
        group = set(rule.get("group") or [])
        against = rule.get("conflicts_with")
        if against is None:
            met = sorted(group & chosen)
            if len(met) > 1:
                problems.append(
                    f"Поля {', '.join(met)} взаимоисключающие: {rule.get('rule')}.")
            continue
        left, right = sorted(group & chosen), sorted(set(against) & chosen)
        if left and right:
            problems.append(
                f"Поля {', '.join(left)} несовместимы с {', '.join(right)}: "
                f"{rule.get('rule')}.")
    return problems


def _filters(reference: Reference, params: dict, report_type: str) -> list:
    """Фильтры: поле, роль, оператор, число значений."""
    problems = []
    criteria = params.get("SelectionCriteria") or {}
    items = criteria.get("Filter") or []
    per_item = reference.limit("filter_values_per_item", 10000)
    named = set()
    for item in items:
        name = item.get("Field")
        operator = item.get("Operator")
        values = item.get("Values") or []
        if name in named:
            problems.append(
                f"Поле {name} стоит в двух фильтрах сразу: одно поле — не "
                f"более чем в одном фильтре.")
        named.add(name)
        if reference.field(name) is None:
            problems.append(
                f"Фильтр по полю {name}: такого поля нет в таблице "
                f"допустимых полей.")
            continue
        if not reference.flag(name, "in_filter"):
            problems.append(f"Поле {name} не фильтруется.")
            continue
        if reference.role(name, report_type) is None:
            problems.append(
                f"Фильтр по полю {name} недопустим в {report_type}.")
        operators = reference.operators(name)
        if operators is None:
            problems.append(
                f"Фильтр по полю {name} собрать нечем: поле помечено "
                f"фильтруемым, а операторов для него документация не "
                f"называет. Такой отбор делается в "
                f"Мастере отчётов кабинета — вслепую скилл запрос не "
                f"собирает.")
        elif not operators:
            problems.append(f"Поле {name} фильтром не бывает.")
        elif operator not in operators:
            problems.append(
                f"Оператор {operator} к полю {name} неприменим. Допустимо: "
                f"{', '.join(operators)}.")
        if len(values) > per_item:
            problems.append(
                f"Фильтр по полю {name}: {len(values)} значений при пределе "
                f"{per_item}.")
    problems += _filter_groups(reference, [item.get("Field") for item in items])
    return problems


def _filter_groups(reference: Reference, names: list) -> list:
    problems = []
    chosen = set(names)
    for rule in reference.incompatible:
        if not isinstance(rule, dict) or rule.get("scope") != "Filter":
            continue
        met = sorted(set(rule.get("group") or []) & chosen)
        if len(met) > 1:
            problems.append(
                f"Фильтры по {', '.join(met)} взаимоисключающие: "
                f"{rule.get('rule')}.")
    return problems


def needs_goals(reference: Reference, params: dict) -> bool:
    """Есть ли показатели конверсий в столбцах, фильтрах или сортировке."""
    fields = set(params.get("FieldNames") or [])
    fields.update(item.get("Field") for item in
                  (params.get("SelectionCriteria") or {}).get("Filter") or [])
    fields.update(item.get("Field") for item in params.get("OrderBy") or [])
    return bool(fields & (set(reference.goal_scoped) | {"Profit"}))


def _goals(reference: Reference, params: dict) -> list:
    problems = []
    goals = params.get("Goals") or []
    if needs_goals(reference, params) and not goals:
        problems.append("Для отчёта с конверсиями пользователь должен выбрать цели: "
                        "--list-goals, затем --goals ID[,ID].")
    if len(set(map(str, goals))) != len(goals):
        problems.append("В --goals повторяются идентификаторы целей.")
    filters = (params.get("SelectionCriteria") or {}).get("Filter") or []
    purchase = {"PurchaseRevenue", "PurchaseProfit", "PurchaseGoalsRoi"}
    if purchase.intersection(params.get("FieldNames") or []):
        chosen = next((item for item in filters if item.get("Field") == "PurchaseGoals"), None)
        if not chosen or not chosen.get("Values"):
            problems.append("Для дохода от покупок выберите цели пользователя фильтром "
                            "PurchaseGoals IN ID[,ID]; --goals этот фильтр не заменяет.")
    per_request = reference.limit("goals_per_request", 10)
    if len(goals) > per_request:
        problems.append(
            f"Целей {len(goals)} при пределе {per_request} на запрос.")
    for value in goals:
        if not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
            problems.append(
                f"Цель «{excerpt(value, 32)}» — не идентификатор. Reports "
                f"принимает идентификаторы целей Метрики числами.")
    known = reference.attribution_models
    for model in params.get("AttributionModels") or []:
        if model not in known:
            problems.append(
                f"Модель атрибуции {model} неизвестна. Есть: "
                f"{', '.join(sorted(known))}. Устаревшие LSC, FC, LYDC и "
                f"LYDCCD Директ молча подменяет — в скилле их нет.")
    return problems


def _order(reference: Reference, params: dict, report_type: str) -> list:
    problems = []
    for item in params.get("OrderBy") or []:
        name = item.get("Field")
        if reference.field(name) is None:
            problems.append(f"Сортировка по полю {name}: такого поля нет.")
            continue
        if not reference.flag(name, "in_order_by"):
            problems.append(f"По полю {name} сортировать нельзя.")
        elif reference.role(name, report_type) is None:
            problems.append(
                f"Сортировка по полю {name} недопустима в {report_type}.")
    return problems


def _page(reference: Reference, params: dict) -> list:
    problems = []
    page = params.get("Page") or {}
    limit = page.get("Limit")
    criteria = params.get("SelectionCriteria") or {}
    by_login = any(item.get("Field") == "ClientLogin"
                   for item in criteria.get("Filter") or [])
    ceiling = reference.limit(
        "row_limit_with_client_login_filter" if by_login else "default_row_limit",
        500000 if by_login else 1000000)
    if isinstance(limit, int) and limit > ceiling:
        problems.append(
            f"Page.Limit {limit} больше предела {ceiling}"
            + (" (с фильтром по ClientLogin предел ниже)." if by_login else "."))
    if isinstance(limit, int) and limit < 1:
        problems.append(f"Page.Limit {limit}: страница без строк.")
    return problems


def notes(reference: Reference, params: dict, now=None) -> list:
    """Что стоит сказать человеку, не отказывая. Судит он, а не код."""
    said = []
    report_type = params.get("ReportType")
    names = list(params.get("FieldNames") or [])
    implied = reference.implied
    for name in names:
        for hidden in implied.get(name) or []:
            if hidden not in names:
                said.append(
                    f"{name} тянет за собой группировку по {hidden}, а столбца "
                    f"нет: строки будут выглядеть дублями. Добавьте {hidden}.")
    segments = [name for name in names
                if reference.role(name, report_type) == "segment"]
    if segments:
        said.append(
            f"Сегменты {', '.join(segments)} добавляют группировку: строк "
            f"больше, и числа в каждой другие, а не «то же самое подробнее».")
    _, undocumented = reference.money_columns
    money_asked = [name for name in names if name in undocumented]
    if money_asked:
        said.append(
            f"Поля {', '.join(money_asked)} похожи на деньги, но множитель для "
            f"них документация не называет — показаны сырыми, как пришли.")
    for item in (params.get("SelectionCriteria") or {}).get("Filter") or []:
        name = item.get("Field")
        values = reference.enum(name)
        if not values:
            continue
        unknown = [value for value in item.get("Values") or [] if value not in values]
        if unknown:
            said.append(
                f"Значения {', '.join(unknown)} поля {name} в справочнике не "
                f"числятся — перечень собран {reference.checked_at} и полным "
                f"не объявлен. Фильтр уйдёт как есть.")
    if params.get("Goals"):
        said.append("Конверсии показаны отдельно по выбранным целям. "
                    "Один визит может достичь нескольких целей: сумма не равна числу уникальных заявок.")
    if "Profit" in names:
        said.append("Profit — общий показатель API. Прибыль по выбранной цели считайте "
                    "как Revenue_<ID>_<модель> минус Cost.")
    if "Query" in names:
        said.append("Средние позиции рассчитаны по первой странице поиска. "
                    "AvgTrafficVolume — объём трафика, а не процент выкупленных показов.")
    said.extend(depth_said(reference, params, now))
    return said


def depth_said(reference: Reference, params: dict, now=None) -> list:
    """Что из спрошенного периода Директ не отдаст по глубине хранения."""
    since = str((params.get("SelectionCriteria") or {}).get("DateFrom") or "")
    if not since:
        if ("Query" in (params.get("FieldNames") or [])
                and params.get("DateRangeType") in ("LAST_365_DAYS", "ALL_TIME")):
            return ["Поисковые запросы хранятся только за последние 180 дней: "
                    "выбранный период будет покрыт частично."]
        return []
    moment = now if now is not None else datetime.now(timezone.utc)
    today = (moment - BEHIND).date()
    asked = date.fromisoformat(since)
    said = []
    depth = reference.limit("query_field_depth_days", 180)
    if "Query" in (params.get("FieldNames") or []):
        edge = today - timedelta(days=int(depth))
        if asked < edge:
            said.append(
                f"Поисковые запросы (`Query`) хранятся {int(depth)} дней: за "
                f"даты до {edge.isoformat()} строк не будет, и это не "
                f"отсутствие показов.")
    edge = date(today.year - 3, today.month, 1)
    if asked < edge:
        said.append(
            f"Статистика хранится три года от текущего месяца: за даты до "
            f"{edge.isoformat()} строк не будет, и это не отсутствие показов.")
    return said


class Pace:
    """Темп запросов к `Reports`: не больше `limit` за окно."""

    __slots__ = ("limit", "window", "_sleep", "_clock", "_moments")

    def __init__(self, limit: int, window: float = QUOTA_WINDOW,
                 sleep=time.sleep, clock=time.monotonic):
        self.limit = int(limit)
        self.window = float(window)
        self._sleep = sleep
        self._clock = clock
        self._moments: list = []

    def wait(self, cap=None) -> None:
        """Дождаться места в окне и записать свой запрос."""
        if self.limit < 1:
            self._moments.append(self._clock())
            return
        now = self._clock()
        self._moments = [at for at in self._moments if now - at < self.window]
        if len(self._moments) >= self.limit:
            pause = self.window - (now - self._moments[0])
            if cap is not None:
                pause = min(pause, max(float(cap), 0))
            if pause > 0:
                self._sleep(pause)
            now = self._clock()
            self._moments = [at for at in self._moments if now - at < self.window]
        self._moments.append(now)

    @property
    def spent(self) -> int:
        """Сколько запросов уже сделано. Нужно проверке, а не работе."""
        return len(self._moments)


def pace_for(reference: Reference, **named) -> Pace:
    """Счётчик темпа по справочнику: одна штука на команду."""
    return Pace(reference.limit("requests_per_10s", 20), **named)


def retry_in(answer, fallback: int = POLL_DEFAULT) -> int:
    """Пауза до следующего опроса — из заголовка `retryIn`."""
    try:
        seconds = int(str(answer.retry_in).strip())
    except ValueError:
        return fallback
    return max(1, min(seconds, POLL_MAX))


def one_page(client, params: dict, *, account, report_type: str,
             processing_mode: str, use_operator_units, wait: int = WAIT_DEFAULT,
             sleep=time.sleep, clock=time.monotonic, warn=None, pace=None) -> str:
    """Один отчёт целиком: постановка, ожидание готовности, тело ответа."""
    mode = processing_mode
    started = clock()
    fallen_back = False
    broken = 0

    def overrun(status) -> None:
        """Отказ по пределу ожидания — один на все паузы цикла."""
        raise DirectFailure(
            f"Отчёт «{params.get('ReportName')}» не сформировался за "
            f"{wait} с (последний код {status}). Он остался в очереди "
            f"Директа: повторите ту же команду — опрос продолжится с тем "
            f"же именем. Дольше ждать — --wait."
        )

    def linger(seconds: float, status) -> None:
        """Пауза внутри бюджета — единственная в цикле, кроме одной."""
        left = wait - (clock() - started)
        sleep(min(seconds, max(left, 0)))
        if clock() - started >= wait:
            overrun(status)

    budgeted = None
    while True:
        if pace is not None:
            pace.wait(cap=(None if budgeted is None
                           else max(wait - (clock() - started), 0)))
            if budgeted is not None and clock() - started >= wait:
                overrun(budgeted)
        budgeted = None
        try:
            answer = client.report_once(
                params, report_type=report_type, account=account,
                processing_mode=mode, use_operator_units=use_operator_units,
                retry=False,
            )
        except TransportFailure as failure:
            if failure.status == 502 and not fallen_back:
                pass
            elif failure.status == 500 and broken >= 1:
                raise TransportFailure(
                    f"Директ дважды не смог сформировать отчёт "
                    f"«{params.get('ReportName')}» (HTTP 500). Справочник на "
                    f"этот случай отправляет в поддержку, а не в третий "
                    f"повтор: {excerpt(str(failure), 200)}",
                    retryable=False, status=500,
                ) from None
            elif failure.status == 500 and broken < 1:
                broken += 1
                if warn is not None:
                    warn(f"Директ не смог сформировать отчёт "
                         f"({excerpt(str(failure), 120)}) — один повтор с нуля.")
                sleep(POLL_DEFAULT)
                continue
            elif failure.retryable and clock() - started < wait:
                if warn is not None:
                    warn(f"Отчёт не дошёл ({excerpt(str(failure), 120)}) — "
                         f"повтор через {POLL_DEFAULT} с.")
                linger(POLL_DEFAULT, failure.status)
                budgeted = failure.status
                continue
            else:
                raise
            fallen_back = True
            mode = "offline"
            if warn is not None:
                warn("Директ не уложился в ограничение на время обработки — "
                     "тот же отчёт заказан в режиме offline.")
            continue
        if answer.ready:
            return answer.text
        waited = clock() - started
        if waited >= wait:
            raise DirectFailure(
                f"Отчёт «{params.get('ReportName')}» за {int(waited)} с не "
                f"сформировался (последний код {answer.status}). Он остался в "
                f"очереди Директа: повторите ту же команду — опрос продолжится "
                f"с тем же именем, а не закажет второй отчёт. Дольше ждать — "
                f"--wait."
            )
        pause = retry_in(answer)
        if warn is not None and answer.status == 201:
            warn(f"Отчёт поставлен в очередь, опрос через {int(pause)} с.")
        linger(pause, answer.status)
        budgeted = answer.status


class Report:
    """Отчёт целиком: шапка, столбцы, строки и то, как он собирался."""

    __slots__ = ("caption", "columns", "rows", "stated", "pages", "params",
                 "truncated")

    def __init__(self, caption, columns, rows, stated, pages, params, truncated):
        self.caption = caption
        self.columns = columns
        self.rows = rows
        self.stated = stated
        self.pages = pages
        self.params = params
        self.truncated = truncated

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return (f"<Report {len(self.rows)} строк, {len(self.columns)} столбцов, "
                f"страниц {self.pages}>")

    def as_dict(self) -> dict:
        """Отчёт для кэша и для `--json`."""
        return {
            "caption": self.caption,
            "columns": list(self.columns),
            "rows": list(self.rows),
            "stated": self.stated,
            "pages": self.pages,
            "truncated": self.truncated,
            "params": self.params,
        }

    @classmethod
    def restored(cls, stored, params: dict):
        """Отчёт из записи кэша. Негодная запись — промах, а не поломка."""
        if not isinstance(stored, dict):
            raise DirectFailure("Запись отчёта в кэше — не объект.")
        columns = stored.get("columns")
        rows = stored.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise DirectFailure("В записи отчёта нет столбцов или строк.")
        wanted = {str(name) for name in columns}
        for number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise DirectFailure(
                    f"Строка {number} в записи отчёта — не объект.")
            if set(row) != wanted:
                raise DirectFailure(
                    f"Строка {number} в записи отчёта не совпадает со "
                    f"столбцами: лишние {sorted(set(row) - wanted)}, "
                    f"недостающие {sorted(wanted - set(row))}.")
            for name, value in row.items():
                if not isinstance(value, str):
                    raise DirectFailure(
                        f"Ячейка {name} строки {number} в записи отчёта — "
                        f"не текст ({type(value).__name__}).")
        stated = stored.get("stated")
        if isinstance(stated, bool) or not isinstance(stated, int):
            raise DirectFailure(
                f"В записи отчёта нет числа строк ({stated!r}). Такую запись "
                f"мог сделать разбор, принимавший ответ без строки "
                f"«{TOTAL_ROWS.strip()}»."
            )
        if stated != len(rows):
            raise DirectFailure(
                f"В записи отчёта объявлено {stated} строк, а лежит "
                f"{len(rows)}. Такую запись мог сделать разбор, принимавший "
                f"расхождение как примечание."
            )
        return cls(
            caption=str(stored.get("caption") or ""),
            columns=[str(name) for name in columns],
            rows=rows,
            stated=stated,
            pages=stored.get("pages") if isinstance(stored.get("pages"), int) else 1,
            params=params,
            truncated=bool(stored.get("truncated")),
        )


def caption_period(caption: str) -> str:
    """Фактический период из шапки отчёта — то, что в скобках в конце."""
    tail = str(caption).strip().strip('"').rstrip()
    if not tail.endswith(")") or "(" not in tail:
        return ""
    return tail[tail.rfind("(") + 1:-1].strip()


def collect(client, reference: Reference, params: dict, *, account, prefix: str,
            run: str, processing_mode: str, use_operator_units,
            wait: int = WAIT_DEFAULT, sleep=time.sleep, clock=time.monotonic,
            warn=None, pace=None) -> Report:
    """Отчёт со всеми страницами: запрос, опрос, склейка."""
    report_type = params.get("ReportType")
    limit = int((params.get("Page") or {}).get("Limit") or 0)
    columns: list = []
    rows: list = []
    caption = ""
    stated = 0
    pages = 0
    offset = int((params.get("Page") or {}).get("Offset") or 0)
    while True:
        page = dict(params, Page={"Limit": limit, "Offset": offset})
        page["ReportName"] = page_name(prefix, page, run)
        text = one_page(
            client, page, account=account, report_type=report_type,
            processing_mode=processing_mode, use_operator_units=use_operator_units,
            wait=wait, sleep=sleep, clock=clock, warn=warn, pace=pace,
        )
        table = parse(text, f"reports.{report_type}")
        pages += 1
        if not columns:
            columns, caption = table.columns, table.caption
        elif table.columns != columns:
            raise DirectFailure(
                f"Страница {pages} отчёта пришла с другими столбцами: было "
                f"{len(columns)}, стало {len(table.columns)}. Склеивать их "
                f"нельзя — это разные отчёты."
            )
        elif caption_period(table.caption) != caption_period(caption):
            raise DirectFailure(
                f"Страница {pages} отчёта пришла за другой период: было "
                f"«{caption_period(caption)}», стало "
                f"«{caption_period(table.caption)}». Склеивать их нельзя — "
                f"это статистика за разное время. Задайте период явными "
                f"датами вместо AUTO или относительного."
            )
        rows.extend(table.rows)
        stated += table.stated if table.stated is not None else len(table.rows)
        if len(table.rows) != limit or not limit:
            return Report(caption, columns, rows, stated, pages, params, False)
        if pages >= PAGES_MAX:
            if warn is not None:
                warn(f"Склейка остановлена на {pages} странице — это "
                     f"предохранитель скилла, а не предел Директа. Сузьте "
                     f"период или отбор.")
            return Report(caption, columns, rows, stated, pages, params, True)
        offset += limit


class Table:
    """Одна страница отчёта, разобранная из TSV."""

    __slots__ = ("caption", "columns", "rows", "stated")

    def __init__(self, caption, columns, rows, stated):
        self.caption = caption
        self.columns = columns
        self.rows = rows
        self.stated = stated


def parse(text: str, where: str) -> Table:
    """TSV отчёта: шапка, имена столбцов, строки, объявленное число строк."""
    lines = str(text).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[-1].startswith(TOTAL_ROWS):
        raise DirectFailure(
            f"{where}: в ответе нет строки «{TOTAL_ROWS.strip()}». Скилл её "
            f"не отключает: это единственный признак того, что выборка "
            f"полна, и без неё принять ответ значит выдать часть строк за "
            f"весь отчёт. Похоже на оборванное тело ответа."
        )
    tail = lines.pop()[len(TOTAL_ROWS):].strip()
    if not tail.isascii() or not tail.isdigit():
        raise DirectFailure(
            f"{where}: строка «{excerpt(tail, 32)}» вместо числа строк отчёта."
        )
    stated = int(tail)
    if len(lines) < 2:
        raise DirectFailure(
            f"{where}: в отчёте нет шапки или имён столбцов. Пришло "
            f"{len(lines)} строк: {excerpt(text, 200) or 'пустое тело'}."
        )
    caption = str(lines[0])
    columns = [str(name) for name in lines[1].split("\t")]
    rows = []
    for number, line in enumerate(lines[2:], 3):
        cells = [str(value) for value in line.split("\t")]
        if len(cells) != len(columns):
            raise DirectFailure(
                f"{where}: в строке {number} отчёта {len(cells)} ячеек при "
                f"{len(columns)} столбцах. Разбор по именам столбцов на такой "
                f"строке молча сдвинул бы значения."
            )
        rows.append(dict(zip(columns, cells)))
    if stated != len(rows):
        raise DirectFailure(
            f"{where}: отчёт объявил {stated} строк статистики, а прислал "
            f"{len(rows)}. Последняя строка ответа называет число строк "
            f"этого ответа, и расхождение означает оборванное тело: принять "
            f"его значит выдать часть строк за весь отчёт."
        )
    return Table(caption, columns, rows, stated)


def cell(row: dict, column: str, where: str = "отчёт") -> str:
    """Ячейка строки отчёта по имени столбца."""
    if column not in row:
        raise DirectFailure(
            f"{where}: столбца {column} в отчёте нет. Имена столбцов "
            f"собираются из запроса, а не из перечня полей."
        )
    return row[column]


def number(text):
    """Число из ячейки или None. Прочерк и пустое — не ноль."""
    value = str(text).strip()
    if not value or value == DASH:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def is_money(column: str, reference: Reference) -> bool:
    """Денежный ли это столбец — по задокументированному перечню."""
    documented, _ = reference.money_columns
    return reference.column_base(column) in documented


def metrics_of(report: Report, reference: Reference) -> list:
    """Столбцы-метрики отчёта: то, что складывается и сравнивается."""
    report_type = report.params.get("ReportType")
    return [column for column in report.columns
            if reference.role(reference.column_base(column), report_type)
            == "metric"]


def aggregate_params(reference: Reference, params: dict) -> dict:
    """Запрос итога: тот же отбор без единой группировки."""
    if params.get("ReportType") == "SEARCH_QUERY_PERFORMANCE_REPORT":
        raise DirectFailure("Общий итог по запросам не запрошен: CUSTOM_REPORT "
                            "имеет другой состав данных и может включать сети. "
                            "Анализируйте строки выгрузки по запросам.")
    names = []
    for name in params.get("FieldNames") or []:
        base = reference.column_base(name)
        if base in names:
            continue
        if reference.role(base, "CUSTOM_REPORT") != "metric":
            continue
        names.append(base)
    if not names:
        raise DirectFailure(
            "Итог этого отчёта отдельным запросом не берётся: ни одна его "
            "метрика не допустима в CUSTOM_REPORT — так устроены поля "
            "медийного отчёта."
        )
    criteria = params.get("SelectionCriteria") or {}
    for item in criteria.get("Filter") or []:
        if reference.role(item.get("Field"), "CUSTOM_REPORT") is None:
            raise DirectFailure(
                f"Итог этого отчёта отдельным запросом не берётся: фильтр по "
                f"полю {item.get('Field')} в CUSTOM_REPORT недопустим."
            )
    aggregated = dict(params, ReportType="CUSTOM_REPORT", FieldNames=names)
    aggregated.pop("OrderBy", None)
    page = params.get("Page") or {}
    aggregated["Page"] = dict(page, Limit=max(2, int(page.get("Limit") or 2)),
                              Offset=0)
    return aggregated


def totals_of(report: Report, reference: Reference) -> dict:
    """Значения метрик единственной строки — она же итог."""
    if len(report.rows) != 1:
        raise DirectFailure(
            f"Итогом считается единственная строка, а их {len(report.rows)}."
        )
    row = report.rows[0]
    return {column: {"value": number(cell(row, column)),
                     "raw": cell(row, column),
                     "money": is_money(column, reference)}
            for column in metrics_of(report, reference)}


def keys_of(report: Report, reference: Reference) -> list:
    """Столбцы, которые опознают строку: сегменты и атрибуты, но не метрики."""
    report_type = report.params.get("ReportType")
    return [column for column in report.columns
            if reference.role(reference.column_base(column), report_type)
            in ("segment", "attribute")]


def identity_of(report: Report, reference: Reference) -> list:
    """Столбцы, которыми строка опознаётся **между периодами**."""
    keys = keys_of(report, reference)
    bases = {reference.column_base(name) for name in keys}
    named = set()
    for name in keys:
        base = reference.column_base(name)
        for kin in ((base[:-len("Name")] + "Id") if base.endswith("Name")
                    else None, base + "Id"):
            if kin and reference.field(kin) is not None and kin in bases:
                named.add(name)
                break
    return [name for name in keys if name not in named] or keys


def compare(before: Report, after: Report, reference: Reference) -> dict:
    """Сведение двух периодов по опознающим столбцам."""
    if before.columns != after.columns:
        raise DirectFailure(
            "Периоды сравниваются по одинаковым столбцам, а пришли разные: "
            f"{len(before.columns)} против {len(after.columns)}."
        )
    cut = [name for name, item in (("первый", before), ("второй", after))
           if item.truncated]
    if cut:
        raise DirectFailure(
            f"Периоды не сводятся: {', '.join(cut)} обрезан предохранителем "
            f"склейки. Недоехавшие строки читались бы как появившиеся или "
            f"исчезнувшие, а разности по ним неверны. Сузьте период или "
            f"отбор — либо поднимите --limit."
        )
    keys = identity_of(after, reference)
    shown_keys = keys_of(after, reference)
    metrics = metrics_of(after, reference)
    apart = [name for name in keys
             if before.rows and after.rows
             and not ({cell(row, name) for row in before.rows}
                      & {cell(row, name) for row in after.rows})]
    hidden = [name for shown in after.columns
              for name in reference.implied.get(shown) or []
              if name not in after.columns]

    def indexed(report, where: str):
        found = {}
        for row in report.rows:
            found[tuple(cell(row, name) for name in keys)] = row
        if len(found) != len(report.rows):
            raise DirectFailure(
                f"Периоды не сводятся: в отчёте ({where}) "
                f"{len(report.rows)} строк, а различающих их наборов "
                f"значений — {len(found)}. Группировка отчёта шире его "
                f"столбцов, и какая строка какой пара — отсюда не видно."
                + (f" Похоже, не хватает {', '.join(hidden)}: допишите в "
                   f"--fields." if hidden else
                   " Тип отчёта добавляет группировку независимо от"
                   " FieldNames — допишите её поле в --fields.")
            )
        return found

    was, now = indexed(before, "первый период"), indexed(after, "второй период")
    paired = [key for key in now if key in was]
    rows = []
    for key in list(now) + [item for item in was if item not in now]:
        left, right = was.get(key), now.get(key)
        changes = {}
        for column in metrics:
            first = None if left is None else number(cell(left, column))
            second = None if right is None else number(cell(right, column))
            changes[column] = {
                "before": first, "after": second,
                "delta": None if first is None or second is None else second - first,
            }
        seen = right if right is not None else left
        rows.append({"key": key, "was": left is not None,
                     "now": right is not None, "metrics": changes,
                     "shown": tuple(cell(seen, name) for name in shown_keys)})
    return {"keys": keys, "shown_keys": shown_keys, "metrics": metrics,
            "rows": rows, "unpairable": apart, "pairs": len(paired),
            "unpaired": bool(before.rows and after.rows and not paired)}
