#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Extract PDF/EPUB/TXT/Markdown into a resumable local job directory."""

from __future__ import annotations

import argparse
import hashlib
import html
import html.parser
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

WORDS_PER_TOKEN = 0.75


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value, flags=re.IGNORECASE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:72] or "source"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) / WORDS_PER_TOKEN)


def run_text_command(cmd: list[str], timeout: int = 180) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    return None


def detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".epub":
        return "epub"
    if ext in {".txt", ".text"}:
        return "text"
    if ext in {".md", ".markdown"}:
        return "markdown"

    with path.open("rb") as f:
        header = f.read(58)
    if header.startswith(b"%PDF"):
        return "pdf"
    if header.startswith(b"PK"):
        return "epub" if looks_like_epub(path) else "zip"
    try:
        header.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def looks_like_epub(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                mt = zf.read("mimetype").decode("ascii", errors="ignore").strip()
                if mt == "application/epub+zip":
                    return True
            return "META-INF/container.xml" in names
    except Exception:
        return False


def extract_pdf(path: Path, mode: str, script_dir: Path) -> tuple[str, str, list[str]]:
    warnings: list[str] = []

    if mode == "technical":
        text = extract_with_docling(path, script_dir)
        if text:
            return text, "docling", warnings
        warnings.append("docling unavailable; fell back to text extraction")

    if shutil.which("pdftotext"):
        text = run_text_command(["pdftotext", "-layout", str(path), "-"])
        if text:
            return text, "pdftotext", warnings

    text = run_uv_helper(script_dir / "extract_pypdf.py", str(path), timeout=180)
    if text:
        return text, "PyPDF2", warnings

    text = run_uv_helper(script_dir / "extract_pdfminer.py", str(path), timeout=180)
    if text:
        return text, "pdfminer.six", warnings

    raise SystemExit(
        "Could not extract PDF text. Install poppler pdftotext, PyPDF2, pdfminer.six, or docling."
    )


def run_uv_helper(helper: Path, *args: str, timeout: int) -> str | None:
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        return None
    text = run_text_command([uv, "run", "--script", str(helper), *args], timeout=timeout)
    return text if text and text.strip() else None


def extract_with_docling(path: Path, script_dir: Path) -> str | None:
    return run_uv_helper(script_dir / "extract_docling.py", str(path), timeout=60 * 30)


class HTMLTextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "head", "svg"}
    BLOCK = {"p", "br", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.parts))
        lines = [normalize_ws(line) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def normalize_ws(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u202f", " ")
    return re.sub(r"[ \t]+", " ", value).strip()


def extract_epub(path: Path) -> tuple[str, str, list[str], dict[str, Any]]:
    warnings: list[str] = []
    text, epub_info = extract_epub_stdlib(path)
    if text.strip():
        return text, "stdlib-epub", warnings, epub_info
    raise SystemExit("Could not extract EPUB text.")


def extract_epub_stdlib(path: Path) -> tuple[str, dict[str, Any]]:
    with zipfile.ZipFile(path) as zf:
        rootfile = epub_rootfile(zf)
        opf_dir = posixpath.dirname(rootfile)
        opf = ElementTree.fromstring(zf.read(rootfile))
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        manifest: dict[str, str] = {}
        ncx_path = ""
        nav_path = ""
        for item in opf.findall(".//opf:manifest/opf:item", ns):
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            media = item.attrib.get("media-type", "")
            properties = item.attrib.get("properties", "")
            if item_id and href and ("html" in media or href.endswith((".html", ".xhtml"))):
                manifest[item_id] = posixpath.normpath(posixpath.join(opf_dir, href))
            if href and (media == "application/x-dtbncx+xml" or href.endswith(".ncx")):
                ncx_path = posixpath.normpath(posixpath.join(opf_dir, href))
            if href and "nav" in properties.split():
                nav_path = posixpath.normpath(posixpath.join(opf_dir, href))

        order = []
        for itemref in opf.findall(".//opf:spine/opf:itemref", ns):
            href = manifest.get(itemref.attrib.get("idref", ""))
            if href:
                order.append(href)
        if not order:
            order = sorted(n for n in zf.namelist() if n.endswith((".html", ".xhtml")))

        parts = []
        spine_items = []
        char_pos = 0
        for name in order:
            try:
                raw = zf.read(name).decode("utf-8", errors="replace")
            except KeyError:
                continue
            parser = HTMLTextExtractor()
            parser.feed(raw)
            text = parser.text()
            if text.strip():
                if parts:
                    char_pos += 2
                parts.append(text)
                spine_items.append(
                    {
                        "href": name,
                        "char_start": char_pos,
                        "char_end": char_pos + len(text),
                        "words": len(text.split()),
                    }
                )
                char_pos += len(text)
        full_text = "\n\n".join(parts)
        epub_info = {
            "epub_rootfile": rootfile,
            "epub_title": opf_text_value(opf, "title"),
            "epub_author": opf_text_value(opf, "creator"),
            "epub_spine_items": spine_items,
            "epub_toc_source": "ncx" if ncx_path else ("nav" if nav_path else ""),
            "epub_toc": parse_ncx_toc(zf, ncx_path) if ncx_path else parse_nav_toc(zf, nav_path),
        }
        return full_text, epub_info


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def opf_text_value(root: ElementTree.Element, wanted: str) -> str:
    for elem in root.iter():
        if local_name(elem.tag) == wanted and elem.text:
            return normalize_ws(elem.text)
    return ""


def parse_ncx_toc(zf: zipfile.ZipFile, ncx_path: str) -> list[dict[str, Any]]:
    if not ncx_path:
        return []
    try:
        root = ElementTree.fromstring(zf.read(ncx_path))
    except Exception:
        return []
    items: list[dict[str, Any]] = []

    def walk(elem: ElementTree.Element, level: int) -> None:
        if local_name(elem.tag) == "navpoint":
            label = ""
            src = ""
            for child in elem.iter():
                name = local_name(child.tag)
                if name == "text" and child.text and not label:
                    label = normalize_ws(child.text)
                elif name == "content" and child.attrib.get("src") and not src:
                    src = child.attrib["src"]
            if label:
                items.append(
                    {
                        "label": label,
                        "href": src,
                        "level": level,
                        "order": len(items) + 1,
                    }
                )
            for child in elem:
                if local_name(child.tag) == "navpoint":
                    walk(child, level + 1)
        else:
            for child in elem:
                walk(child, level)

    walk(root, 1)
    return items


def parse_nav_toc(zf: zipfile.ZipFile, nav_path: str) -> list[dict[str, Any]]:
    if not nav_path:
        return []
    try:
        raw = zf.read(nav_path).decode("utf-8", errors="replace")
    except Exception:
        return []
    links = re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", raw, flags=re.I | re.S)
    items = []
    for href, label_html in links:
        parser = HTMLTextExtractor()
        parser.feed(label_html)
        label = normalize_ws(parser.text())
        if label:
            items.append({"label": label, "href": href, "level": 1, "order": len(items) + 1})
    return items


def epub_rootfile(zf: zipfile.ZipFile) -> str:
    if "META-INF/container.xml" not in zf.namelist():
        raise ValueError("EPUB container.xml not found")
    root = ElementTree.fromstring(zf.read("META-INF/container.xml"))
    for elem in root.iter():
        if elem.tag.endswith("rootfile") and elem.attrib.get("full-path"):
            return elem.attrib["full-path"]
    raise ValueError("EPUB rootfile not found")


def count_pdf_pages(path: Path) -> int:
    if shutil.which("pdfinfo"):
        text = run_text_command(["pdfinfo", str(path)], timeout=30)
        if text:
            for line in text.splitlines():
                if line.startswith("Pages:"):
                    try:
                        return int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
    helper = Path(__file__).resolve().parent / "extract_pypdf.py"
    text = run_uv_helper(helper, "--count-pages", str(path), timeout=60)
    if text:
        try:
            return int(text.strip())
        except ValueError:
            return 0
    return 0


def count_epub_spine(path: Path) -> int:
    try:
        with zipfile.ZipFile(path) as zf:
            rootfile = epub_rootfile(zf)
            opf = ElementTree.fromstring(zf.read(rootfile))
            return sum(1 for elem in opf.iter() if elem.tag.endswith("itemref"))
    except Exception:
        return 0


def detect_structure(text: str) -> dict[str, Any]:
    head = text[:80000]
    lines = head.splitlines()
    chapter_re = re.compile(
        r"^\s*((глава|chapter|часть|part)\s+([0-9ivxlcdm]+|[а-яёa-z]+)|[0-9]{1,2}(\.[0-9]{1,2})?\s+[^.]{3,120})\s*$",
        re.IGNORECASE,
    )
    headings = []
    for line in lines:
        stripped = line.strip()
        if 3 <= len(stripped) <= 140 and chapter_re.match(stripped):
            headings.append(stripped)
    toc_words = ["оглавление", "содержание", "table of contents", "contents"]
    return {
        "has_toc": any(word in head.lower() for word in toc_words),
        "headings_detected": len(headings),
        "headings_sample": headings[:20],
    }


def make_job_id(path: Path, explicit: str | None) -> str:
    if explicit:
        return slugify(explicit)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{slugify(path.stem)}-{stamp}-{os.getpid()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--mode", default="auto", choices=["auto", "text", "technical"])
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--job-id")
    args = parser.parse_args()

    source = Path(args.input).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise SystemExit(f"Source file not found: {source}")

    source_format = detect_format(source)
    if source_format == "unknown" or source_format == "zip":
        raise SystemExit(f"Unsupported source format: {source_format}")

    warnings: list[str] = []
    script_dir = Path(__file__).resolve().parent
    if source_format == "pdf":
        text, method, warnings = extract_pdf(source, args.mode, script_dir)
        units = {"pages": count_pdf_pages(source)}
    elif source_format == "epub":
        text, method, warnings, epub_info = extract_epub(source)
        units = {"spine_items": count_epub_spine(source)}
    else:
        text = source.read_text(encoding="utf-8", errors="replace")
        method = "plain-text"
        units = {"pages": 0}

    job_id = make_job_id(source, args.job_id)
    job_dir = Path(args.cache_dir).expanduser().resolve() / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    output_text = job_dir / "source.txt"
    output_meta = job_dir / "metadata.json"
    output_text.write_text(text, encoding="utf-8")

    source_hash = sha256_file(source)
    metadata: dict[str, Any] = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_file": str(source),
        "source_sha256": f"sha256:{source_hash}",
        "filename": source.name,
        "format": source_format,
        "extraction_mode": args.mode,
        "extraction_method": method,
        "output_text": str(output_text),
        "file_size_mb": round(source.stat().st_size / (1024 * 1024), 3),
        "chars": len(text),
        "words": len(text.split()),
        "estimated_tokens": estimate_tokens(text),
        "warnings": warnings,
        **units,
        **detect_structure(text),
    }
    if source_format == "epub":
        metadata.update(epub_info)
    output_meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Job: {job_id}")
    print(f"Format: {source_format}")
    print(f"Method: {method}")
    print(f"Words: {metadata['words']}")
    if metadata.get("epub_title"):
        print(f"Title: {metadata['epub_title']}")
    if metadata.get("epub_author"):
        print(f"Author: {metadata['epub_author']}")
    if metadata.get("epub_toc"):
        print(f"EPUB TOC: {len(metadata['epub_toc'])} items ({metadata.get('epub_toc_source')})")
    print(f"Text: {output_text}")
    print(f"Meta: {output_meta}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
