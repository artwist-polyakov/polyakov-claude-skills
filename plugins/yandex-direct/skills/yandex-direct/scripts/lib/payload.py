"""Краткое представление бинарных данных для вывода и журналов."""

import base64
import binascii
import hashlib


def compact_payload(value):
    """Заменить ImageData размером и SHA-256, не меняя исходный запрос."""
    if isinstance(value, list):
        return [compact_payload(one) for one in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, one in value.items():
        if key != "ImageData" or one is None:
            result[key] = compact_payload(one)
            continue
        try:
            data = base64.b64decode(one, validate=True)
        except (binascii.Error, ValueError, TypeError):
            result[key] = {"error": "некорректные бинарные данные base64"}
        else:
            result[key] = {"bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
    return result
