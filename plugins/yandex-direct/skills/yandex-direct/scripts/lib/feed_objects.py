"""Общее чтение фидов для команд фидов и товарных объявлений."""

from copy import deepcopy

from config import DirectFailure
from errors import required, TransportFailure
from writer import Limits


READ_PARAMS = {
    "FieldNames": ["Id", "Name", "BusinessType", "SourceType", "FilterSchema",
                   "UpdatedAt", "CampaignIds", "NumberOfItems", "Status", "TitleAndTextSources"],
    "UrlFeedFieldNames": ["Url", "Login", "RemoveUtmTags"],
    "FileFeedFieldNames": ["Filename"],
}

# Статус и счётчик предложений меняются во время фоновой обработки. Их
# изменение само по себе не означает, что кто-то переписал настройки фида.
WRITE_READ_PARAMS = {
    **READ_PARAMS,
    "FieldNames": ["Id", "Name", "BusinessType", "SourceType", "CampaignIds"],
}

BUSINESS_TYPES = ("RETAIL", "HOTELS", "REALTY", "AUTOMOBILES", "FLIGHTS", "OTHER")
STATUSES = {"NEW": "ожидает обработки", "UPDATING": "обрабатывается",
            "DONE": "обработан", "ERROR": "ошибка обработки"}


def read_feeds(client, account, accounts, ids=None):
    """Свежие фиды по ID или весь кабинет. Отсутствующие ID проверяет вызывающий."""
    identifiers = list(dict.fromkeys(ids)) if ids is not None else None
    if identifiers == []:
        return {}
    limits = Limits.load()
    size = limits.selection("feeds") or 10000
    chunks = [None] if identifiers is None else [
        identifiers[start:start + size] for start in range(0, len(identifiers), size)]
    found = {}
    for chunk in chunks:
        params = deepcopy(READ_PARAMS)
        # Пустая SelectionCriteria запрещена: если она есть, Ids обязателен.
        if chunk is not None:
            params["SelectionCriteria"] = {"Ids": chunk}
        need = limits.units_cost("feeds", "get", len(chunk) if chunk is not None else None)
        for item in client.get_all(
                "feeds", params, account=account,
                use_operator_units=lambda need=need: accounts.use_operator_units(account, need=need)):
            identifier = required(item, "Id", int, "Feeds.get")
            if (isinstance(identifier, bool) or identifier < 1 or identifier in found
                    or (chunk is not None and identifier not in chunk)):
                raise TransportFailure(
                    f"Feeds.get вернул некорректный, повторный или незапрошенный ID {identifier}.",
                    retryable=False)
            required(item, "Name", str, "Feeds.get")
            for field, allowed in (("BusinessType", BUSINESS_TYPES), ("SourceType", ("URL", "FILE")),
                                   ("Status", STATUSES)):
                if required(item, field, str, "Feeds.get") not in allowed:
                    raise DirectFailure(f"Фид {identifier}: неизвестное значение {field}.")
            source = "UrlFeed" if item["SourceType"] == "URL" else "FileFeed"
            body = required(item, source, dict, "Feeds.get")
            required(body, "Url" if source == "UrlFeed" else "Filename", str, "Feeds.get")
            if source == "UrlFeed" and body.get("RemoveUtmTags") not in ("YES", "NO"):
                raise DirectFailure(f"Фид {identifier}: не прочитан RemoveUtmTags.")
            found[identifier] = item
    return found
