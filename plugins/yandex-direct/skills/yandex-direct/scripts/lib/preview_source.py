"""Данные объявления из Ads.get или сохранённой выгрузки для предпросмотра."""

from __future__ import annotations

import json

import incoming
import money
import objects
import rendering
from config import DirectFailure
from responsive import Kit


def read_export(path, ad_id=None) -> tuple:
    """Читает форматы ads.py, его файла карточки и исходного ответа Ads.get."""
    try:
        document = json.loads(incoming.text_of(path, "Выгрузка объявления"))
    except ValueError:
        raise DirectFailure("Выгрузка объявления должна быть корректным JSON.") from None
    source = document
    if isinstance(source, dict):
        source = source.get("result", source)
    if isinstance(source, dict):
        source = source.get("ads", source.get("Ads", source.get("ad", source)))
    records = source if isinstance(source, list) else [source]
    records = [one.get("raw", one) for one in records if isinstance(one, dict)]
    records = [one for one in records if isinstance(one, dict) and one.get("Id")]
    checked = []
    for one in records:
        number = incoming.whole_number(one["Id"], "ID объявления в выгрузке")
        if number < 1:
            raise DirectFailure("ID объявления в выгрузке должен быть положительным.")
        checked.append(dict(one, Id=number))
    records = checked
    if ad_id is not None:
        records = [one for one in records if str(one["Id"]) == str(ad_id)]
    if len(records) != 1:
        raise DirectFailure(
            f"В выгрузке найдено объявлений: {len(records)}. Нужна полная карточка "
            "одного объявления; в выгрузке нескольких выберите --ad ID.")
    return records[0], document if isinstance(document, dict) else {}


def ad_from_record(record: dict, related=None, read=None) -> rendering.Ad:
    """Сохраняет полный текстовый комплект; недоступные дополнения называет явно.

    read(service, params) опционально читает связанные объекты через общий клиент.
    Без read используются только коллекции из JSON, сеть не вызывается.
    """
    body = record.get("ResponsiveAd") or record.get("TextAd")
    if not isinstance(body, dict):
        raise DirectFailure(
            "Предпросмотр из объявления поддерживает ResponsiveAd и TextAd. "
            "Для этого формата подготовьте бриф и укажите недоступные элементы.")
    kit = Kit.of(record, carry=False)
    if not kit.titles or not kit.texts:
        raise DirectFailure(
            "В выгрузке нет полного комплекта заголовков и текстов. "
            "Используйте исходный ответ Ads.get или полную карточку ads.py.")
    related = related or {}
    notes = []
    ad_id = record.get("Id")
    expected = (("AdImages", "VideoExtensions") if record.get("ResponsiveAd")
                else ("AdImageHash", "VideoExtension"))
    for field in (*expected, "SitelinkSetId", "AdExtensions", "PriceExtension"):
        if field not in body:
            notes.append(f"Поле {field} не передано в выгрузке; наличие этого элемента в кабинете неизвестно.")

    def lookup(collection, service, ids, fields, *, id_field="Id",
               selection="Ids", extra=None):
        wanted = list(dict.fromkeys(str(one) for one in ids if one is not None))
        available = related.get(collection) or (related.get("result") or {}).get(collection) or []
        found = {str(one.get(id_field)): one for one in available if isinstance(one, dict)}
        missing = [one for one in wanted if one not in found]
        if missing and read is not None:
            params = {"SelectionCriteria": {
                selection: missing if selection == "AdImageHashes" else [int(one) for one in missing]},
                "FieldNames": fields}
            params.update(extra or {})
            try:
                for one in read(service, params):
                    found[str(one.get(id_field))] = one
            except DirectFailure as failure:
                notes.append(f"Не удалось прочитать {collection}: {failure}")
        return found

    sitelinks = []
    set_id = body.get("SitelinkSetId")
    if set_id:
        sets = lookup("SitelinksSets", "sitelinks", [set_id], ["Id", "Sitelinks"])
        link_set = sets.get(str(set_id))
        if link_set is None:
            notes.append(f"Быстрые ссылки набора {set_id} не загружены; пустой блок не означает их отсутствие.")
        else:
            sitelinks = [rendering.Sitelink(one.get("Title", ""),
                          one.get("Description"), one.get("Href"))
                         for one in link_set.get("Sitelinks") or []]

    callouts = []
    extension_ids = [one.get("AdExtensionId") for one in body.get("AdExtensions") or []]
    extensions = lookup("AdExtensions", "adextensions", extension_ids,
                        ["Id", "Type"], extra={"CalloutFieldNames": ["CalloutText"]})
    for number in extension_ids:
        extension = extensions.get(str(number))
        if extension is None:
            notes.append(f"Дополнение {number} не загружено.")
        elif (extension.get("Callout") or {}).get("CalloutText"):
            callouts.append(extension["Callout"]["CalloutText"])
        else:
            notes.append(f"Дополнение {number} ({extension.get('Type', 'тип не указан')}) не отображается в макете.")

    images = list(objects.items_of(body.get("AdImages")))
    if body.get("AdImageHash"):
        images.append({"ImageHash": body["AdImageHash"]})
    videos = list(objects.items_of(body.get("VideoExtensions")))
    if body.get("VideoExtension"):
        videos.append(body["VideoExtension"])
    other_creatives = []
    for name, extra_body in objects.bodies_of(record).items():
        if extra_body is body:
            continue
        details = "; ".join(f"{field}: {extra_body[field]}"
                            for field in ("Title", "Title2", "Text", "Href") if extra_body.get(field))
        notes.append(f"Дополнительная структура {name} отдельно не воспроизведена. {details}".strip())
        creative = extra_body.get("Creative")
        if isinstance(creative, dict) and creative.get("CreativeId"):
            other_creatives.append(dict(creative, source=name))
            notes.append(f"Креатив {creative['CreativeId']} из {name} показан только миниатюрой; "
                         "его содержимое целиком не проверено.")
    image_hashes = [one.get("ImageHash") for one in images if not one.get("ImageUrl")]
    image_records = lookup("AdImages", "adimages", image_hashes,
                           ["AdImageHash", "OriginalUrl", "PreviewUrl"],
                           id_field="AdImageHash", selection="AdImageHashes")
    creative_ids = [one.get("CreativeId") for one in videos + other_creatives if not one.get("ThumbnailUrl")]
    creative_records = lookup("Creatives", "creatives", creative_ids,
                              ["Id", "Type", "ThumbnailUrl", "PreviewUrl"])
    media = []
    for number, one in enumerate(images, 1):
        identity = str(one.get("ImageHash") or f"image-{number}")
        stored = image_records.get(identity, {})
        media.append({"id": identity, "kind": "image",
                      "url": one.get("ImageUrl") or stored.get("OriginalUrl") or stored.get("PreviewUrl"),
                      "label": f"Объявление {ad_id} · изображение {number}"})
    for number, one in enumerate(videos, 1):
        identity = str(one.get("CreativeId") or f"video-{number}")
        stored = creative_records.get(identity, {})
        media.append({"id": identity, "kind": "video",
                      "url": one.get("ThumbnailUrl") or stored.get("ThumbnailUrl"),
                      "label": f"Объявление {ad_id} · миниатюра видео {number}"})
    for one in other_creatives:
        identity = str(one["CreativeId"])
        stored = creative_records.get(identity, {})
        media.append({"id": identity, "kind": "creative",
                      "url": one.get("ThumbnailUrl") or stored.get("ThumbnailUrl"),
                      "label": f"Объявление {ad_id} · миниатюра креатива из {one['source']}"})
    kit.images = [one["id"] for one in media if one["kind"] == "image"]

    price = body.get("PriceExtension") or {}
    price_text = None
    if price.get("Price") is not None:
        qualifier = {"FROM": "от ", "UP_TO": "до "}.get(price.get("PriceQualifier"), "")
        price_text = qualifier + money.format_api(price["Price"], price.get("PriceCurrency", ""))
        if price.get("OldPrice") is not None:
            price_text += " (прежняя: " + money.format_api(price["OldPrice"], price.get("PriceCurrency", "")) + ")"
    if body.get("Title2"):
        notes.append(f"Дополнительный заголовок: «{body['Title2']}». Отдельный слот в макете не воспроизведён.")
    for field, label in (("VCardId", "визитка"), ("BusinessId", "организация"),
                         ("TrackingPhoneId", "телефон")):
        if body.get(field):
            notes.append(f"Связанный объект «{label}» ({body[field]}) не загружен; проверьте его отдельно.")
    for field, label in (("Carousel", "Карусель"), ("ButtonExtension", "Кнопка")):
        if body.get(field):
            notes.append(f"{label} не воспроизведена в макете: "
                         + json.dumps(body[field], ensure_ascii=False))
    return rendering.Ad(kit, sitelinks=sitelinks, callouts=callouts,
                        price=price_text, media=media, notes=notes)
