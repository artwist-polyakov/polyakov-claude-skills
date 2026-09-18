"""Чтение связанных быстрых ссылок и уточнений по известным ID."""

from __future__ import annotations

import objects
from cache import Cache, signature
from config import DirectFailure
from errors import optional, required
from incoming import whole_number

READ_PARAMS = {
    "sitelinks": {"FieldNames": ["Id"],
                  "SitelinkFieldNames": ["Title", "Href", "Description", "TurboPageId"]},
    "adextensions": {"FieldNames": ["Id", "Type", "Status", "StatusClarification", "Associated"],
                     "CalloutFieldNames": ["CalloutText"]},
}


def ids_of(records) -> dict:
    """ID дополнений всех структур объявления; повторные ссылки объединяются."""
    sitelinks, extensions = [], []
    for record in records:
        for body in objects.bodies_of(record).values():
            if body.get("SitelinkSetId") is not None:
                sitelinks.append(body["SitelinkSetId"])
            extensions.extend(required(one, "AdExtensionId", int, "Ads.get.AdExtensions")
                              for one in objects.items_of(body.get("AdExtensions")))
    return {"sitelink_ids": list(dict.fromkeys(sitelinks)),
            "extension_ids": list(dict.fromkeys(extensions))}


class _Incomplete(DirectFailure):
    def __init__(self, records, missing):
        super().__init__("Не все запрошенные дополнения вернулись из Директа.")
        self.records, self.missing = records, missing


def _ids(values) -> list:
    found = [whole_number(one, "ID дополнения") for one in values]
    if any(one < 1 for one in found):
        raise DirectFailure("ID дополнений должны быть положительными.")
    return sorted(set(found))


def _checked(records, collection, ids):
    found = {}
    for record in records:
        number = required(record, "Id", int, collection)
        if number not in ids:
            raise DirectFailure(f"{collection}: вернулся незапрошенный ID {number}.")
        if collection == "SitelinksSets":
            required(record, "Sitelinks", list, collection)
        else:
            kind = required(record, "Type", str, collection)
            for field in ("State", "Associated"):
                optional(record, field, str, collection)
            if kind == "CALLOUT":
                callout = required(record, "Callout", dict, collection)
                required(callout, "CalloutText", str, collection)
        found[number] = record
    missing = [one for one in ids if one not in found]
    if missing:
        raise _Incomplete(list(found.values()), missing)
    return list(found.values())


def read_related(client, accounts, login, *, sitelink_ids=(), extension_ids=(),
                 cache=None, limits=None) -> dict:
    """Коллекции для JSON/превью и ExtensionsRead с полнотой, пропусками, ошибками.

    Пустой список никогда не превращается в чтение всего кабинета. Ошибки
    отдельных порций сохраняются отдельно от ID, не вернувшихся в успешном
    ответе. Неполная порция не записывается в кэш.
    """
    requested = {"SitelinksSets": _ids(sitelink_ids),
                 "AdExtensions": _ids(extension_ids)}
    status = {"complete": True, "requested": requested,
              "missing": {name: [] for name in requested}, "errors": []}
    result = {"SitelinksSets": [], "AdExtensions": [], "ExtensionsRead": status}
    if not any(requested.values()):
        return result
    if limits is None:
        from writer import Limits
        limits = Limits.load()
    cache = cache if cache is not None else Cache(login)
    for collection, service in (
        ("SitelinksSets", "sitelinks"), ("AdExtensions", "adextensions"),
    ):
        ids = requested[collection]
        # Если отдельного предела отбора нет в справочнике, читаем небольшими
        # порциями. Это не ограничивает общий размер выбранного комплекта.
        size = limits.selection(service, "Ids") or 100
        for start in range(0, len(ids), size):
            chosen = ids[start:start + size]
            params = {"SelectionCriteria": {"Ids": chosen}, **READ_PARAMS[service]}

            def produce():
                need = limits.units_cost(service, "get", len(chosen))
                records = client.get_all(
                    service, params, collection=collection, account=login,
                    use_operator_units=(lambda: accounts.use_operator_units(login, need=need))
                    if accounts is not None else None,
                )
                return _checked(records, collection, chosen)

            try:
                entry = cache.through(f"related-{service}-{signature(params)}", "structure", produce)
                records = _checked(entry.data, collection, chosen)
            except _Incomplete as failure:
                records = failure.records
                status["missing"][collection].extend(failure.missing)
            except DirectFailure as failure:
                records = []
                status["errors"].append({"collection": collection, "ids": chosen,
                                         "message": str(failure)})
            result[collection].extend(records)
    status["complete"] = not status["errors"] and not any(status["missing"].values())
    return result


def notes_of(related) -> list:
    """Ошибки и пропуски чтения — те же формулировки в карточке и превью."""
    status = (related or {}).get("ExtensionsRead") or {}
    notes = []
    for error in status.get("errors") or []:
        ids = ", ".join(map(str, error.get("ids") or []))
        notes.append(f"Не удалось прочитать {error.get('collection')} (ID {ids}): "
                     f"{error.get('message')}")
    for collection, ids in (status.get("missing") or {}).items():
        if ids:
            notes.append(f"{collection}: не вернулись ID {', '.join(map(str, ids))}; "
                         "связи в объявлении сохранены, содержимое недоступно.")
    return notes
