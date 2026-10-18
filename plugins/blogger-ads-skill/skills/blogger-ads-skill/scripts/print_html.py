#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Печать локального HTML в PDF через Chrome/Chromium с отдельным временным профилем."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def find_browser(explicit=None):
    chosen = explicit or os.environ.get("CHROME_BIN")
    if chosen:
        path = Path(chosen).expanduser()
        if not path.is_file():
            raise ValueError("Не найден браузер из --browser / CHROME_BIN; укажите исполняемый файл Chrome или Chromium")
        return path.resolve()
    candidates = [shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge")]
    candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for base in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        if os.environ.get(base):
            candidates += [str(Path(os.environ[base]) / suffix) for suffix in
                           ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise ValueError("Chrome/Chromium не найден. Укажите --browser /путь/к/браузеру или откройте исходный HTML и сохраните PDF через печать браузера. Не пересобирайте сокращённую форму другим генератором")


def print_pdf(source, output, browser=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    provenance = output.with_suffix(output.suffix + ".source.json")
    if source.suffix.lower() not in (".html", ".htm") or not source.is_file():
        raise ValueError("Нужен существующий локальный файл HTML")
    if output.suffix.lower() != ".pdf":
        raise ValueError("Выходной файл должен иметь расширение .pdf")
    if output.exists() or provenance.exists():
        raise FileExistsError(f"PDF или протокол уже существует: {output}; выберите новую версию")
    executable = find_browser(browser)
    original = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix="blogger-ads-print-") as temporary:
        directory = Path(temporary)
        printed = directory / "printed.pdf"
        command = [str(executable), "--headless=new", "--disable-gpu", "--no-first-run",
                   "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                   "--disable-background-networking", "--no-pdf-header-footer",
                   "--run-all-compositor-stages-before-draw", "--virtual-time-budget=1000",
                   f"--user-data-dir={directory / 'profile'}", f"--print-to-pdf={printed}", source.as_uri()]
        try:
            result = subprocess.run(command, capture_output=True, timeout=60, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Браузер не завершил печать за 60 секунд; откройте HTML и проверьте печать вручную") from exc
        if result.returncode or not printed.is_file():
            detail = result.stderr.decode("utf-8", errors="replace")[-1200:]
            raise ValueError(f"Браузер не создал PDF (код {result.returncode}). {detail}")
        payload = printed.read_bytes()
        if not payload.startswith(b"%PDF-"):
            raise ValueError("Браузер создал файл, который не является PDF")
        if source.read_bytes() != original:
            raise ValueError("HTML изменился во время печати; повторите с сохранённой версией")
    record = {"source_html": str(source), "source_sha256": sha256(original).hexdigest(),
              "pdf_sha256": sha256(payload).hexdigest(), "browser": str(executable),
              "created_at": datetime.now(timezone.utc).isoformat(),
              "method": "browser-print-original-html", "visual_review": "required"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(payload)
    with provenance.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Исходный HTML счёта или УПД")
    parser.add_argument("--output", type=Path, required=True, help="Новый PDF")
    parser.add_argument("--browser", help="Путь к исполняемому файлу Chrome/Chromium; иначе поиск в системе")
    args = parser.parse_args()
    try:
        print(print_pdf(args.input, args.output, args.browser))
        print("Напечатан исходный HTML. Просмотрите каждую страницу; протокол происхождения — .pdf.source.json")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()
