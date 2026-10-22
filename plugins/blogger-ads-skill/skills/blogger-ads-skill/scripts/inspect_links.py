#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7", "pymupdf>=1.26,<2"]
# ///
"""Показать фактические адреса гиперссылок PDF/DOCX без открытия адресов."""
import argparse
from collections import Counter
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

_LINK_SPEC = importlib.util.spec_from_file_location("creative_links", Path(__file__).with_name("creative_links.py"))
creative_links = importlib.util.module_from_spec(_LINK_SPEC)
_LINK_SPEC.loader.exec_module(creative_links)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
STRICT_NAMESPACES = {
    "http://purl.oclc.org/ooxml/wordprocessingml/main": W[1:-1],
    "http://purl.oclc.org/ooxml/drawingml/main": A[1:-1],
    "http://purl.oclc.org/ooxml/officeDocument/relationships": R[1:-1],
    "http://purl.oclc.org/ooxml/package/relationships": REL[1:-1],
}
COMPARISON_NOTE = (
    "Различия — повод проверить якоря, а не автоматическое доказательство ошибки: "
    "печатная таблица, переносы и некликабельный видимый URL могут менять число аннотаций. "
    "Порядок, параметры адресов и повторы не нормализованы."
)


def expected_urls(path):
    link_map = creative_links.parse_links(json.loads(Path(path).read_text(encoding="utf-8")))
    return [row["url"] for row in link_map]


def pdf_links(raw):
    reader = PdfReader(BytesIO(raw))
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError("PDF защищён паролем: адреса не прочитаны")
    links, warnings = [], []
    catalog = reader.root_object
    for key in ("/OpenAction", "/AA", "/Outlines", "/AcroForm"):
        if key in catalog:
            warnings.append(f"Каталог PDF содержит {key}; действия не разобраны. "
                            "Сверьте их отдельно")
    names = catalog.get("/Names", {})
    if hasattr(names, "get_object"):
        names = names.get_object()
    if isinstance(names, dict) and "/JavaScript" in names:
        warnings.append("Каталог PDF содержит /Names/JavaScript; действия не разобраны. "
                        "Сверьте их отдельно")
    for page_index, page in enumerate(reader.pages, 1):
        if "/AA" in page:
            warnings.append(f"Страница {page_index}: дополнительные действия /AA не разобраны; "
                            "сверьте их отдельно")
        annotations = page.get("/Annots", [])
        if hasattr(annotations, "get_object"):
            annotations = annotations.get_object()
        if not isinstance(annotations, list):
            warnings.append(f"Страница {page_index}: /Annots не является массивом; аннотации не прочитаны")
            continue
        for annotation_index, reference in enumerate(annotations, 1):
            location = f"Страница {page_index}, аннотация {annotation_index}"
            try:
                annotation = reference.get_object()
            except (AttributeError, PdfReadError) as error:
                warnings.append(f"{location}: не удалось прочитать аннотацию: {error}")
                continue
            if not isinstance(annotation, dict):
                warnings.append(f"{location}: аннотация не является словарём")
                continue
            if "/AA" in annotation:
                warnings.append(f"{location}: дополнительные действия аннотации /AA не разобраны; "
                                "сверьте их отдельно")
            if annotation.get("/Subtype") != "/Link":
                if "/A" in annotation:
                    warnings.append(f"{location}: действие /A аннотации {annotation.get('/Subtype')} не разобрано; "
                                    "сверьте его отдельно")
                continue
            action = annotation.get("/A")
            if action is None:
                if "/Dest" not in annotation:
                    warnings.append(f"{location}: ссылка не содержит /A или /Dest")
                continue
            action = action.get_object()
            if not isinstance(action, dict):
                warnings.append(f"{location}: /A не является словарём")
                continue
            if "/Next" in action:
                warnings.append(f"{location}: цепочка действий /Next не разобрана; сверьте её отдельно")
            if "/S" not in action:
                warnings.append(f"{location}: у действия нет типа /S")
                continue
            if action.get("/S") != "/URI":
                warnings.append(f"{location}: неподдерживаемое действие {action.get('/S')}; "
                                "сверьте переход отдельно")
                continue
            if "/URI" not in action:
                warnings.append(f"{location}: действие /URI не содержит адреса")
                continue
            target = action["/URI"]
            rect = annotation.get("/Rect")
            try:
                if rect is None or len(rect) != 4:
                    raise ValueError("ожидались четыре координаты")
                rect = [float(value) for value in rect]
            except (TypeError, ValueError) as error:
                warnings.append(f"{location}: некорректный /Rect: {error}")
                rect = None
            row = {"order": len(links) + 1, "page": page_index,
                   "annotation_index": annotation_index,
                   "xref": getattr(reference, "idnum", None),
                   "rect": rect,
                   "target": target if isinstance(target, str) else None}
            if not isinstance(target, str):
                if isinstance(target, bytes):
                    row["uri_bytes_hex"] = target.hex()
                warnings.append(f"{location}: URI не удалось прочитать как строку")
            links.append(row)
    pdf_anchor_hints(raw, links, warnings)
    return links, warnings


def pdf_anchor_hints(raw, links, warnings):
    """Текст из области ссылки — подсказка для сверки, не распознавание изображения."""
    import pymupdf

    try:
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            by_page = {}
            for row in links:
                page_number = row["page"] - 1
                if page_number not in by_page:
                    page = document[page_number]
                    by_page[page_number] = (page, {link.get("xref"): link for link in page.get_links()})
                page, annotations = by_page[page_number]
                annotation = annotations.get(row.get("xref")) if row.get("xref") else None
                row["anchor_hint"] = None
                if annotation and annotation.get("uri") == row["target"]:
                    rectangle = annotation["from"] * page.derotation_matrix
                    row["anchor_hint"] = page.get_textbox(rectangle) or None
    except (RuntimeError, ValueError) as error:
        warnings.append(f"Текст областей ссылок PDF не извлечён: {error}; сверьте страницы визуально")


def visible_text(element):
    """Текст якоря в порядке XML, с явными табуляциями и переносами."""
    fragments = []
    for node in element.iter():
        if node.tag == W + "t":
            fragments.append(node.text or "")
        elif node.tag == W + "tab":
            fragments.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            fragments.append("\n")
    return "".join(fragments)


def paragraph_links(root):
    """Каждая ссылка один раз в порядке XML, с ближайшим абзацем."""
    paragraph_count = 0
    hyperlink_counts = Counter()

    def walk(element, paragraph_index=None):
        nonlocal paragraph_count
        if element.tag == W + "p":
            paragraph_count += 1
            paragraph_index = paragraph_count
        if element.tag == W + "hyperlink" and paragraph_index is not None:
            hyperlink_counts[paragraph_index] += 1
            yield paragraph_index, hyperlink_counts[paragraph_index], element
        for child in element:
            yield from walk(child, paragraph_index)

    yield from walk(root)


def has_hyperlink_field(root):
    """Склеить части команды, сохраняя границы отдельных полей Word."""
    command = re.compile(r"\s*HYPERLINK\b", re.IGNORECASE)
    fragments = []
    for node in root.iter():
        if node.tag in (W + "fldSimple", W + "fldChar"):
            if command.match("".join(fragments)):
                return True
            fragments.clear()
            if node.tag == W + "fldSimple" and command.match(node.get(W + "instr", "")):
                return True
        elif node.tag == W + "instrText":
            fragments.append(node.text or "")
    return command.match("".join(fragments)) is not None


def ooxml_root(raw):
    """Свести Strict и Transitional имена к одному виду, сохранив текст и URL."""
    def name(value):
        if value.startswith("{"):
            namespace, local = value[1:].split("}", 1)
            return "{" + STRICT_NAMESPACES.get(namespace, namespace) + "}" + local
        return value

    root = ET.fromstring(raw)
    for node in root.iter():
        node.tag = name(node.tag)
        attributes = {name(key): value for key, value in node.attrib.items()}
        if len(attributes) != len(node.attrib):
            raise ValueError("В DOCX неоднозначные атрибуты Strict и Transitional одного элемента")
        node.attrib = attributes
        if node.tag == REL + "Relationship":
            strict_prefix = "http://purl.oclc.org/ooxml/officeDocument/relationships/"
            relation_type = node.get("Type", "")
            if relation_type.startswith(strict_prefix):
                node.set("Type", R[1:-1] + "/" + relation_type[len(strict_prefix):])
    return root


def docx_links(raw):
    links, warnings = [], []
    with ZipFile(BytesIO(raw)) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("В DOCX отсутствует word/document.xml")
        # Импорт может находиться вне XML-частей Word и не иметь целого w:altChunk.
        for rel_path in sorted(name for name in names if name.endswith(".rels")):
            for relationship in ooxml_root(archive.read(rel_path)).findall(REL + "Relationship"):
                if relationship.get("Type", "").rsplit("/", 1)[-1] == "aFChunk":
                    warnings.append(f"{rel_path}: импорт aFChunk {relationship.get('Id')} "
                                    f"(Target={relationship.get('Target')}, TargetMode={relationship.get('TargetMode', 'Internal')}); "
                                    "содержимое и ссылки импортируемого файла не разобраны. "
                                    "Сверьте их отдельно")
        # Тело первым; остальные части (например, колонтитулы) — по имени.
        parts = sorted(name for name in names if name.startswith("word/") and name.endswith((".xml", ".vml")))
        parts.remove("word/document.xml")
        for part in ["word/document.xml"] + parts:
            root = ooxml_root(archive.read(part))
            if any(node.tag == W + "altChunk" for node in root.iter()):
                warnings.append(f"{part}: обнаружен w:altChunk; содержимое и ссылки импортируемого файла не разобраны. "
                                "Сверьте их отдельно")
            if has_hyperlink_field(root):
                warnings.append(f"{part}: обнаружено поле HYPERLINK; его адрес и якорь не извлечены. "
                                "Сверьте их вручную")
            if any(node.tag in (A + "hlinkClick", A + "hlinkHover") for node in root.iter()):
                warnings.append(f"{part}: обнаружены ссылки DrawingML (hlinkClick/hlinkHover); их адреса и объекты не извлечены. "
                                "Сверьте их вручную")
            if any(node.tag.startswith(V) and any(key.rsplit("}", 1)[-1] == "href" for key in node.attrib)
                   for node in root.iter()):
                warnings.append(f"{part}: обнаружены ссылки VML (href); их адреса и объекты не извлечены. "
                                "Сверьте их вручную")
            rel_path = str(Path(part).parent / "_rels" / (Path(part).name + ".rels"))
            relationships = {}
            if rel_path in names:
                relationships = {node.get("Id"): node for node in ooxml_root(archive.read(rel_path)).findall(REL + "Relationship")}
            for paragraph_index, hyperlink_index, link in paragraph_links(root):
                relationship_id = link.get(R + "id")
                relationship = relationships.get(relationship_id)
                target = relationship.get("Target") if relationship is not None else None
                if relationship is not None and (
                        relationship.get("Type") != "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
                        or relationship.get("TargetMode") != "External"):
                    target = None
                    warnings.append(f"{part}, абзац {paragraph_index}: отношение {relationship_id} не является внешней гиперссылкой; "
                                    "нужны Type гиперссылки и TargetMode=External, адрес не учтён")
                row = {"order": len(links) + 1, "part": part,
                       "paragraph_index": paragraph_index, "hyperlink_index": hyperlink_index,
                       "anchor": visible_text(link), "target": target,
                       "relationship_id": relationship_id,
                       "target_mode": relationship.get("TargetMode") if relationship is not None else None}
                if link.get(W + "anchor") is not None:
                    row["internal_anchor"] = link.get(W + "anchor")
                if relationship_id and relationship is None:
                    warnings.append(f"{part}, абзац {paragraph_index}: не найден адрес отношения {relationship_id}")
                links.append(row)
    return links, warnings


def compare_urls(expected, actual):
    """Сравнить точные адреса с сохранением порядка и повторов."""
    wanted_counts, actual_counts = Counter(expected), Counter(actual)
    urls = dict.fromkeys(expected + actual)
    counts = [{"url": url, "expected": wanted_counts[url], "actual": actual_counts[url],
               "difference": actual_counts[url] - wanted_counts[url]} for url in urls]
    return {"expected_urls": expected, "actual_urls": actual,
            "expected_count": len(expected), "actual_count": len(actual),
            "counts": counts, "differences": [row for row in counts if row["difference"]],
            "ordered_lists_equal": expected == actual, "note": COMPARISON_NOTE}


def inspect_links(path, expected=None):
    path = Path(path).resolve()
    raw = path.read_bytes()
    kind = path.suffix.lower().lstrip(".")
    if kind not in ("pdf", "docx"):
        raise ValueError("Поддерживаются только PDF и DOCX")
    links, warnings = pdf_links(raw) if kind == "pdf" else docx_links(raw)
    result = {"kind": "link_inspection", "source": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
              "format": kind, "links": links, "warnings": warnings,
              "limitations": [COMPARISON_NOTE,
                  "PDF: извлечены /Link с действием /URI; прочие действия, /A у других типов аннотаций, цепочки /Next, /AA аннотаций и страниц, /OpenAction, /AA, /Outlines, /AcroForm и /Names/JavaScript каталога вызывают предупреждение. Закладки и поля форм не разбираются. anchor_hint — текст из области той же аннотации, он может захватить соседнюю строку или отсутствовать у скана; видимый якорь сверяйте по странице и прямоугольнику. Это не OCR и не автоматическое подтверждение соответствия якоря адресу.",
                  "DOCX: поддержаны пространства имён Strict и Transitional. Внешние адреса w:hyperlink учтены только при Type гиперссылки и TargetMode=External; внутренние w:anchor показаны отдельно. Обычный текст URL не извлекается; поля HYPERLINK, ссылки DrawingML (hlinkClick/hlinkHover), VML (href) и импорт w:altChunk/aFChunk вызывают предупреждение и требуют отдельной сверки адресов и связанных текста или объектов. Импортируемые HTML/MHTML и другие форматы не разбираются и не загружаются. Номера абзацев относятся к каждой XML-части отдельно.",
                  "Адреса не открывались. Для DOCX порядок между XML-частями не определяет порядок страниц."]}
    if expected is not None:
        actual = [row["target"] for row in links if row["target"] is not None]
        result["comparison"] = compare_urls(expected, actual)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Исходный PDF или DOCX")
    parser.add_argument("--expected", type=Path, help="Исходная JSON-карта ссылок в формате import-creative --links")
    parser.add_argument("--output", type=Path, help="Новый JSON-файл; без параметра выводится в stdout")
    args = parser.parse_args()
    try:
        expected = expected_urls(args.expected) if args.expected else None
        result = inspect_links(args.input, expected)
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as file:
                file.write(text)
        else:
            print(text, end="")
    except (ValueError, OSError, ET.ParseError, BadZipFile, PdfReadError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
