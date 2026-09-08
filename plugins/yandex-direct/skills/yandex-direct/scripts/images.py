#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Загрузка одного изображения и чтение по AdImageHash. Без --apply — проверка."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import policy  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from errors import optional, required  # noqa: E402
from writer import Limits, Operation, Task, Writer, showing  # noqa: E402

FIELDS = ["AdImageHash", "Name", "Type", "OriginalUrl", "PreviewUrl"]


def say(text):
    print(redact(str(text)))


def warn(text):
    print(redact(str(text)), file=sys.stderr)


def dimensions(data: bytes) -> tuple:
    """Формат, ширина и высота из заголовка файла; перекодирования нет."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        if data[12:16] == b"IHDR" and struct.unpack(">I", data[8:12])[0] == 13:
            return "PNG", *struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return "GIF", *struct.unpack("<HH", data[6:10])
    if data.startswith(b"\xff\xd8"):
        position = 2
        while position + 3 < len(data):
            if data[position] != 0xFF:
                break
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if position + 2 > len(data):
                break
            length = struct.unpack(">H", data[position:position + 2])[0]
            if length < 2 or position + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and length >= 8:
                height, width = struct.unpack(">HH", data[position + 3:position + 7])
                return "JPG", width, height
            position += length
    raise DirectFailure("Не удалось прочитать размеры изображения. "
                        "Нужен файл JPG, PNG или GIF с корректным заголовком.")


def read_image(path, name, limits: Limits) -> tuple:
    """Прочитать один файл в пределах справочника и проверить размеры для РСЯ."""
    path = Path(path).expanduser()
    rules = limits.data["media"]["image"]
    allowed = rules["other_ad_types"]
    maximum = allowed["max_bytes"]
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise DirectFailure(f"Изображение превышает предел {maximum} байт.")
    kind, width, height = dimensions(data)
    if kind not in rules["formats"]:
        raise DirectFailure(f"Формат {kind} не разрешён справочником Директа.")
    if width <= 0 or height <= 0:
        raise DirectFailure("Ширина и высота изображения должны быть больше нуля.")
    fits = False
    for rule in allowed["rules"]:
        ratio = width / height
        if "ratio_range" in rule:
            fits = (rule["ratio_range"]["min"] <= ratio <= rule["ratio_range"]["max"]
                    and rule["side_min"] <= width <= rule["side_max"]
                    and rule["side_min"] <= height <= rule["side_max"])
        else:
            fits = (rule["ratio_min"] <= ratio <= rule["ratio_max"]
                    and rule["min_width"] <= width <= rule["max_width"]
                    and rule["min_height"] <= height <= rule["max_height"])
        if fits:
            break
    if not fits:
        raise DirectFailure(
            f"Размер {width}×{height} не подходит для изображения текстового "
            "объявления в РСЯ по media.image.other_ad_types в limits.json.")
    name = path.name if name is None else name
    if not name.strip():
        raise DirectFailure("Имя изображения не должно быть пустым.")
    problems = limits.text_problems("AdImage.Name", name)
    if problems:
        raise DirectFailure("Имя изображения: " + "; ".join(problems))
    item = {"Name": name, "ImageData": base64.b64encode(data).decode("ascii")}
    details = {"file": str(path.resolve()), "name": name, "format": kind,
               "width": width, "height": height, "bytes": len(data),
               "sha256": hashlib.sha256(data).hexdigest()}
    return item, details


def upload_operation(item: dict) -> Operation:
    label = item["Name"]
    changes = [policy.Change(object_id=label, what=said, field=field,
                             after=item[field], service="изображение")
               for field, said in (("Name", "имя"), ("ImageData", "содержимое файла"))]
    return Operation(
        "adimages", "add", params_key="AdImages", items=[item], changes=changes,
        id_field="AdImageHash", read_key="AdImageHashes", results_key="AddResults",
        read={"FieldNames": FIELDS}, labels=[label], search=None, batch_limit=1,
        texts={"Name": "AdImage.Name"}, unread=("ImageData",),
        # При повторной загрузке тех же байтов Директ может сохранить старое
        # имя. Результат загрузки — возвращённый и перечитанный хеш, не переименование.
        expect=[{"ImageData": item["ImageData"]}],
    )


def get_images(client, accounts, account, hashes, limits: Limits) -> list:
    hashes = list(dict.fromkeys(hashes))
    if not hashes or any(not one.strip() for one in hashes):
        raise DirectFailure("Нужен хотя бы один непустой AdImageHash.")
    maximum = limits.selection("adimages", "AdImageHashes") or 1
    records = []
    for start in range(0, len(hashes), maximum):
        chosen = hashes[start:start + maximum]
        need = limits.units_cost("adimages", "get", len(chosen))
        records += client.get_all(
            "adimages", {"SelectionCriteria": {"AdImageHashes": chosen},
                         "FieldNames": FIELDS}, account=account,
            use_operator_units=lambda need=need: accounts.use_operator_units(account, need=need),
        )
    found = {required(one, "AdImageHash", str, "AdImages.get") for one in records}
    for one in records:
        for field in FIELDS[1:]:
            optional(one, field, str, "AdImages.get")
    missing = [one for one in hashes if one not in found]
    if missing:
        raise DirectFailure("Изображения не прочитались: " + ", ".join(missing))
    return records


def image_lines(records):
    lines = []
    for one in records:
        lines.append(f"AdImageHash: {one['AdImageHash']} · имя: {one.get('Name', 'не возвращено')}"
                     f" · тип: {one.get('Type', 'не возвращён')}")
        lines += [f"{field}: {one[field]}" for field in ("OriginalUrl", "PreviewUrl")
                  if one.get(field)]
    return lines


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def common(parser, *, leaf=False):
    default = argparse.SUPPRESS if leaf else None
    parser.add_argument("--env", choices=("production", "test_cabinet"), default=default)
    parser.add_argument("--account", default=default, help="логин кабинета")
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False)


def build_parser():
    parser = Parser(description=__doc__)
    common(parser)
    parent = argparse.ArgumentParser(add_help=False)
    common(parent, leaf=True)
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload", parents=[parent], help="загрузить один файл для РСЯ")
    upload.add_argument("--file", required=True, help="локальный JPG, PNG или GIF")
    upload.add_argument("--name", help="имя в Директе; по умолчанию имя файла")
    mode = upload.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="выполнить загрузку")
    mode.add_argument("--dry-run", action="store_true", help="проверка без записи (умолчание)")
    get = commands.add_parser("get", parents=[parent], help="прочитать изображения по хешам")
    get.add_argument("--hash", action="append", required=True, dest="hashes")
    return parser


def run(args) -> int:
    limits = Limits.load()
    item, details = read_image(args.file, args.name, limits) if args.command == "upload" else (None, None)
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    if args.command == "get":
        records = get_images(client, accounts, account, args.hashes, limits)
        say(json.dumps({"images": records}, ensure_ascii=False) if args.json
            else "\n".join(image_lines(records)))
        return 0
    seen = []
    note = (f"Файл: {details['file']} · {details['format']} · "
            f"{details['width']}×{details['height']} · {details['bytes']} байт · "
            f"SHA-256 {details['sha256']}")
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=[note]), warn=warn)
    report = engine.run(Task("загрузка изображения", [upload_operation(item)]))
    records, details_error = [], ""
    if report.ok and report.applied and report.written:
        try:
            records = get_images(client, accounts, account, report.written, limits)
        except DirectFailure as failure:
            details_error = ("Загрузка подтверждена для " + ", ".join(report.written)
                             + f", но дополнительное чтение метаданных не удалось: {failure}. "
                             "Не повторяйте upload; используйте get --hash.")
            warn(details_error)
    if args.json:
        result = report.machine()
        result.update(file=details, images=records)
        if details_error:
            result["details_error"] = details_error
        say(json.dumps(result, ensure_ascii=False))
    else:
        lines = report.lines()
        if seen:
            lines = lines[len(report.preview):]
        say("\n".join(lines + image_lines(records)))
    return 0 if report.ok else 1


def main(argv=None) -> int:
    preload_secrets()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (DirectFailure, OSError) as failure:
        warn(str(failure))
        return 1


if __name__ == "__main__":
    sys.exit(main())
