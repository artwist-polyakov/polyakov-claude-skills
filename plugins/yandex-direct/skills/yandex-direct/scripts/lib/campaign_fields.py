"""Разбор частичной правки полей кампании без собственного механизма записи."""

import json
import math
import re

from config import DirectFailure
from incoming import text_of


BODIES = ("UnifiedCampaign", "TextCampaign")

# Эти настройки уже имеют предметные проверки. Универсальная запись их не заменяет.
PROTECTED = {
    "Name": "name", "StartDate": "dates", "EndDate": "dates",
    "DailyBudget": "справочник BUDGETS.md",
    "BiddingStrategy": "strategy", "PriorityGoals": "strategy",
    "AttributionModel": "strategy", "PackageBiddingStrategy": "справочник BUDGETS.md",
    "WeeklyBudgetRollover": "справочник BUDGETS.md",
    "CounterIds": "counters", "TrackingParams": "tracking",
    "TimeTargeting": "schedule", "TimeZone": "schedule",
    "NegativeKeywords": "предметную команду для минус-фраз",
    "NegativeKeywordSharedSetIds": "sets", "BlockedIps": "ips",
    "ExcludedSites": "sites",
}
READ_ONLY = {"Id", "Type", "State", "Status", "StatusPayment", "StatusClarification",
             "Statistics", "Currency", "Funds", "RepresentedBy",
             "CanBeUsedAsPackageBiddingStrategySource"}
SETTING_COMMANDS = {"ENABLE_SITE_MONITORING": "monitoring",
                    "ADD_METRICA_TAG": "tracking", "ADD_OPENSTAT_TAG": "tracking"}
READ_ONLY_SETTINGS = {"SHARED_ACCOUNT_ENABLED", "DAILY_BUDGET_ALLOWED"}
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DirectFailure(f"Поле «{key}» указано дважды.")
        result[key] = value
    return result


def _constant(value):
    raise DirectFailure(f"Значение {value} не входит в JSON.")


def _json(text):
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)


def patch_from(*, assignments=(), body=None):
    """JSON использует форму API; --set сокращает Settings.Option до одного пути."""
    if body:
        try:
            patch = _json(text_of(body, "Файл правки"))
        except ValueError as exc:
            raise DirectFailure(f"Файл правки не содержит корректный JSON: {exc}.") from None
    else:
        patch = {}
        for assignment in assignments:
            path, separator, text = assignment.partition("=")
            parts = path.split(".")
            if not separator or not text or not all(NAME.fullmatch(part) for part in parts):
                raise DirectFailure("--set ожидает Путь.К.Полю=ЗНАЧЕНИЕ.")
            try:
                value = _json(text)
            except ValueError:
                if text[0] in '\"{[':
                    raise DirectFailure(f"Значение поля «{path}» не содержит корректный JSON.") from None
                value = text
            target = patch
            for part in parts[:-1]:
                target = target.setdefault(part, {})
                if not isinstance(target, dict):
                    raise DirectFailure(f"Путь «{path}» пересекается с другой правкой.")
            if parts[-1] in target:
                raise DirectFailure(f"Путь «{path}» указан дважды или пересекается с другой правкой.")
            target[parts[-1]] = value
        for prefix in BODIES:
            typed = patch.get(prefix)
            if isinstance(typed, dict) and isinstance(typed.get("Settings"), dict):
                typed["Settings"] = [{"Option": key, "Value": value}
                                     for key, value in typed["Settings"].items()]
    if not isinstance(patch, dict) or not patch:
        raise DirectFailure("Правка должна быть непустым объектом полей кампании без Id и конверта params.")
    if set(patch) & {"method", "params", "Campaigns"}:
        raise DirectFailure("Передайте поля одной кампании без конверта method, params и Campaigns.")
    for name, value in patch.items():
        if name in BODIES:
            _check_fields(value, name)
        else:
            _check_fields({name: value}, "")
    return patch


def settings(value, where):
    """Однозначная коллекция настроек; неизвестные имена опций не перечисляются в коде."""
    if not isinstance(value, list) or not value:
        raise DirectFailure(f"{where}: ожидается непустой массив Option/Value.")
    found = {}
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"Option", "Value"}
                or not isinstance(item["Option"], str)
                or not NAME.fullmatch(item["Option"])
                or item["Value"] not in ("YES", "NO")):
            raise DirectFailure(f"{where}: нужны Option и Value со значением YES либо NO.")
        if item["Option"] in found:
            raise DirectFailure(f"{where}: настройка «{item['Option']}» указана дважды.")
        found[item["Option"]] = item["Value"]
    return found


def _check_fields(fields, prefix):
    if not isinstance(fields, dict) or not fields:
        raise DirectFailure(f"{prefix or 'Правка'}: ожидается непустой объект полей.")
    for name, value in fields.items():
        path = f"{prefix}.{name}" if prefix else name
        if not NAME.fullmatch(name):
            raise DirectFailure(f"Недопустимое имя поля «{path}».")
        if prefix in ("", *BODIES) and name in READ_ONLY:
            raise DirectFailure(f"«{path}» нельзя изменять действием fields.")
        if prefix in ("", *BODIES) and name in PROTECTED:
            raise DirectFailure(f"Для «{path}» используйте {PROTECTED[name]}: у настройки есть отдельные проверки.")
        if prefix in BODIES and name == "Settings":
            for option in settings(value, path):
                if option in READ_ONLY_SETTINGS:
                    raise DirectFailure(f"«{path}.{option}» доступно только для чтения.")
                if option in SETTING_COMMANDS:
                    raise DirectFailure(f"Для «{path}.{option}» используйте {SETTING_COMMANDS[option]}.")
        elif isinstance(value, dict):
            _check_fields(value, path)
        elif isinstance(value, list) or value is None:
            raise DirectFailure(f"«{path}»: замена массивов и очистка null требуют предметной команды.")
        elif isinstance(value, float) and not math.isfinite(value):
            raise DirectFailure(f"«{path}»: число должно быть конечным.")


def merge_patch(current, patch, prefix=""):
    """Обычные поля остаются частичными; Settings сохраняет неназванные опции."""
    if not isinstance(current, dict):
        raise DirectFailure(f"«{prefix}» не прочитано как объект: правка не подготовлена.")
    result = {}
    for name, value in patch.items():
        path = f"{prefix}.{name}" if prefix else name
        if name not in current:
            raise DirectFailure(f"Поле «{path}» отсутствует в свежем ответе API; проверить правку нельзя.")
        before = current[name]
        if prefix in BODIES and name == "Settings":
            existing, wanted = settings(before, path), settings(value, path)
            missing = wanted.keys() - existing.keys()
            if missing:
                raise DirectFailure(f"В свежем {path} нет настроек: {', '.join(sorted(missing))}.")
            result[name] = [{"Option": key, "Value": wanted.get(key, old)}
                            for key, old in existing.items()]
        elif isinstance(value, dict):
            result[name] = merge_patch(before, value, path)
        elif isinstance(before, (dict, list)):
            raise DirectFailure(f"«{path}»: нельзя заменить структуру одним значением.")
        else:
            result[name] = value
    return result


def changed_fields(patch, prefix=""):
    """Пути для Operation: словари раскрываются, Settings остаётся одной коллекцией."""
    for name, value in patch.items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            yield from changed_fields(value, path)
        else:
            yield path
