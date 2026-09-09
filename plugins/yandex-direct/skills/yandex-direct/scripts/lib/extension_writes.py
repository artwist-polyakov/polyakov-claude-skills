"""Привязки быстрых ссылок и уточнений к прочитанным объявлениям."""

from collections import defaultdict

import responsive
from config import DirectFailure
from incoming import whole_number
from policy import Change
from writer import Limits, Operation

UNSET = object()
BODY = {"TEXT_AD": "TextAd", "RESPONSIVE_AD": "ResponsiveAd"}
READ = {
    "FieldNames": ["Id", "Type", "State"],
    # Обе структуры запрашиваются всегда: иначе комбинаторное объявление
    # может прийти как TextAd с одним заголовком.
    "TextAdFieldNames": ["Href", "TurboPageId", "SitelinkSetId", "AdExtensions"],
    "ResponsiveAdFieldNames": ["Titles", "Texts", "Href", "SitelinkSetId",
                               "AdExtensions"],
}


def _positive(value, name):
    value = whole_number(value, name)
    if value < 1:
        raise DirectFailure(f"{name} должен быть положительным.")
    return value


def _texts(body, field, item_field, where):
    values = body.get(field)
    if not isinstance(values, list) or not values:
        raise DirectFailure(f"{where}: не прочитан полный набор {field}.")
    if any(not isinstance(one, dict) or not isinstance(one.get(item_field), str)
           or not one[item_field] for one in values):
        raise DirectFailure(f"{where}: повреждён набор {field}.")
    return [one[item_field] for one in values]


def _snapshot(record):
    if not isinstance(record, dict):
        raise DirectFailure("Объявление должно быть объектом из Ads.get.")
    identifier = _positive(record.get("Id"), "ID объявления")
    kind = record.get("Type")
    if kind not in BODY:
        raise DirectFailure(f"Объявление {identifier}: тип {kind!r} не поддерживается; "
                            "нужен TEXT_AD или RESPONSIVE_AD.")
    body = record.get(BODY[kind])
    where = f"Объявление {identifier}"
    if not isinstance(body, dict):
        raise DirectFailure(f"{where}: не прочитана структура {BODY[kind]}.")
    if any(field not in body for field in ("SitelinkSetId", "AdExtensions", "Href")):
        raise DirectFailure(f"{where}: не прочитаны SitelinkSetId, AdExtensions или Href.")
    if body["Href"] is not None and not isinstance(body["Href"], str):
        raise DirectFailure(f"{where}: повреждён Href.")
    sitelinks = body["SitelinkSetId"]
    if sitelinks is not None:
        sitelinks = _positive(sitelinks, f"{where}, SitelinkSetId")
    extensions = body["AdExtensions"]
    extensions = [] if extensions is None else extensions
    if not isinstance(extensions, list):
        raise DirectFailure(f"{where}: AdExtensions должен быть массивом.")
    callouts = []
    for one in extensions:
        if not isinstance(one, dict) or one.get("Type") != "CALLOUT":
            raise DirectFailure(f"{where}: уточнение не прочитано или его тип не CALLOUT.")
        callouts.append(_positive(one.get("AdExtensionId"), f"{where}, AdExtensionId"))
    if len(callouts) != len(set(callouts)):
        raise DirectFailure(f"{where}: повторяются AdExtensionId в ответе.")
    result = {"Id": identifier, "Type": kind, "State": record.get("State"),
              "SitelinkSetId": sitelinks, "AdExtensionIds": sorted(callouts),
              "Href": body["Href"], "TurboPageId": body.get("TurboPageId")}
    if kind == "RESPONSIVE_AD":
        result["Titles"] = _texts(body, "Titles", "Title", where)
        result["Texts"] = _texts(body, "Texts", "Text", where)
    return result


def _guard(snapshots):
    def unchanged(records):
        problems = []
        for before in snapshots:
            identifier = before["Id"]
            try:
                after = _snapshot(records.get(identifier))
            except DirectFailure as failure:
                problems.append(f"Объявление {identifier}: {failure}")
                continue
            changed = [field for field in before if before[field] != after.get(field)]
            if changed:
                problems.append(f"Объявление {identifier} изменилось после чтения: "
                                f"{', '.join(changed)}. Перечитайте и подготовьте привязки заново.")
        return problems
    return unchanged


def _operation(snapshots, sitelink_set, callout_ids):
    name = BODY[snapshots[0]["Type"]]
    items, expected, changes = [], [], []
    derived, unread, clears = [name], [], []
    if callout_ids is not UNSET:
        derived.append((f"{name}.CalloutSetting", f"{name}.AdExtensions"))
        if callout_ids:
            # Operation — команда, которой нет в Ads.get. Итоговый набор
            # проверяется целиком по AdExtensionId, без послабления full.
            unread.append(f"{name}.CalloutSetting.AdExtensions.Operation")
        else:
            clears.append(f"{name}.CalloutSetting")
    if sitelink_set is None:
        clears.append(f"{name}.SitelinkSetId")
    for before in snapshots:
        identifier = before["Id"]
        sent, wanted = {}, {}
        if name == "ResponsiveAd":
            kit = responsive.Kit(before["Titles"], before["Texts"])
            sent.update(kit.updated())
            wanted.update(kit.expected())
        if sitelink_set is not UNSET:
            sent["SitelinkSetId"] = wanted["SitelinkSetId"] = sitelink_set
        if callout_ids is not UNSET:
            sent["CalloutSetting"] = ({"AdExtensions": [
                {"AdExtensionId": one, "Operation": "SET"} for one in callout_ids
            ]} if callout_ids else None)
            wanted["AdExtensions"] = ([{"AdExtensionId": one} for one in callout_ids]
                                      if callout_ids else None)
        labels = {"Titles": "заголовки (сохранить)", "Texts": "тексты (сохранить)",
                  "SitelinkSetId": "набор быстрых ссылок",
                  "CalloutSetting": "уточнения (итоговый набор)"}
        changes += [Change(object_id=identifier, what=labels[field],
                           field=f"{name}.{field}", after=value, service="объявление")
                    for field, value in sent.items()]
        items.append({"Id": identifier, name: sent})
        expected.append({"Id": identifier, name: wanted})
    texts, collections = responsive.rules_for(items)
    return Operation("ads", "update", params_key="Ads", items=items, expect=expected,
                     changes=changes, read=READ, derived=derived, unread=unread,
                     clears=clears, texts=texts, collections=collections,
                     guard=_guard(snapshots))


def build_binding_operations(records, *, sitelink_set=UNSET, callout_ids=UNSET):
    """Собрать операции для полного итогового набора привязок.

    records — массив Ads.get либо словарь ID → объект. UNSET оставляет поле
    прежним, sitelink_set=None снимает быстрые ссылки, callout_ids=[] снимает
    все уточнения. Возвращает [] при совпадении всех привязок. Ввод проверяется
    целиком до сборки операций; разные типы объявлений пишутся отдельно.
    """
    if sitelink_set is UNSET and callout_ids is UNSET:
        raise DirectFailure("Не заданы привязки быстрых ссылок или уточнений.")
    if not isinstance(records, (list, tuple, dict)):
        raise DirectFailure("Объявления должны быть массивом или словарём ID → объект.")
    if sitelink_set is not UNSET and sitelink_set is not None:
        sitelink_set = _positive(sitelink_set, "ID набора быстрых ссылок")
    if callout_ids is not UNSET:
        if not isinstance(callout_ids, (list, tuple)):
            raise DirectFailure("Итоговый набор уточнений должен быть массивом ID.")
        callout_ids = list(dict.fromkeys(_positive(one, "ID уточнения") for one in callout_ids))
        problems = Limits.load().collection_problems("AdExtensionIds", callout_ids)
        if problems:
            raise DirectFailure("Уточнения: " + "; ".join(problems))
    snapshots = [_snapshot(one) for one in (records.values() if isinstance(records, dict)
                                           else records)]
    if isinstance(records, dict):
        for key, before in zip(records, snapshots):
            if _positive(key, "Ключ объявления") != before["Id"]:
                raise DirectFailure("Ключ объявления не совпадает с его полем Id.")
    if not snapshots:
        raise DirectFailure("Не переданы объявления для изменения привязок.")
    ids = [one["Id"] for one in snapshots]
    if len(ids) != len(set(ids)):
        raise DirectFailure("В списке объявлений повторяются ID.")
    groups = defaultdict(list)
    for before in snapshots:
        if before["State"] == "ARCHIVED":
            raise DirectFailure(f"Объявление {before['Id']} архивное; привязки менять нельзя.")
        if sitelink_set is not UNSET and sitelink_set is not None and not before["Href"]:
            if before["Type"] != "TEXT_AD" or not before["TurboPageId"]:
                raise DirectFailure(f"Объявление {before['Id']}: для быстрых ссылок нужен Href.")
        same_sitelinks = sitelink_set is UNSET or sitelink_set == before["SitelinkSetId"]
        same_callouts = callout_ids is UNSET or sorted(callout_ids) == before["AdExtensionIds"]
        if not (same_sitelinks and same_callouts):
            groups[before["Type"]].append(before)
    return [_operation(group, sitelink_set, callout_ids) for group in groups.values()]
