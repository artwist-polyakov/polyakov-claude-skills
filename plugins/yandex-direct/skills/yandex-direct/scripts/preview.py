#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Превью объявления: матрица комбинаций комплекта самодостаточной страницей."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import cache as cache_module  # noqa: E402
import combinatorial  # noqa: E402
import incoming  # noqa: E402
import objects  # noqa: E402
import preview_media  # noqa: E402
import preview_source  # noqa: E402
import rendering  # noqa: E402
import ads as ads_command  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, excerpt, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from responsive import Kit  # noqa: E402
from writer import Limits  # noqa: E402

DEFAULT_OUT = cache_module.CACHE_DIR / ".previews"

# Формы брифа здесь нет: бриф у человека один, и объявлена она один раз, в
# `combinatorial.BRIEF`. Своё объявление тут было, и второе объявление одной
# формы расходилось с первым молча — каждое проверяло свой файл (`P-03`).
#
# Поля, которые команда читает. Остальные она принимает и не читает: пометки
# агента, сегменты, фразы и группу подстановки читает `ads_generate.py` — здесь
# картинка, а не вердикт. Что именно не читается, называет подсказка `--brief`,
# собранная по общей форме.
BRIEF_READ = ("titles", "texts", "href", "display_url_path", "note", "images",
              "sitelinks", "callouts", "vcard", "price")


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


# --------------------------------------------------------------------------
# Материал
# --------------------------------------------------------------------------

def read_brief(path) -> dict:
    """Бриф из файла, разобранный по общей форме `combinatorial.BRIEF`.

    Своего разбора здесь нет: типы, перечень полей, кодировка и пустое
    значение — общее правило скилла, и живёт оно в `incoming` (`F-15`). Своей
    формы тоже нет: тот же файл читает `ads_generate.py`, и вердикт по составу
    полей у обеих команд один (`P-03`)."""
    return incoming.document(path, "Бриф", combinatorial.BRIEF)


def sitelink_argument(value: str):
    """`--sitelink «Заголовок|Описание|Адрес»`: кроме заголовка всё лишнее."""
    title, _, rest = str(value).partition("|")
    description, _, href = rest.partition("|")
    if not title.strip():
        raise argparse.ArgumentTypeError(
            "Быстрая ссылка без текста не показывается. Формат — "
            "«Заголовок|Описание|Адрес», кроме заголовка всё можно не писать.")
    return rendering.Sitelink(title.strip(), description.strip() or None,
                              href.strip() or None)


def pair_argument(value: str):
    """`--pair 3x1`: место заголовка и место текста, оба с единицы."""
    said = str(value).lower().replace("×", "x")
    left, mark, right = said.partition("x")
    if not mark:
        raise argparse.ArgumentTypeError(
            f"Пара пишется как «3x1» — место заголовка, «x», место текста. "
            f"Пришло «{excerpt(value, 20)}».")
    try:
        places = (incoming.whole_number(left.strip(), "Место заголовка"),
                  incoming.whole_number(right.strip(), "Место текста"))
    except DirectFailure as failure:
        raise argparse.ArgumentTypeError(str(failure)) from None
    if places[0] < 1 or places[1] < 1:
        raise argparse.ArgumentTypeError(
            "Места в комплекте считаются с единицы, а не с нуля.")
    return places


def kit_of(brief: dict, args) -> Kit:
    """Комплект из брифа и аргументов. Пустой комплект — отказ."""
    titles = list(args.title or brief.get("titles") or [])
    texts = list(args.text or brief.get("texts") or [])
    if not titles or not texts:
        raise DirectFailure(
            f"В комплекте заголовков {len(titles)}, текстов {len(texts)}. "
            f"Директ требует хотя бы один каждого, а пустая матрица чиста "
            f"ровно потому, что в ней ничего нет.")
    return Kit(titles, texts,
               images=(list(args.image or ())
                       or list(brief.get("images") or [])),
               href=args.href or brief.get("href"),
               display_url_path=(args.display_url_path
                                 or brief.get("display_url_path")))


def ad_of(brief: dict, args) -> rendering.Ad:
    """Объявление целиком: комплект плюс то, что покажется рядом."""
    links = list(args.sitelink or ()) or [
        rendering.Sitelink(one["title"], one.get("description"),
                           one.get("href"))
        for one in brief.get("sitelinks") or []]
    return rendering.Ad(
        kit_of(brief, args),
        sitelinks=links,
        callouts=list(args.callout or ()) or list(brief.get("callouts") or []),
        vcard=args.vcard or brief.get("vcard"),
        price=args.price or brief.get("price"),
    )


def templated(kit: Kit) -> bool:
    """Есть ли в комплекте шаблон `#…#`, который сложится только на показе."""
    return any("#" in one for one in list(kit.titles) + list(kit.texts))


def source_ad(args):
    """Чтение существующего объявления; выгрузка не требует подключения к API."""
    if args.from_json:
        record, related = preview_source.read_export(args.from_json, args.ad)
        ad = preview_source.ad_from_record(record, related)
        ad.notes.insert(0, "Данные из сохранённой выгрузки; актуальность в кабинете не проверялась.")
    else:
        client = Client.from_env(profile=args.env, warn=warn)
        accounts = Accounts.load(client, warn=warn)
        login = resolve_account(accounts, client, args.account)
        cache = cache_module.Cache.from_args(args, account=login, warn=warn)
        entry = ads_command.read_ads(cache, client, accounts, login,
                                     objects.ads_params(ad_ids=[args.ad]))
        found = [one for one in entry.data if one.get("Id") == args.ad]
        if len(found) != 1:
            raise DirectFailure(f"Объявление {args.ad} в кабинете {login} не найдено.")
        record = found[0]
        limits = Limits.load()

        def read(service, params):
            need = limits.units_cost(service, "get")
            return client.get_all(service, params, account=login,
                use_operator_units=lambda: accounts.use_operator_units(login, need=need))

        ad = preview_source.ad_from_record(record, read=read)
        ad.notes.insert(0, f"Кабинет {login}. Объявление "
                        + ("из кэша; --no-cache обновит данные." if entry.hit else "прочитано из API."))
    args.out = str(Path(args.out) / f"ad-{record['Id']}")
    return ad, (f"Объявление {record['Id']} · кампания {record.get('CampaignId', '—')} · "
                f"{record.get('Status', 'статус не указан')} · {record.get('State', 'состояние не указано')}")


def material(args) -> tuple:
    """Один путь сборки данных для матрицы и отдельной пары."""
    original = None
    if args.ad is not None or args.from_json:
        original, heading = source_ad(args)
        brief = {"titles": original.kit.titles, "texts": original.kit.texts,
                 "href": original.kit.href, "display_url_path": original.kit.display_url_path,
                 "images": original.kit.images, "callouts": original.callouts,
                 "sitelinks": [{"title": one.title, "description": one.description,
                                "href": one.href} for one in original.sitelinks],
                 "vcard": original.vcard, "price": original.price, "note": heading}
    else:
        brief = read_brief(args.brief) if args.brief else {}
    ad = ad_of(brief, args)
    if original is not None:
        ad.notes = list(original.notes)
        if any((args.title, args.text, args.image, args.image_url, args.sitelink,
                args.callout, args.href, args.display_url_path, args.vcard, args.price)):
            ad.notes.append("Поля из аргументов заменены только в предпросмотре; кабинет не изменён.")
    media = list(original.media) if original is not None else []
    if args.image:
        media = [one for one in media if one["kind"] != "image" or one["id"] in args.image]
    known = {one["id"] for one in media if one["kind"] == "image"}
    media += [{"id": identity, "kind": "image", "url": None, "label": "Изображение"}
              for identity in ad.kit.images if identity not in known]
    if args.image_url:
        media = [one for one in media if one["kind"] != "image"]
        media += [{"id": f"image-{index}", "kind": "image", "url": url,
                   "label": f"Изображение {index}"}
                  for index, url in enumerate(args.image_url, 1)]
        ad.kit.images = [one["id"] for one in media if one["kind"] == "image"]
    ad.media = preview_media.embed_media(media, load=not args.no_images)
    ad.notes += [f"{one['label']} ({one['id']}): {one['error']}"
                 for one in ad.media if one.get("error")]
    return ad, args.heading or brief.get("note") or ad.kit.summary()


# --------------------------------------------------------------------------
# Отрисовка
# --------------------------------------------------------------------------

def chosen(args) -> list:
    return list(args.placement or rendering.PLACEMENTS)


def write_page(path: Path, page: str) -> Path:
    """Записать страницу, убедившись, что она никуда не ходит."""
    outside = rendering.external_references(page)
    if outside:
        raise DirectFailure(
            f"В собранном превью нашлись ссылки наружу: "
            f"{'; '.join(outside)}. Такой файл разваливается там, где его "
            f"откроют без сети, и записан он не будет.")
    # Кодируется до открытия файла, а не при записи в него. Одиночная
    # суррогатная пара приходит из брифа (`"\ud800"` — законный JSON) и роняет
    # кодирование: `path.write_text` к этому моменту файл уже создал и оставил
    # бы на диске пустой — то есть превью, которого нет, но которое лежит.
    try:
        body = page.encode("utf-8")
    except UnicodeEncodeError as failure:
        raise DirectFailure(
            f"В превью попал символ, который не записывается в UTF-8 "
            f"(позиция {failure.start} собранной страницы). Так выглядит "
            f"одиночная суррогатная пара в брифе: JSON её принимает, а файла "
            f"с ней не существует.") from failure
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def draw(args, pairs, tag: str) -> tuple:
    """Нарисовать выбранные плейсменты. Возвращает пару «файлы, замечания»."""
    ad, heading = material(args)
    limits = Limits.load()
    out = Path(args.out)
    made, notes, by_place = [], list(ad.notes), {}
    if templated(ad.kit):
        notes.append(
            "в комплекте есть шаблон `#…#`: пара складывается на показе после "
            "подстановки фразы, и нарисован здесь текст шаблона, а не то, что "
            "увидит человек — подстановку считает `ads_generate.py`")
    for name in chosen(args):
        placement = rendering.load(name)
        drawn = pairs(ad)
        page, said = rendering.matrix_html(
            placement, ad, drawn, theme=args.theme, limits=limits,
            heading=heading, footer=FOOTER)
        made.append(write_page(out / f"{name}-{tag}.html", page))
        # Замечания сводятся дважды: по парам внутри места показа — это
        # делает сама отрисовка, потому что число нарисованных карточек знает
        # только она, — и по местам показа между собой, здесь. Одно и то же
        # приходит от каждой пары и от каждого плейсмента, и напечатанное
        # восемьдесят четыре раза оно перестаёт читаться вместе с остальным.
        for one in said:
            by_place.setdefault(one, []).append(placement.title)
    notes += [one if len(where) == len(chosen(args))
              else f"{', '.join(where)} · {one}"
              for one, where in by_place.items()]
    return made, notes


# Подвал каждой картинки. Стоит в самом файле, а не только в отчёте команды:
# картинку пересылают отдельно от вывода, и оговорка, оставшаяся в терминале,
# до того, кто согласовывает тексты, не доедет.
FOOTER = (
    "Зачёркнутое — за пределом поля: Директ такое объявление не примет.",
    "Обрезание местом показа Яндекс числами не публикует — здесь его нет; "
    "достоверный вид даёт «Нацелиться на объявление» в кабинете.",
)


def run_matrix(args) -> int:
    made, notes = draw(args, lambda ad: combinatorial.matrix(
        ad.kit.titles, ad.kit.texts).pairs, "matrix")
    return report(args, made, notes)


def run_pair(args) -> int:
    """Одна пара или несколько названных — для разбора проблемной комбинации."""
    def pairs(ad):
        grid = combinatorial.matrix(ad.kit.titles, ad.kit.texts)
        at = {(one.title_place, one.text_place): one for one in grid.pairs}
        chosen_pairs = []
        for place in args.pair:
            found = at.get(place)
            if found is None:
                raise DirectFailure(
                    f"Пары {place[0]} × {place[1]} в комплекте нет: "
                    f"заголовков {len(ad.kit.titles)}, текстов "
                    f"{len(ad.kit.texts)}. Нарисованная «на всякий случай» "
                    f"пустая карточка выглядела бы разобранной комбинацией.")
            chosen_pairs.append(found)
        return chosen_pairs

    tag = "-".join(f"{one}x{two}" for one, two in args.pair)
    made, notes = draw(args, pairs, tag)
    return report(args, made, notes)


def run_placements(args) -> int:
    """Какие места показа умеет рисовать превью и чем они отличаются."""
    places = [rendering.load(one) for one in rendering.PLACEMENTS]
    lines = []
    for placement in places:
        lines.append(f"{placement.name} — {placement.title}, ширина "
                     f"{placement.width}")
        lines.append(f"    элементы: {', '.join(placement.slots)}")
    if args.json:
        say(json.dumps({"placements": [
            {"name": one.name, "title": one.title, "width": one.width,
             "slots": list(one.slots)}
            for one in places]}, ensure_ascii=False))
        return 0
    cache_module.outline(lines)
    return 0


def report(args, made, notes) -> int:
    if args.json:
        say(json.dumps({"files": [str(one) for one in made],
                        "notes": notes, "theme": args.theme},
                       ensure_ascii=False))
        return 0
    lines = [f"Нарисовано мест показа: {len(made)}, тема «{args.theme}»."]
    lines += [f"{rendering.load(name).title}: {path}"
              for name, path in zip(chosen(args), made)]
    lines += [f"  · {one}" for one in notes[:20]]
    if len(notes) > 20:
        lines.append(f"  · ещё замечаний: {len(notes) - 20} — целиком `--json`")
    lines.append("Вердикт по комплекту даёт `ads_generate.py check`; здесь "
                 "показ.")
    cache_module.outline(lines, path=made[0].parent if made else None,
                         total=len(made))
    return 0


# --------------------------------------------------------------------------
# Разбор аргументов
# --------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def add_common(parser, *, leaf: bool) -> None:
    """Общие флаги — и на корень, и на каждое действие.

    Причина та же, что у `ads_generate.py`: `argparse` разбирает флаги корня до
    подкоманды, и `matrix --json` без этого падал бы кодом 2. У листьев
    умолчание `SUPPRESS`, иначе лист затирает значение, пришедшее с корня."""
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="машиночитаемый вывод")


def add_material(step) -> None:
    step.add_argument("--brief", metavar="ФАЙЛ",
                      help=combinatorial.brief_help(BRIEF_READ))
    step.add_argument("--ad", type=int, metavar="ID",
                      help="прочитать объявление из кабинета или выбрать его в --from-json")
    step.add_argument("--from-json", metavar="ФАЙЛ",
                      help="полная JSON-выгрузка ads.py; без обращения к API Директа")
    step.add_argument("--account", metavar="ЛОГИН", help="кабинет для --ad")
    step.add_argument("--env", choices=("production", "test_cabinet"),
                      help="профиль подключения для --ad")
    cache_module.add_arguments(step)
    step.add_argument("--no-images", action="store_true",
                      help="не загружать изображения и миниатюры, оставить подписи")
    step.add_argument("--title", action="append",
                      help="заголовок; повторяется. Заменяет взятые из брифа")
    step.add_argument("--text", action="append", help="текст; повторяется")
    step.add_argument("--sitelink", action="append", type=sitelink_argument,
                      metavar="ТЕКСТ|ОПИСАНИЕ",
                      help="быстрая ссылка; повторяется")
    step.add_argument("--image", action="append", metavar="ХЕШ",
                      help="хеш изображения; повторяется")
    step.add_argument("--image-url", action="append", metavar="HTTPS_URL",
                      help="загрузить и встроить изображение; повторяется, заменяет изображения источника")
    step.add_argument("--callout", action="append", metavar="ТЕКСТ",
                      help="уточнение; повторяется")
    step.add_argument("--vcard", metavar="СТРОКА",
                      help="визитка одной строкой: город, телефон, часы")
    step.add_argument("--price", metavar="ЦЕНА",
                      help="цена для карточки товарной галереи")
    step.add_argument("--href", metavar="ССЫЛКА", help="ссылка объявления")
    step.add_argument("--display-url-path", dest="display_url_path",
                      metavar="ПУТЬ", help="отображаемая ссылка")
    step.add_argument("--placement", action="append",
                      choices=rendering.PLACEMENTS,
                      help="место показа; повторяется. По умолчанию все")
    step.add_argument("--theme", choices=rendering.THEMES, default="auto",
                      help="тема: `auto` подстраивается под окно читателя")
    step.add_argument("--out", metavar="КАТАЛОГ", default=str(DEFAULT_OUT),
                      help="куда складывать страницы")
    step.add_argument("--heading", metavar="СТРОКА",
                      help="заголовок страницы; по умолчанию состав комплекта")


def build_parser() -> Parser:
    parser = Parser(
        description="Превью объявления страницей: матрица комбинаций "
                    "комплекта для четырёх мест показа.")
    add_common(parser, leaf=False)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common, leaf=True)

    actions = parser.add_subparsers(dest="action", required=True)

    grid = actions.add_parser("matrix", parents=[common],
                              help="все комбинации комплекта")
    add_material(grid)

    one = actions.add_parser("pair", parents=[common],
                             help="названные пары «заголовок × текст»")
    add_material(one)
    one.add_argument("--pair", action="append", required=True,
                     type=pair_argument, metavar="NxM",
                     help="пара: место заголовка и место текста; повторяется")

    actions.add_parser("placements", parents=[common],
                       help="какие места показа умеет рисовать превью")
    return parser


def run(args) -> int:
    return {"matrix": run_matrix, "pair": run_pair,
            "placements": run_placements}[args.action](args)


def main(argv=None) -> int:
    preload_secrets()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action != "placements" and not any(
            (args.brief, args.title, args.text, args.ad, args.from_json)):
        parser.error("назовите --ad ID, --from-json ФАЙЛ, --brief либо --title с --text")
    if args.action != "placements":
        if args.ad is not None and args.ad < 1:
            parser.error("--ad: нужен положительный ID объявления")
        if args.brief and (args.ad is not None or args.from_json):
            parser.error("--brief нельзя совмещать с --ad или --from-json")
        if args.image and args.image_url:
            parser.error("используйте --image либо --image-url")
    try:
        return run(args)
    except DirectFailure as failure:
        warn(str(failure))
        return 1
    except OSError as failure:
        warn(redact(f"Не удалось прочитать или записать файл: {failure}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
