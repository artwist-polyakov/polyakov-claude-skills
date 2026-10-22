#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7", "pymupdf>=1.26,<2"]
# ///
"""Отрисовка DOCX в PDF и PNG с контролем сохранности файлов."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


KIND = 'docx_layout'
DPI = 144


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve(base, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('В manifest нужен непустой путь файла')
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def read_manifest(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('kind') != KIND or value.get('format_version') != 1:
        raise ValueError('Нужен manifest.json от render_docx.py формата 1')
    return value


def source_reference(manifest_path, value):
    source = value.get('source')
    if not isinstance(source, dict) or not isinstance(source.get('sha256'), str):
        raise ValueError('В manifest нужны source.path и source.sha256')
    return resolve(Path(manifest_path).parent, source.get('path')), source['sha256']


def checked_file(base, item, label):
    if not isinstance(item, dict):
        raise ValueError(f'{label}: нужны path и sha256')
    path = resolve(base, item.get('path'))
    if not path.is_file() or sha(path) != item.get('sha256'):
        raise ValueError(f'{label}: файл отсутствует или изменён после отрисовки: {path}. Нужна новая отрисовка и просмотр')
    return path


def check(manifest_path):
    """Проверить сохранённые файлы и полноту набора страниц."""
    manifest_path = Path(manifest_path).resolve()
    value = read_manifest(manifest_path)
    base = manifest_path.parent
    source = checked_file(base, value.get('source'), 'DOCX')
    if source.suffix.lower() != '.docx':
        raise ValueError('Исходник должен быть DOCX')
    pdf = checked_file(base, value.get('pdf'), 'PDF')
    from pypdf import PdfReader  # Импорт handoff без проверки вёрстки не требует PDF-библиотек.
    try:
        count = len(PdfReader(pdf).pages)
    except Exception as error:
        raise ValueError(f'Не удалось прочитать PDF: {pdf}: {error}') from error
    pages = value.get('pages')
    if (count < 1 or isinstance(value.get('page_count'), bool) or value.get('page_count') != count
            or not isinstance(pages, list) or len(pages) != count):
        raise ValueError('Неполный набор страниц: page_count и pages должны совпадать с числом страниц PDF')
    seen = {source, pdf, manifest_path}
    for number, item in enumerate(pages, 1):
        if not isinstance(item, dict) or type(item.get('number')) is not int or item['number'] != number:
            raise ValueError('Номера страниц должны идти ровно от 1 до числа страниц PDF, без повторов и пропусков')
        path = checked_file(base, item, f'PNG страницы {number}')
        if path in seen or path.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError(f'Страница {number}: нужен отдельный PNG-файл')
        seen.add(path)
    return value


def find_soffice(soffice=None):
    executable = soffice or shutil.which('soffice') or shutil.which('libreoffice')
    if not executable:
        raise ValueError('LibreOffice не найден: укажите --soffice /путь/к/soffice или добавьте soffice/libreoffice в PATH')
    executable = str(Path(executable).expanduser().resolve())
    if not Path(executable).is_file():
        raise ValueError(f'Не найден исполняемый файл LibreOffice: {executable}')
    return executable


def convert_to_pdf(source, destination, soffice=None):
    """Создать PDF через LibreOffice."""
    executable = find_soffice(soffice)
    with tempfile.TemporaryDirectory(prefix='docx-layout-lo-') as profile:
        command = [executable, '-env:UserInstallation=' + Path(profile).as_uri(),
                   '--headless', '--convert-to', 'pdf:writer_pdf_Export',
                   '--outdir', str(destination), str(source)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.SubprocessError as error:
            raise ValueError(f'Не удалось отрисовать DOCX через LibreOffice: {error}') from error
    pdf = destination / (source.stem + '.pdf')
    if result.returncode or not pdf.is_file():
        raise ValueError(f'LibreOffice не создал PDF: {result.stderr.strip() or result.stdout.strip()}. Для повтора выберите новую папку')
    return pdf


def page_pixels(page):
    import pymupdf
    return page.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB, alpha=False)


def render(input_path, output_dir, soffice=None):
    source = Path(input_path).resolve()
    destination = Path(output_dir).resolve()
    if source.suffix.lower() != '.docx' or not source.is_file():
        raise ValueError('Нужен существующий исходный DOCX')
    if destination.exists():
        raise ValueError('Папка отрисовки уже существует: выберите новую; старые файлы не перезаписываются')
    executable = find_soffice(soffice)
    import pymupdf
    digest = sha(source)
    destination.mkdir(parents=True, exist_ok=False)
    pdf = convert_to_pdf(source, destination, executable)
    pages = []
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, 1):
            path = destination / f'page-{number:03d}.png'
            page_pixels(page).save(path)
            pages.append({'number': number, 'path': path.name, 'sha256': sha(path)})
    if not pages or sha(source) != digest:
        raise ValueError('DOCX изменён во время отрисовки либо PDF пуст. Повторите в новой папке')
    value = {'kind': KIND, 'format_version': 1,
             'source': {'path': Path(os.path.relpath(source, destination)).as_posix(), 'sha256': digest},
             'pdf': {'path': pdf.name, 'sha256': sha(pdf)}, 'page_count': len(pages), 'pages': pages,
             'render': {'engine': 'LibreOffice', 'executable': executable, 'dpi': DPI}}
    manifest = destination / 'manifest.json'
    with manifest.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    check(manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('render')
    command.add_argument('--input', required=True)
    command.add_argument('--output-dir', required=True)
    command.add_argument('--soffice', help='Явный путь к исполняемому файлу LibreOffice; иначе поиск в PATH')
    command = commands.add_parser('check')
    command.add_argument('--manifest', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'render':
            manifest = render(args.input, args.output_dir, args.soffice)
            value = {'manifest': str(manifest), 'page_count': read_manifest(manifest)['page_count']}
        else:
            value = {'valid': True, 'source': check(args.manifest)['source']}
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f'Ошибка: {error}\n')
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
