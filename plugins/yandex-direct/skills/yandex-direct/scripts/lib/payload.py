"""Краткое представление бинарных данных для вывода и журналов."""

import base64
import binascii
import hashlib


def compact_payload(value, _parent=None):
    """Показать размер и SHA-256 картинки или файла фида вместо base64."""
    if isinstance(value, list):
        return [compact_payload(one, _parent) for one in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, one in value.items():
        binary = key in {"ImageData", "FileFeed.Data"} or (
            _parent == "FileFeed" and key == "Data")
        if not binary or one is None:
            result[key] = compact_payload(one, key)
            continue
        try:
            data = base64.b64decode(one, validate=True)
        except (binascii.Error, ValueError, TypeError):
            result[key] = {"error": "некорректные бинарные данные base64"}
        else:
            result[key] = {"bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
    return result
