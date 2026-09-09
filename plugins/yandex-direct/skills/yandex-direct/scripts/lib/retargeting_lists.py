"""Общее чтение условий Директа для просмотра и привязки к группам."""

from config import DirectFailure
from errors import required
from writer import Limits

LIST_FIELDS = ("Id", "Name", "Description", "Type", "Rules", "Scope",
               "IsAvailable", "AvailableForTargetsInAdGroupTypes")


def read_lists(client, account, accounts, ids=None) -> dict:
    """Свежие условия по ID или весь кабинет; возвращает словарь по Id."""
    identifiers = list(dict.fromkeys(ids)) if ids is not None else None
    if identifiers == []:
        return {}
    limits = Limits.load()
    size = limits.selection("retargetinglists") or 1000
    chunks = [None] if identifiers is None else [
        identifiers[start:start + size] for start in range(0, len(identifiers), size)]
    found = {}
    for chunk in chunks:
        need = limits.units_cost("retargetinglists", "get", len(chunk) if chunk is not None else None)
        for item in client.get_all(
                "retargetinglists", {
                    "SelectionCriteria": {} if chunk is None else {"Ids": chunk},
                    "FieldNames": list(LIST_FIELDS)}, account=account,
                use_operator_units=lambda need=need: accounts.use_operator_units(account, need=need)):
            identifier = required(item, "Id", int, "RetargetingLists.get")
            if chunk is not None and identifier not in chunk:
                raise DirectFailure(f"RetargetingLists.get вернул незапрошенное условие {identifier}.")
            found[identifier] = item
    return found
