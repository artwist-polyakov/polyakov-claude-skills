#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Статистика Директа: отчёты с выбранными пользователем целями."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import money  # noqa: E402
import reports as reports_lib  # noqa: E402
import incoming  # noqa: E402
from accounts import Accounts, Ambiguous, resolve_account  # noqa: E402
from cache import (  # noqa: E402
    Cache,
    add_arguments,
    human_age,
    outline,
    plural,
    tsv,
)
from config import DirectFailure, excerpt, preload_secrets, redact, short  # noqa: E402
from direct import Client  # noqa: E402

import campaigns as campaign_command  # noqa: E402

SHOWN = 8

JSON_LIMIT = 200

PREFERRED = ("Impressions", "Clicks", "Ctr", "Cost", "AvgCpc", "Conversions",
             "CostPerConversion", "Revenue", "Profit", "GoalsRoi")

METRICS_SHOWN = 4

KEY_WIDTH = 52

GOALS_SCAN = 50


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def order_from(value: str, parser) -> list:
    """`--order-by Cost:DESC` → `[{"Field": "Cost", "SortOrder": "DESCENDING"}]`."""
    found = []
    for item in str(value).split(","):
        name, _, direction = item.strip().partition(":")
        if not name:
            continue
        way = (direction or "desc").strip().lower()
        if way not in ("asc", "desc"):
            parser.error(f"--order-by: «{item.strip()}» — порядок пишется "
                         f"через двоеточие: Cost:desc или Date:asc")
        found.append({"Field": name.strip(),
                      "SortOrder": "ASCENDING" if way == "asc" else "DESCENDING"})
    if not found:
        parser.error("--order-by: перечень пуст")
    return found


def goals_of_cabinet(client, login: str, campaign_ids) -> list:
    """Доступные цели статистики по всем переданным кампаниям."""
    found = {}
    for start in range(0, len(campaign_ids), GOALS_SCAN):
        for record in client.stat_goals(campaign_ids=campaign_ids[start:start + GOALS_SCAN]):
            if not isinstance(record, dict):
                continue
            number = record.get("GoalID")
            if number is None:
                continue
            found.setdefault(str(number), {
                "id": str(number),
                "name": str(record.get("GoalName") or record.get("Name") or "Без названия"),
                "source": "GetStatGoals",
            })
    return list(found.values())


def build(reference, args, parser, *, campaign_id=None, chosen_period=None) -> dict:
    """Параметры запроса из аргументов команды и пресета."""
    filters = [reports_lib.parse_filter(item) for item in args.filter or []]
    if campaign_id is not None:
        filters.insert(0, {"Field": "CampaignId", "Operator": "IN",
                           "Values": [str(campaign_id)]})
    if args.preset:
        preset = reference.preset(args.preset)
        report_type = preset.get("report_type")
        field_names = list(preset.get("field_names") or [])
        order_by = preset.get("order_by")
    else:
        report_type = args.type
        field_names = [name.strip() for name in args.fields.split(",")
                       if name.strip()]
        order_by = None
    if args.preset:
        defaults = reference.preset(args.preset).get("filters") or []
        for item in defaults:
            explicit = next((f for f in filters if f["Field"] == item["Field"]), None)
            if explicit is not None and explicit != item:
                parser.error(f"пресет {args.preset}: фильтр {item['Field']} должен быть {item['Values']}")
            if explicit is None:
                filters.append(item)
    if args.traffic_only:
        field_names = [name for name in field_names if name not in reports_lib.CONVERSION_FIELDS]
        if any(item["Field"] in reports_lib.CONVERSION_FIELDS for item in filters):
            parser.error("--traffic-only несовместим с фильтрами по конверсиям или доходу")
    if args.order_by:
        order_by = order_from(args.order_by, parser)
    if args.traffic_only and any(item["Field"] in reports_lib.CONVERSION_FIELDS for item in order_by or []):
        parser.error("--traffic-only несовместим с сортировкой по конверсиям или доходу")
    return reports_lib.request_params(
        reference,
        report_type=report_type,
        field_names=field_names,
        chosen_period=chosen_period,
        filters=filters,
        goals=args.goals or (),
        attribution=args.attribution or (),
        include_vat=args.vat,
        order_by=order_by,
        limit=args.limit,
    )


def processing_mode(reference, args, report_type: str = "", warn=None) -> str:
    """Режим формирования: `--offline`, `--online` или умолчание пресета."""
    described = reference.report_type(report_type) or {}
    if described.get("offline_only"):
        if warn is not None and (args.online or args.offline):
            warn(f"{report_type} формируется только офлайн при любом значении "
                 f"заголовка — режим взят offline, а не из аргумента.")
        return "offline"
    if args.offline:
        return "offline"
    if args.online:
        return "online"
    if args.preset:
        return str(reference.preset(args.preset).get("processing_mode") or "auto")
    return str(reference.default("processing_mode", "auto"))


def looks_numeric(query) -> bool:
    """Отбор задан идентификатором, а не куском названия."""
    wanted = str(query or "").strip()
    return bool(wanted) and wanted.isascii() and wanted.isdigit()


def chosen_campaign(known: list, query: str) -> int:
    """Идентификатор кампании отбора."""
    wanted = str(query).strip()
    try:
        record = campaign_command.one_campaign(known, wanted)
    except DirectFailure:
        if not looks_numeric(wanted):
            raise
        warn(f"Кампании {wanted} в срезе кабинета нет: `Campaigns.get` не "
             f"возвращает кампании Мастера кампаний вовсе. Отбор уходит "
             f"номером, а часовой пояс такой кампании скиллу неизвестен.")
        return int(wanted)
    return campaign_command.row_of(record)["id"]


class Cabinets:
    """Кабинеты, чьи строки попали в отчёт, и их деньги."""

    __slots__ = ("accounts", "login", "logins", "snapshot")

    def __init__(self, accounts, login: str, logins, snapshot=None):
        self.accounts = accounts
        self.login = login
        self.logins = list(logins)
        self.snapshot = dict(snapshot or {})

    @property
    def alone(self) -> str:
        """Единственный кабинет отчёта — или выбранный, если строк нет."""
        return self.logins[0] if len(self.logins) == 1 else self.login

    def as_snapshot(self, listed_at: str = "") -> dict:
        """Метаданные кабинетов отчёта — то, что ложится в запись."""
        return {name: {"currency": self.currency_of(name),
                       "vat_rate": self.rate_of(name),
                       "listed_at": listed_at}
                for name in self.logins}

    def listed_of(self, name: str) -> str:
        """Когда снят список, из которого взяты метаданные этого кабинета."""
        return str((self.snapshot.get(name) or {}).get("listed_at") or "")

    def listed(self) -> str:
        """Когда снят список, которым подписан отчёт, или пусто при разнобое."""
        found = {self.listed_of(name) for name in self.logins}
        return found.pop() if len(found) == 1 and "" not in found else ""

    def listed_apart(self) -> str:
        """Почему общей даты нет — или пусто, когда вопрос не стоит."""
        if not self.several or self.listed():
            return ""
        unknown = [name for name in self.logins if not self.listed_of(name)]
        if unknown:
            return (f"Дата списка не сохранена для {', '.join(unknown)}: "
                    f"валюта и ставка этих кабинетов подписаны без даты.")
        return ("Списки кабинетов сняты в разные дни: ставки и валюты "
                "взяты каждая из своего.")

    @classmethod
    def of(cls, accounts, login: str, params: dict, *reports, snapshot=None):
        """Кабинеты показанного: по столбцу `ClientLogin`, иначе по фильтру."""
        found = set()
        for report in reports:
            if report is None:
                continue
            if "ClientLogin" not in report.columns:
                continue
            found |= {reports_lib.cell(row, "ClientLogin")
                      for row in report.rows}
        listed = sorted(item for item in found if item)
        if listed:
            return cls(accounts, login, listed, snapshot)
        for item in (params.get("SelectionCriteria") or {}).get("Filter") or []:
            if item.get("Field") == "ClientLogin":
                return cls(accounts, login, item.get("Values") or [login],
                           snapshot)
        return cls(accounts, login, [login], snapshot)

    def _cabinet(self, name: str):
        return self.accounts.by_login(name)

    def currency_of(self, name: str) -> str:
        """Валюта кабинета: из снимка записи, иначе из живого списка."""
        kept = self.snapshot.get(name)
        if isinstance(kept, dict) and "currency" in kept:
            return kept["currency"]
        cabinet = self._cabinet(name)
        return cabinet.currency if cabinet is not None else ""

    @property
    def several(self) -> bool:
        return len(self.logins) > 1

    @property
    def read_elsewhere(self) -> bool:
        """Срез кампаний читан не у тех кабинетов, чьи строки в отчёте."""
        return self.logins != [self.login]

    @property
    def currencies(self) -> list:
        return sorted({self.currency_of(name) for name in self.logins})

    @property
    def rates(self) -> list:
        found = {self.rate_of(name) for name in self.logins}
        return sorted(found, key=lambda item: (item is None, item))

    @property
    def one_currency(self) -> bool:
        """Все ли кабинеты отчёта в одной валюте — и известна ли она."""
        found = self.currencies
        return len(found) == 1 and bool(found[0])

    def for_row(self, row, report) -> str:
        """Валюта строки — или пусто, если приписать её нечему."""
        if "ClientLogin" in report.columns:
            return self.currency_of(reports_lib.cell(row, "ClientLogin"))
        if not self.several:
            return self.currency_of(self.alone)
        return ""

    def rate_of(self, name: str):
        """Ставка НДС кабинета. `None` — «Директ её не назвал»."""
        kept = self.snapshot.get(name)
        if isinstance(kept, dict) and "vat_rate" in kept:
            return kept["vat_rate"]
        cabinet = self._cabinet(name)
        return None if cabinet is None else cabinet.vat_rate

    def common_rate(self):
        """Ставка, общая для всех кабинетов отчёта, или `None`."""
        found = self.rates
        return found[0] if len(found) == 1 else None

    def for_key(self, key, keys: list) -> str:
        """Валюта строки сведения: кабинет берётся из её же ключа."""
        if "ClientLogin" in keys:
            return self.currency_of(key[keys.index("ClientLogin")])
        return self.one()

    def one(self) -> str:
        """Валюта, общая для всего отчёта, или пусто, если общей нет."""
        if not self.several:
            return self.currency_of(self.alone)
        return self.currencies[0] if self.one_currency else ""

    def said(self) -> str:
        """Что написать в шапке про валюту."""
        if not self.several:
            return self.currency_of(self.alone) or "неизвестна"
        found = [item for item in self.currencies if item]
        if self.one_currency:
            return f"{found[0]} у всех {len(self.logins)} кабинетов"
        return ("по кабинетам: " + (", ".join(found) or "неизвестны")
                + " — суммы разных кабинетов не складываются")


def timezones_of(records: list, campaign_id=None) -> dict:
    """`{часовой пояс: сколько кампаний}` по срезу кабинета."""
    tally: dict = {}
    for record in records:
        row = campaign_command.row_of(record)
        if campaign_id is not None and row["id"] != campaign_id:
            continue
        zone = row["timezone"] or "не указан"
        tally[zone] = tally.get(zone, 0) + 1
    return tally


def timezone_line(tally: dict, narrowed: bool = False,
                  partial: str = "", reported=()) -> str:
    if partial:
        others = [name for name in reported if name != partial]
        if len(others) == 1 and len(reported) == 1:
            return (f"Часовой пояс прочитан у кабинета {partial}, а строки "
                    f"отчёта — кабинета {others[0]}: сутки в его отчёте могут "
                    f"быть нарезаны иначе.")
        return (f"Часовой пояс проверен только у кабинета {partial}: "
                f"у остальных кабинетов отчёта он не прочитан, и сутки в "
                f"разрезе по датам могут быть нарезаны иначе.")
    if not tally and narrowed:
        return ("Часовой пояс этой кампании неизвестен: в срез `Campaigns.get` "
                "она не попала. Сутки отчёта нарезаны неизвестно как.")
    if not tally:
        return ("Часовой пояс кампаний не прочитан: срез кабинета пуст. Сутки "
                "отчёта нарезаны неизвестно как.")
    if len(tally) == 1:
        zone, count = next(iter(tally.items()))
        return (f"Часовой пояс: {zone} у всех "
                f"{plural(count, 'кампании', 'кампаний', 'кампаний')} среза.")
    listed = ", ".join(f"{zone} — {count}"
                       for zone, count in sorted(tally.items(),
                                                 key=lambda pair: -pair[1]))
    return (f"Часовых поясов несколько ({listed}): сутки в разрезе по датам "
            f"нарезаны по-разному, складывать их нельзя.")


def value_text(row, column: str, currency: str, reference) -> str:
    """Ячейка для человека: деньги в валюте, прочерк прочерком."""
    raw = reports_lib.cell(row, column)
    if reports_lib.number(raw) is None:
        return raw or "—"
    if not reports_lib.is_money(column, reference):
        return raw
    try:
        return money.format_api(raw, currency)
    except money.MoneyError:
        return raw


def shown_metrics(report, reference) -> list:
    """В кратком выводе оставляем расход и конверсии по каждой выбранной цели."""
    metrics = reports_lib.metrics_of(report, reference)
    conversions = [name for name in metrics
                   if reference.column_base(name) in ("Conversions", "CostPerConversion")]
    if conversions:
        return [name for name in ("Clicks", "Cost") if name in metrics] + conversions
    preferred = [name for name in metrics if reference.column_base(name) in PREFERRED]
    return (preferred or metrics)[:METRICS_SHOWN]


def row_line(row, keys: list, metrics: list, currency: str, reference) -> str:
    said = " · ".join(reports_lib.cell(row, name) for name in keys) or "весь отбор"
    numbers = " · ".join(
        f"{name} {value_text(row, name, currency, reference)}"
        for name in metrics)
    return f"  {excerpt(said, KEY_WIDTH):<{KEY_WIDTH}} {numbers}"


def totals_line(report, whole, reference, cabinets, why: str) -> str:
    """Итог по отбору — из отчёта без группировок, а не сложением строк."""
    if cabinets.several and not cabinets.one_currency:
        return ("Итог одной суммой не считается: строки отчёта принадлежат "
                "кабинетам с разной валютой.")
    if whole is None:
        return f"Итог не запрошен: {why}"
    if not whole.rows:
        return "Итого: строк за период нет."
    summed = reports_lib.totals_of(whole, reference)
    parts = []
    for name in shown_metrics(report, reference):
        found = summed.get(name)
        if found is None:
            continue
        parts.append(f"{name} "
                     f"{value_text(whole.rows[0], name, cabinets.one(), reference)}")
    tail = "" if whole is report else " (отдельным запросом без группировок)"
    return "Итого: " + (" · ".join(parts) + tail if parts else "показывать нечего")


def vat_said(params: dict, rate, since: str = "", several: bool = False) -> str:
    """Учёт НДС и ставка кабинета — рядом с цифрами, а не в памяти человека."""
    counted = "учтён" if params.get("IncludeVAT") == "YES" else "не учтён"
    if several:
        return f"НДС {counted} · ставка у каждого кабинета своя"
    if rate is None:
        return f"НДС {counted} · ставка кабинета не названа"
    age = f", список от {since[:10]}" if since else ""
    return f"НДС {counted} · ставка {rate:g} %{age}"


def head_lines(login: str, args, reference, params: dict, chosen_period: dict,
               entry, report, currency: str, mode: str, vat_rate=None,
               listed_at: str = "", several: bool = False) -> list:
    what = (f"пресет «{reference.preset(args.preset).get('title')}»"
            if args.preset else "отчёт по заказу")
    age = f"из кэша, {human_age(entry.age)}" if entry.hit else "прочитано заново"
    lines = [f"Кабинет {login} · {what} · {params['ReportType']} · {age}"]
    goals = params.get("Goals") or []
    models = params.get("AttributionModels") or []
    actual = ("" if chosen_period["kind"] == "CUSTOM_DATE" else
              reports_lib.caption_period(report.caption))
    lines.append(
        f"Период {reports_lib.period_said(chosen_period)}"
        + (f" ({actual})" if actual else "") + " · "
        f"{vat_said(params, vat_rate, listed_at, several)} · "
        f"атрибуция {', '.join(models) or 'LC'} · "
        f"валюта {currency} · режим {mode}"
        + (f" · цели {', '.join(goals)}" if goals else ""))
    pages = f", страниц {report.pages}" if report.pages > 1 else ""
    lines.append(
        f"Строк {len(report.rows)}{pages}"
        + (" · склейка остановлена предохранителем" if report.truncated else ""))
    return lines


def report_lines(login: str, args, reference, params, chosen_period, entry,
                 report, whole, why: str, cabinets, mode: str, zones: dict,
                 notes: list, blind: list, vat_rate=None, listed_at: str = "",
                 lists_apart: str = "", narrowed: bool = False, sweep=None,
                 periods=None, totalled=None, export=None) -> list:
    lines = head_lines(login, args, reference, params, chosen_period, entry,
                       report, cabinets.said(), mode, vat_rate,
                       "" if lists_apart else listed_at, cabinets.several)
    if lists_apart:
        lines.append(lists_apart)
    lines.append(timezone_line(
        zones, narrowed=narrowed,
        partial=login if cabinets.read_elsewhere else "",
        reported=cabinets.logins))
    if blind:
        lines.append(f"Для кампаний Мастера кампаний пусты: {', '.join(blind)}.")
    lines.extend(notes)
    lines.append("")
    if sweep is None:
        lines.extend(rows_shown(report, reference, cabinets))
    else:
        lines.extend(compare_shown(sweep, reference, cabinets, periods))
    lines.append(totals_line(report, whole, reference, totalled or cabinets, why))
    lines.extend(campaign_command.export_line(export))
    return lines


def rows_shown(report, reference, cabinets) -> list:
    keys = reports_lib.keys_of(report, reference)
    metrics = shown_metrics(report, reference)
    lines = [row_line(row, keys, metrics, cabinets.for_row(row, report),
                      reference)
             for row in report.rows[:SHOWN]]
    if not report.rows:
        lines.append("  Строк нет. Пустой отчёт — законный ответ: показов за "
                     "период не было либо отбор их не застал.")
    if len(report.rows) > SHOWN:
        lines.append(
            f"  … ещё {plural(len(report.rows) - SHOWN, 'строка', 'строки', 'строк')}"
            f" — в файле ниже или через rg по TSV рядом с ним")
    return lines


def compare_shown(sweep: dict, reference, cabinets, periods) -> list:
    """Сведение двух периодов: строки с наибольшим изменением первой метрики."""
    metrics = [name for name in sweep["metrics"]
               if reference.column_base(name) in PREFERRED][:2] or sweep["metrics"][:2]
    lines = [f"Сравнение периодов: {periods[0]} → {periods[1]}"]

    def weight(item):
        found = item["metrics"].get(metrics[0]) if metrics else None
        delta = None if found is None else found["delta"]
        return abs(delta) if delta is not None else 0

    for item in sorted(sweep["rows"], key=weight, reverse=True)[:SHOWN]:
        said = " · ".join(item.get("shown") or item["key"]) or "весь отбор"
        shown = cabinets.for_key(item["key"], sweep["keys"])
        parts = [f"{name} "
                 f"{delta_text(item['metrics'][name], name, reference, shown)}"
                 for name in metrics]
        mark = "" if item["was"] and item["now"] else (
            " · только во втором" if item["now"] else " · только в первом")
        lines.append(f"  {excerpt(said, KEY_WIDTH):<{KEY_WIDTH}} "
                     f"{' · '.join(parts)}{mark}")
    if not sweep["rows"]:
        lines.append("  Строк нет ни в одном периоде.")
    if len(sweep["rows"]) > SHOWN:
        rest = len(sweep["rows"]) - SHOWN
        beyond = ("" if len(sweep["rows"]) <= JSON_LIMIT else
                  f"; он отдаёт первые {JSON_LIMIT} и общее число — за "
                  f"остальным сузьте отбор или период")
        lines.append(
            f"  … ещё {plural(rest, 'строка', 'строки', 'строк')}"
            f" сведения — в машиночитаемом выводе (--json){beyond}")
    return lines


def delta_text(found: dict, column: str, reference, currency: str) -> str:
    def one(value):
        if value is None:
            return "—"
        if reports_lib.is_money(column, reference):
            return money.format_api(int(value), currency)
        return f"{int(value)}" if value % 1 == 0 else f"{value.normalize():f}"

    delta = found["delta"]
    sign = "" if delta is None or delta < 0 else "+"
    return (f"{one(found['before'])} → {one(found['after'])}"
            + (f" ({sign}{one(delta)})" if delta is not None else ""))


def as_json(login: str, params, chosen_period, entry, report, whole, why: str,
            reference, currency: str, mode: str, zones: dict, notes: list,
            sweep, vat_rate=None, listed_at: str = "", cabinets=None,
            earlier_period=None, export=None) -> dict:
    summed = ({} if whole is None or not whole.rows
              else reports_lib.totals_of(whole, reference))
    return {
        "account": login,
        "currency": currency,
        "cabinets": [] if cabinets is None else [
            {"login": name, "currency": cabinets.currency_of(name),
             "vat_rate": cabinets.rate_of(name),
             "listed_at": cabinets.listed_of(name)}
            for name in cabinets.logins],
        "vat_rate": (vat_rate if cabinets is None or not cabinets.several
                     else cabinets.common_rate()),
        "cabinets_listed_at": (listed_at if cabinets is None
                               else cabinets.listed()),
        "processing_mode": mode,
        "period": chosen_period,
        "params": params,
        "from_cache": entry.hit,
        "cache": None if entry.path is None else short(entry.path),
        "caption": report.caption,
        "columns": report.columns,
        "rows_total": len(report.rows),
        "pages": report.pages,
        "truncated": report.truncated,
        "timezones": zones,
        "notes": notes,
        "totals": {
            name: {
                "value": None if found["value"] is None else str(found["value"]),
                "raw": found["raw"],
                "money": found["money"],
            }
            for name, found in summed.items()
        },
        "totals_source": ("единственная строка отчёта" if whole is report
                          else None if whole is None else "отчёт без группировок"),
        "totals_note": why,
        "rows": report.rows[:JSON_LIMIT],
        "compare": None if sweep is None else {
            "keys": sweep["keys"],
            "before": earlier_period,
            "after": chosen_period,
            "rows_total": len(sweep["rows"]),
            "shown_keys": sweep.get("shown_keys"),
            "unpairable": sweep.get("unpairable") or [],
            "pairs": sweep.get("pairs"),
            "unpaired": bool(sweep.get("unpaired")),
            "rows": [{"key": list(item["key"]),
                      "shown": list(item.get("shown") or ()),
                      "was": item["was"], "now": item["now"],
                      "metrics": {name: {key: None if value is None else str(value)
                                         for key, value in change.items()}
                                  for name, change in item["metrics"].items()}}
                     for item in sweep["rows"][:JSON_LIMIT]],
        },
        "csv": None if export is None else str(export),
    }


def catalogue(reference) -> list:
    """`--list`: что вообще можно спросить. Сеть при этом не задействуется."""
    lines = [f"Пресеты (справочник сверен {reference.checked_at}):"]
    for name in reference.preset_names:
        preset = reference.preset(name)
        lines.append(f"  {name:<15} {preset.get('title')} · "
                     f"{preset.get('report_type')} · строка — {preset.get('row')}")
    lines.append("Отчёт по заказу: --type ТИП --fields Поле,Поле")
    lines.extend(wrapped("  типы: ", reference.type_names, 3))
    lines.extend(wrapped("  периоды: ", reference.enum("DateRangeType") or [], 7))
    lines.append("  выбор целей: --list-goals; отчёт: --goals ID[,ID]")
    lines.append("  только показы, клики и расход: --traffic-only")
    lines.append("  примеры и описание показателей — references/REPORTS.md")
    return lines


def wrapped(head: str, items, per_line: int) -> list:
    """Длинный перечень в несколько строк."""
    chunks = [items[at:at + per_line] for at in range(0, len(items), per_line)]
    return [f"{head if number == 0 else ' ' * len(head)}{', '.join(chunk)}"
            for number, chunk in enumerate(chunks)]


class Parser(argparse.ArgumentParser):
    """Ошибка аргументов — код 2, и без секретов в тексте."""

    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def make_parser(reference) -> Parser:
    """Разбор аргументов. Сам разбор зовётся в `main`, после `preload_secrets`."""
    parser = Parser(description="Статистика Директа: пресеты и отчёт по заказу.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--preset", choices=reference.preset_names,
                        help="готовый отчёт")
    parser.add_argument("--type", metavar="ТИП", choices=reference.type_names,
                        help="тип отчёта по заказу")
    parser.add_argument("--fields", metavar="СПИСОК",
                        help="столбцы отчёта по заказу через запятую")
    parser.add_argument("--period", metavar="ПЕРИОД",
                        default=reference.default("date_range_type", "LAST_30_DAYS"),
                        help="ГГГГ-ММ-ДД:ГГГГ-ММ-ДД или имя периода")
    parser.add_argument("--compare", metavar="ПЕРИОД",
                        help="второй период: даты, имя периода или previous")
    parser.add_argument("--campaign", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        type=incoming.campaign_selector,
                        help="отбор по одной кампании")
    parser.add_argument("--filter", metavar="«ПОЛЕ ОПЕРАТОР ЗНАЧЕНИЯ»",
                        action="append",
                        help="фильтр; можно несколько раз")
    parser.add_argument("--list-goals", action="store_true",
                        help="показать доступные цели для выбора пользователем")
    parser.add_argument("--traffic-only", action="store_true",
                        help="только показы, клики и расход; исключить конверсии и доход")
    parser.add_argument("--goals", metavar="СПИСОК",
                        help="идентификаторы целей Метрики через запятую")
    parser.add_argument("--attribution", metavar="СПИСОК",
                        help="модели атрибуции через запятую")
    parser.add_argument("--vat", choices=("YES", "NO"), type=str.upper,
                        help="учитывать НДС; по умолчанию из справочника")
    parser.add_argument("--order-by", metavar="ПОЛЕ:ПОРЯДОК",
                        help="сортировка, например Cost:desc")
    parser.add_argument("--limit", metavar="N", type=int,
                        help="строк на страницу")
    parser.add_argument("--wait", metavar="СЕК", type=int,
                        default=reports_lib.WAIT_DEFAULT,
                        help="сколько ждать готовности офлайн-отчёта")
    parser.add_argument("--offline", action="store_true",
                        help="поставить в очередь, не формировать сейчас")
    parser.add_argument("--online", action="store_true",
                        help="сформировать сейчас или отказать")
    parser.add_argument("--list", action="store_true",
                        help="перечень пресетов, типов и периодов")
    parser.add_argument("--csv", metavar="ФАЙЛ", type=Path,
                        help="выгрузить отчёт в файл")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    add_arguments(parser)
    return parser


def settled(args, parser):
    """Что нельзя проверить порознь: сочетания аргументов и их разбор."""
    if args.list:
        return args
    if args.list_goals:
        if args.preset or args.type or args.goals or args.traffic_only:
            parser.error("--list-goals используется отдельно от формирования отчёта")
        return args
    if args.traffic_only and args.goals:
        parser.error("--traffic-only и --goals несовместимы")
    if bool(args.preset) == bool(args.type):
        parser.error("назовите либо пресет (--preset), либо тип отчёта "
                     "(--type) с полями (--fields): перечень — --list")
    if args.type and not args.fields:
        parser.error("--type без --fields: отчёт без столбцов не существует")
    if args.fields and not args.type:
        parser.error("--fields без --type: тип отчёта задаёт, какие поля "
                     "допустимы и какие группировки добавятся сами")
    if args.online and args.offline:
        parser.error("--online и --offline взаимоисключающие")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit: страница без строк")
    if args.wait < 0:
        parser.error("--wait: отрицательное ожидание")
    args.goals = ([item.strip() for item in args.goals.split(",") if item.strip()]
                  if args.goals else None)
    args.attribution = ([item.strip().upper() for item in args.attribution.split(",")
                         if item.strip()] if args.attribution else None)
    return args


def fetch(cache, client, reference, params, *, account, prefix, mode,
          use_operator_units, wait, chosen_period, pace=None, vat_rate=None,
          listed_at: str = "", wants_total=None, snapshot_of=None) -> tuple:
    """Отчёт из кэша, а при промахе — из Директа. Ключ — подпись запроса."""
    key = reports_lib.key_of(params)
    name = f"{reports_lib.LAYER}/{key}"
    marker = f"{prefix}-{key}"

    def produce():
        run = reports_lib.run_for(
            cache, marker, warn=warn,
            ttl=reports_lib.marker_freshness(chosen_period))
        report = reports_lib.collect(
            client, reference, params, account=account, prefix=prefix, run=run,
            processing_mode=mode, use_operator_units=use_operator_units,
            wait=wait, warn=warn, pace=pace,
        )
        allowed, refused = (True, "") if wants_total is None else wants_total(report)
        whole, why = whole_report(
            cache, client, reference, params, report, account=account,
            prefix=prefix, mode=mode, use_operator_units=use_operator_units,
            wait=wait, chosen_period=chosen_period,
            pace=pace) if allowed else (None, refused)
        reports_lib.forget_run(cache, marker)
        body = dict(report.as_dict(), vat_rate=vat_rate, listed_at=listed_at)
        body["cabinets"] = ({} if snapshot_of is None
                            else snapshot_of(report))
        body["totals"] = None if whole is None else whole.as_dict()
        body["totals_note"] = why
        return body

    def through():
        return cache.through(
            name, reports_lib.LAYER, produce,
            index=lambda stored: tsv(stored.get("rows") or [],
                                     stored.get("columns") or []),
            signature=key, ttl=reports_lib.freshness(chosen_period),
        )

    entry = through()
    try:
        report = reports_lib.Report.restored(entry.data, params)
        stored_envelope(entry.data)
    except DirectFailure as failure:
        warn(f"Запись отчёта не разбирается ({failure}) — читаю заново.")
        cache.forget(name)
        entry = through()
        report = reports_lib.Report.restored(entry.data, params)
        stored_envelope(entry.data)
    stored = entry.data if isinstance(entry.data, dict) else {}
    return (entry, report,
            {"vat_rate": stored.get("vat_rate"),
             "vat_known": "vat_rate" in stored,
             "cabinets": kept_cabinets(stored, account),
             "listed_at": str(stored.get("listed_at") or ""),
             "totals": stored.get("totals"),
             "totals_note": str(stored.get("totals_note") or "")})


def kept_cabinets(stored, login: str) -> dict:
    """Снимок кабинетов из записи, включая записи прежней формы."""
    kept = stored.get("cabinets")
    if isinstance(kept, dict) and kept:
        return kept
    if "vat_rate" in stored:
        return {login: {"vat_rate": stored.get("vat_rate"),
                        "listed_at": str(stored.get("listed_at") or "")}}
    return {}


def stored_envelope(stored) -> None:
    """Проверить всё, что лежит в записи рядом со строками."""
    if not isinstance(stored, dict):
        raise DirectFailure("Запись отчёта — не объект.")
    rate = stored.get("vat_rate")
    if rate is not None and (isinstance(rate, bool)
                             or not isinstance(rate, (int, float))):
        raise DirectFailure(f"Ставка НДС в записи — не число: {rate!r}.")
    if not isinstance(stored.get("listed_at", ""), str):
        raise DirectFailure("Дата списка кабинетов в записи — не строка.")
    if not isinstance(stored.get("totals_note", ""), str):
        raise DirectFailure("Причина отсутствия итога в записи — не строка.")
    snapshot = stored.get("cabinets", {})
    if not isinstance(snapshot, dict):
        raise DirectFailure("Снимок кабинетов в записи — не объект.")
    for name, item in snapshot.items():
        if not isinstance(item, dict):
            raise DirectFailure(f"Снимок кабинета {name} — не объект.")
        if not isinstance(item.get("currency", ""), str):
            raise DirectFailure(f"Валюта кабинета {name} в снимке — не строка.")
        if not isinstance(item.get("listed_at", ""), str):
            raise DirectFailure(
                f"Дата списка кабинета {name} в снимке — не строка.")
        rate = item.get("vat_rate")
        if rate is not None and (isinstance(rate, bool)
                                 or not isinstance(rate, (int, float))):
            raise DirectFailure(
                f"Ставка НДС кабинета {name} в снимке — не число: {rate!r}.")
    totals = stored.get("totals")
    if totals is not None:
        reports_lib.Report.restored(totals, stored.get("params") or {})


def read_campaigns(cache, client, accounts, login: str, *, required: bool) -> list:
    """Срез кампаний кабинета — ради часового пояса и поиска по названию."""
    try:
        return campaign_command.read_slice(cache, client, accounts, login).data
    except DirectFailure as failure:
        if required:
            raise
        warn(f"Кампании кабинета не прочитались ({failure}). Отчёт это не "
             f"отменяет, но часовой пояс кампаний в сводке будет неизвестен.")
        return []


def scanned_campaigns(records: list, campaign_id) -> list:
    """Кампании, у которых спрашиваются цели."""
    if campaign_id is not None:
        return [campaign_id]
    living = []
    for record in records:
        row = campaign_command.row_of(record)
        if row["state"] != "ARCHIVED":
            living.append(row["id"])
    return living


def show_goals(client, login: str, records: list, campaign_id, *, as_json=False,
               required=False) -> None:
    """Показать цели для выбора пользователем; ничего не выбирать автоматически."""
    listed = goals_of_cabinet(client, login, scanned_campaigns(records, campaign_id))
    message = ("Выберите одну или несколько ценных целей: например, оплаченный заказ, "
               "заявку или звонок. Передайте их ID через --goals ID[,ID].")
    if as_json:
        say(json.dumps({"account": login, "campaign_id": campaign_id,
                        "status": "goal_selection_required" if required else "available_goals",
                        "available_goals": listed, "message": message}, ensure_ascii=False))
        return
    say(f"Кабинет {login} · доступные цели статистики:")
    for item in listed:
        say(f"  {item['id']} · {item['name']}")
    if not listed:
        say("  Цели не найдены. Проверьте привязку счётчика к кампании и доступ к нему; "
            "если знаете ID нужной цели, передайте его через --goals.")
    say(message)


def whole_report(cache, client, reference, params, report, *, account, prefix,
                 mode, use_operator_units, wait, chosen_period, pace=None) -> tuple:
    """Отчёт для итога и причина, если его нет."""
    if len(report.rows) <= 1:
        return report, ""
    try:
        aggregated = reports_lib.aggregate_params(reference, params)
    except DirectFailure as failure:
        return None, str(failure)
    problems = reports_lib.check(reference, aggregated)
    if problems:
        return None, f"запрос итога не проходит проверку: {problems[0]}"
    marker = f"{prefix}-total-{reports_lib.key_of(aggregated)}"
    whole = reports_lib.collect(
        client, reference, aggregated, account=account, prefix=f"{prefix}-total",
        run=reports_lib.run_for(
            cache, marker, warn=warn,
            ttl=reports_lib.marker_freshness(chosen_period)),
        processing_mode=mode,
        use_operator_units=use_operator_units, wait=wait, warn=warn, pace=pace)
    reports_lib.forget_run(cache, marker)
    return whole, ""


def taxed_apart(now: dict, was: dict) -> list:
    """Кабинеты, у которых ставка НДС в периодах разная."""
    said = []
    for name, kept in sorted((was or {}).items()):
        fresh = (now or {}).get(name)
        if not isinstance(fresh, dict) or not isinstance(kept, dict):
            continue
        if "vat_rate" not in fresh or "vat_rate" not in kept:
            continue
        if fresh["vat_rate"] != kept["vat_rate"]:
            said.append(f"{name}: {kept['vat_rate']} → {fresh['vat_rate']}")
    return said


def restored_total(reference, params, report, taxed: dict) -> tuple:
    """Итог из записи отчёта: строка одна — она же итог, иначе из записи."""
    if len(report.rows) <= 1:
        return report, ""
    stored = taxed.get("totals")
    if not stored:
        return None, taxed.get("totals_note") or "итог в записи не сохранён"
    try:
        aggregated = reports_lib.aggregate_params(reference, params)
    except DirectFailure as failure:
        return None, str(failure)
    try:
        return reports_lib.Report.restored(stored, aggregated), ""
    except DirectFailure as failure:
        return None, f"итог в записи не разбирается: {failure}"


def run(args, parser, reference) -> int:
    if args.list:
        outline(catalogue(reference))
        return 0

    chosen = reports_lib.period(args.period, reference, "--period")
    second = None
    if args.compare:
        second = (reports_lib.preceding(chosen)
                  if args.compare.strip().lower() == "previous"
                  else reports_lib.period(args.compare, reference, "--compare"))

    client = Client.from_env(profile=args.env, warn=warn)
    accounts = Accounts.load(client, refresh=args.no_cache, warn=warn)
    login = resolve_account(accounts, client, args.account)
    cache = Cache.from_args(args, account=login, warn=warn)
    cabinet = accounts.by_login(login)
    vat_rate = cabinet.vat_rate if cabinet is not None else None

    known = read_campaigns(
        cache, client, accounts, login,
        required=bool(args.campaign) and not looks_numeric(args.campaign))
    campaign_id = None
    if args.campaign:
        campaign_id = chosen_campaign(known, args.campaign)

    if args.list_goals:
        show_goals(client, login, known, campaign_id, as_json=args.json)
        return 0

    params = build(reference, args, parser, campaign_id=campaign_id,
                   chosen_period=chosen)
    if reports_lib.needs_goals(reference, params) and not args.goals:
        show_goals(client, login, known, campaign_id, as_json=args.json, required=True)
        return 2
    problems = reports_lib.check(reference, params)
    if problems:
        for problem in problems:
            warn(f"  ✗ {problem}")
        raise DirectFailure(
            f"Запрос не отправлен: {plural(len(problems), 'замечание', 'замечания', 'замечаний')} "
            f"выше. Негодное имя роняет отчёт целиком, а не один столбец, "
            f"поэтому набор проверяется до отправки.")
    notes = reports_lib.notes(reference, params)
    mode = processing_mode(reference, args, params.get("ReportType"), warn)
    prefix = args.preset or "custom"

    def decide():
        """Чьими баллами платить за отчёт. Решать здесь нечего."""
        return None

    pace = reports_lib.pace_for(reference)

    def wants_total(asked):
        """Считать ли итог отчёту по этим параметрам: решает его же выборка."""
        def decided(collected):
            found = Cabinets.of(accounts, login, asked, collected)
            if found.one_currency or not found.several:
                return True, ""
            return False, ("строки отчёта принадлежат кабинетам с разной "
                           "валютой, и одной суммы у них нет")

        return decided

    def snapshot_of(collected, asked=None):
        """Метаданные кабинетов, чьи строки в этом отчёте."""
        return Cabinets.of(accounts, login, asked or params,
                           collected).as_snapshot(accounts.checked_at)

    entry, report, taxed = fetch(
        cache, client, reference, params, account=login, prefix=prefix,
        mode=mode, use_operator_units=decide, wait=args.wait, pace=pace,
        chosen_period=chosen, vat_rate=vat_rate, listed_at=accounts.checked_at,
        wants_total=wants_total(params), snapshot_of=snapshot_of)
    kept = dict(taxed["cabinets"] or {})

    def shown_cabinets(*collected, asked=None):
        return Cabinets.of(accounts, login, asked or params, *collected,
                           snapshot=kept)

    cabinets = shown_cabinets(report)
    shown_listed = taxed["listed_at"] or accounts.checked_at
    whole, why = restored_total(reference, params, report, taxed)
    sweep = None
    before = None
    if second is not None:
        earlier = build(reference, args, parser, campaign_id=campaign_id,
                        chosen_period=second)
        _, before, earlier_taxed = fetch(
            cache, client, reference, earlier, account=login, prefix=prefix,
            mode=mode, use_operator_units=decide, wait=args.wait, pace=pace,
            chosen_period=second, vat_rate=vat_rate,
            listed_at=accounts.checked_at, wants_total=wants_total(earlier),
            snapshot_of=lambda collected: snapshot_of(collected, earlier))
        sweep = reports_lib.compare(before, report, reference)
        if sweep["unpaired"]:
            notes.append(
                f"Пар в сведении нет: строки различает "
                f"{', '.join(sweep['unpairable'])}, а значения этого столбца "
                f"в периодах не пересекаются."
                if sweep["unpairable"] else
                "Пар в сведении нет: ни один набор значений ключа не "
                "встретился в обоих периодах, хотя каждый столбец по "
                "отдельности пересекается.")
            notes.append(
                "Каждая строка показана как «только в первом» или «только во "
                "втором»; сравнивайте по более крупному разрезу — например, "
                "пресетом campaigns.")
        apart = taxed_apart(taxed["cabinets"], earlier_taxed["cabinets"])
        if apart:
            notes.append(
                f"Ставка НДС в периодах разная ({'; '.join(apart)}): разность "
                f"считана по цифрам, к которым налог применялся по-разному.")

    if before is not None:
        kept = {**(earlier_taxed["cabinets"] or {}), **(taxed["cabinets"] or {})}
        cabinets = shown_cabinets(report, before)
    if args.csv:
        campaign_command.dump_csv(report.rows, report.columns, args.csv)
    zones = timezones_of(known, campaign_id)
    blind = list(reference.preset(args.preset).get("blind_for_campaign_master") or []) \
        if args.preset else []

    if args.json:
        body = as_json(login, params, chosen, entry, report, whole, why,
                       reference, cabinets.one(), mode, zones, notes, sweep,
                       vat_rate=cabinets.common_rate(),
                       listed_at=cabinets.listed() or shown_listed,
                       cabinets=cabinets, earlier_period=second,
                       export=args.csv)
        say(json.dumps(body, ensure_ascii=False, default=str))
        return 0
    lines = report_lines(
        login, args, reference, params, chosen, entry, report, whole, why,
        cabinets, mode, zones, notes, blind,
        vat_rate=cabinets.common_rate(),
        listed_at=cabinets.listed() or shown_listed,
        lists_apart=cabinets.listed_apart(),
        narrowed=campaign_id is not None,
        sweep=sweep, totalled=shown_cabinets(report),
        periods=None if second is None else (reports_lib.period_said(second),
                                             reports_lib.period_said(chosen)),
        export=args.csv)
    outline(lines, path=entry.path, total=len(report.rows))
    return 0


def main(argv=None) -> int:
    preload_secrets()
    try:
        reference = reports_lib.Reference.load()
    except DirectFailure as failure:
        warn(str(failure))
        return 1
    parser = make_parser(reference)
    args = settled(parser.parse_args(argv), parser)
    try:
        return run(args, parser, reference)
    except Ambiguous as failure:
        warn(str(failure))
        for match in failure.matches[:SHOWN]:
            warn(f"  {match.cabinet.login} · {match.reason}")
        return 1
    except DirectFailure as failure:
        warn(str(failure))
        return 1
    except OSError as failure:
        warn(redact(f"Не удалось записать файл: {failure}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
