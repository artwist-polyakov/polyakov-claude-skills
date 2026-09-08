#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Вся кампания одним файлом: настройки, группы, объявления, фразы."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Строго до импортов из `lib`: каталог запускаемого файла стоит на пути
# импорта первым, и без этой строки `import cache` нашёл бы `scripts/cache.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import incoming  # noqa: E402
import objects  # noqa: E402
from accounts import Accounts, Ambiguous, resolve_account  # noqa: E402
from cache import Cache, add_arguments, outline  # noqa: E402
from config import DirectFailure, preload_secrets, redact, short  # noqa: E402
from direct import Client  # noqa: E402

# Соседние команды, а не библиотеки. Выгрузка делает ровно то же, что три
# читающие команды, и вторая копия их чтения — с ценой вызова, решением об
# оплате и именем записи кэша — разошлась бы с оригиналом молча: файл выгрузки
# перестал бы совпадать с тем, что показывают команды.
import adgroups as adgroups_command  # noqa: E402
import ads as ads_command  # noqa: E402
import campaigns as campaign_command  # noqa: E402
import keywords as keywords_command  # noqa: E402

# Версия формата файла. Схему снапшота для переноса между кабинетами задаёт
# `X-01`; здесь номер нужен затем, чтобы читатель годовалого файла знал, с чем
# имеет дело, а не выяснял это по составу ключей.
SCHEMA = "campaign-dump/1"

# Чего в выгрузке нет и кто это читает. Перечень печатается всегда: «вся
# кампания» — обещание шире того, что здесь лежит, и молчание превратило бы
# его в неверное.
ABSENT = (
    ("расширения и креативы", "references/API_OBJECTS.md"),
    ("фиды и товарные объявления", "references/API_OBJECTS.md"),
    ("статистика и отчёты", "scripts/report.py"),
    ("корректировки ставок", "scripts/bids.py"),
)

INLINE = 4


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


# --------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------

def collect(cache: Cache, shared: Cache, client, accounts, login: str,
            campaign: dict) -> dict:
    """Четыре чтения в один слепок.

    Порядок сверху вниз — кампания, группы, объявления, фразы, — и он же
    порядок, в котором объекты создаются в кабинете. Читателю выгрузки это
    важнее, чем скорость: `X-02` будет воссоздавать кампанию тем же порядком."""
    number = campaign["Id"]
    group_params = objects.group_params(campaign_ids=[number])
    ad_params = objects.ads_params(campaign_ids=[number])
    key_params = objects.keyword_params(campaign_ids=[number])

    groups = adgroups_command.read_groups(cache, client, accounts, login,
                                          group_params)
    ads = ads_command.read_ads(cache, client, accounts, login, ad_params)
    keys = keywords_command.read_keywords(cache, client, accounts, login,
                                          key_params)
    names = adgroups_command.region_names(client, groups.data, shared)
    limits = campaign_command.limits()
    return {
        "schema": SCHEMA,
        "account": login,
        "campaign_id": number,
        # Чем читали — перечни полей всех четырёх запросов целиком. По ответу
        # усечённое чтение объявлений неотличимо от кабинета без комбинаторных.
        "asked": {
            "campaigns": campaign_command.request_params(),
            "adgroups": group_params,
            "ads": ad_params,
            "keywords": key_params,
        },
        "stored_at": {
            "adgroups": groups.stored,
            "ads": ads.stored,
            "keywords": keys.stored,
        },
        "campaign": campaign_command.card_json(campaign, None),
        "groups": [dict(objects.group_row(record),
                        regions=objects.regions_of(record), raw=record)
                   for record in groups.data],
        "ads": [dict(objects.ad_row(record, limits),
                     composition=objects.composition(record, limits), raw=record)
                for record in ads.data],
        "keywords": [dict(objects.keyword_row(record,
                                              campaign.get("Currency") or ""),
                          autotargeting_settings=objects.autotargeting_of(record),
                          raw=record)
                     for record in keys.data],
        "region_names": {str(key): value for key, value in names.items()},
        "absent": [{"what": what, "card": card} for what, card in ABSENT],
    }


def store(cache: Cache, dump: dict):
    """Слепок в файл — тот, на который указывает последняя строка вывода."""
    return cache.write(f"campaign-dump-{dump['campaign_id']}", "structure", dump)


# --------------------------------------------------------------------------
# Сводка
# --------------------------------------------------------------------------

def report(dump: dict, entry, spent: int) -> None:
    campaign = dump["campaign"]
    ads = dump["ads"]
    keys = dump["keywords"]
    autos = [item for item in keys if item["autotargeting"]]
    whole = [item for item in ads if item["composition"]["complete"] is True]
    applies = [item for item in ads if item["composition"]["kit_applies"]]
    rejected = sum(len(item["composition"]["rejected"]) for item in ads)
    lines = [
        f"Кабинет {dump['account']} · кампания {campaign['id']} · "
        f"{campaign['name']} · {campaign['type_ru']}",
        f"Групп {len(dump['groups'])} · объявлений {len(ads)} · "
        f"фраз {len(keys) - len(autos)} · автотаргетингов {len(autos)}",
    ]
    if ads:
        lines.append(f"Комплект полон у {len(whole)} из {len(applies)}, у "
                     f"кого он вообще бывает · отклонённых элементов "
                     f"{rejected}")
    rare_groups = sum(1 for item in dump["groups"] if item["rarely_served"])
    rare_keys = sum(1 for item in keys if item["rarely_served"])
    lines.append(f"Мало показов: групп {rare_groups}, фраз {rare_keys}")
    regions = sorted({str(name) for name in dump["region_names"].values() if name})
    if regions:
        shown = ", ".join(regions[:INLINE])
        more = f" и ещё {len(regions) - INLINE}" if len(regions) > INLINE else ""
        lines.append(f"Регионы групп: {shown}{more}")
    lines.append(f"Читалось с {objects.RESPONSIVE}FieldNames и обеими формами "
                 f"настроек автотаргетинга — перечни полей лежат в файле, "
                 f"полем asked.")
    lines.append("В выгрузке нет: "
                 + "; ".join(f"{what} — {card}" for what, card in ABSENT)
                 + ". Снапшот для переноса между кабинетами — X-01.")
    if spent:
        lines.append(f"Чтение стоило {campaign_command.units_said(spent)}.")
    outline(lines, path=entry.path,
            total=len(dump["groups"]) + len(ads) + len(keys) + 1)


# --------------------------------------------------------------------------
# Аргументы
# --------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    """Ошибка аргументов — код 2, и без секретов в тексте."""

    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def main(argv=None) -> int:
    # До разбора аргументов: argparse печатает негодное значение сам, и токен,
    # случайно попавший в командную строку, ушёл бы в stderr раньше, чем скилл
    # узнал бы, что это токен.
    preload_secrets()

    parser = Parser(description="Вся кампания одним файлом: группы, объявления, фразы.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--campaign", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        type=incoming.campaign_selector, required=True,
                        help="кампания, которую выгружать")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    add_arguments(parser)
    args = parser.parse_args(argv)

    try:
        client = Client.from_env(profile=args.env, warn=warn)
        accounts = Accounts.load(client, warn=warn)
        login = resolve_account(accounts, client, args.account)
        cache = Cache.from_args(args, account=login, warn=warn)

        slice_entry = campaign_command.read_slice(cache, client, accounts, login)
        campaign = campaign_command.one_campaign(slice_entry.data, args.campaign)
        # Справочник регионов кабинету не принадлежит: запись его кэша общая.
        dump = collect(cache, Cache.from_args(args, warn=warn), client,
                       accounts, login, campaign)
        entry = store(cache, dump)
        spent = client.units.report()["spent"]

        if args.json:
            # Сам слепок в stdout не печатается: он занимает контекстное окно
            # ровно тем, ради чего его и кладут в файл. В машиночитаемом ответе
            # — сводка и путь.
            say(json.dumps({
                "account": login,
                "campaign": campaign.get("Id"),
                "schema": SCHEMA,
                "groups": len(dump["groups"]),
                "ads": len(dump["ads"]),
                "keywords": sum(1 for item in dump["keywords"]
                                if not item["autotargeting"]),
                "autotargetings": sum(1 for item in dump["keywords"]
                                      if item["autotargeting"]),
                "complete_kits": sum(1 for item in dump["ads"]
                                     if item["composition"]["complete"] is True),
                "kits_applicable": sum(1 for item in dump["ads"]
                                       if item["composition"]["kit_applies"]),
                "absent": dump["absent"],
                "dump": None if entry.path is None else short(entry.path),
            }, ensure_ascii=False))
        else:
            report(dump, entry, spent)
    except Ambiguous as failure:
        warn(str(failure))
        for match in failure.matches[:5]:
            warn(f"  {match.cabinet.login} · {match.reason}")
        return 1
    except DirectFailure as failure:
        warn(str(failure))
        return 1
    except OSError as failure:
        warn(redact(f"Не удалось записать файл: {failure}"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
