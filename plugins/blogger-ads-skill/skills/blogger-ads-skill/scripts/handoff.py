#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7", "pymupdf>=1.26,<2", "markdown-it-py>=3,<5"]
# ///
"""Учёт версий и техническая проверка выдаваемых файлов. Без сети."""
import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import deal


DOCUMENT_KINDS = {'contract', 'placement_terms', 'creative', 'amendment', 'payment', 'other'}
CONTRACT_KINDS = {'contract', 'placement_terms', 'creative', 'amendment'}


def document_classification(kind, reason, required=False):
    if kind is None:
        if required or reason is not None:
            raise ValueError('PDF/DOCX требует document_kind: contract, placement_terms, creative, amendment, payment или other')
        return
    if kind not in DOCUMENT_KINDS:
        raise ValueError('document_kind: выберите contract, placement_terms, creative, amendment, payment или other')
    if kind == 'other':
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('document_kind=other требует конкретного kind_reason, почему файл не является договорным или платёжным')
    elif reason is not None:
        raise ValueError('kind_reason допустим только для document_kind=other')


def received_status(status):
    value = status.strip().lower() if isinstance(status, str) else ''
    return value == 'received' or value.startswith('received_')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(base, value):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def relative(base, path):
    return Path(os.path.relpath(path, base)).as_posix()


def archive_payload(base, payload):
    """Локальная копия точных байтов; одинаковое содержимое хранится один раз."""
    digest = hashlib.sha256(payload).hexdigest()
    path = base / '.versions' / digest
    if path.exists():
        if not path.is_file() or sha(path) != digest:
            raise ValueError(f'Архив повреждён: {path}. Существующую копию нельзя перезаписать')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(payload)
    return relative(base, path)


def archive_file(base, item):
    value = item.get('archive_path')
    if not isinstance(value, str) or not value:
        raise ValueError('Нет точной архивной копии. Если зарегистрированный файл сохранился неизменным, '
                         'повторите register с прежними параметрами; не создавайте старую версию пересказом')
    path = resolve(base, value)
    if not path.is_file() or sha(path) != item.get('sha256'):
        raise ValueError(f'Архив отсутствует или повреждён: {path}. Восстановление из него запрещено')
    return path


def restore_hint(state_path, path, item, archive):
    if received_status(item.get('status')):
        return f'Полученный оригинал не изменяется автоматически; точные зарегистрированные байты сохранены в {archive}'
    command = ['uv', 'run', 'scripts/handoff.py', 'restore', '--state', str(state_path), '--file', str(path)]
    if path.exists():
        command += ['--save-current', str(path.with_name(path.stem + '-before-restore' + path.suffix))]
    return 'Восстановите точную зарегистрированную версию: ' + shlex.join(command) + '. Для --save-current нужен новый, ещё не существующий файл'


def restore(state_path, filename, save_current=None):
    """Сначала сохранить правки отдельно, затем вернуть архив; реестр не меняется."""
    state_path = Path(state_path).resolve()
    path = Path(filename).resolve()
    base = state_path.parent
    state = deal.read_json(state_path)
    matches = [item for item in state.get('documents', []) if resolve(base, item['path']) == path]
    if len(matches) != 1:
        raise ValueError('restore: нужен однозначно зарегистрированный файл')
    item = matches[0]
    if received_status(item.get('status')):
        raise ValueError('restore не изменяет полученные оригиналы received; точная архивная копия сохраняется отдельно')
    archive = archive_file(base, item)
    payload = archive.read_bytes()
    current = path.read_bytes() if path.exists() else None
    if current == payload:
        return {'restored': False, 'path': str(path), 'note': 'Файл уже совпадает с зарегистрированной версией; ничего не изменено'}
    saved = None
    if current is not None:
        if save_current is None:
            raise ValueError('Изменённый файл сначала нужно сохранить: укажите --save-current НОВЫЙ_ПУТЬ')
        saved = Path(save_current).resolve()
        if saved.exists() or saved in {state_path, archive, path}:
            raise ValueError('--save-current: нужен новый файл; существующее содержимое не перезаписывается')
        if any(resolve(base, entry['path']) == saved for entry in state.get('documents', [])):
            raise ValueError('--save-current не может занимать путь другого зарегистрированного файла')
        saved.parent.mkdir(parents=True, exist_ok=True)
        with saved.open('xb') as stream:
            stream.write(current)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.restore')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('xb') as stream:
            stream.write(payload)
        # Не затереть изменения, которые появились, пока сохраняли копию.
        if (path.read_bytes() if path.exists() else None) != current:
            raise ValueError('Файл изменился во время восстановления; остановлено, сохранённая копия правок оставлена отдельно')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'restored': True, 'path': str(path), 'sha256': item['sha256'],
            'saved_current': str(saved) if saved else None,
            'note': 'Реестр не изменён. Для продолжения правок используйте отдельную новую версию и register'}


def local_response_links(text):
    """Разобрать локальные ссылки ответа одинаково для выдачи и её истории."""
    from markdown_it import MarkdownIt

    paths, issues = [], []
    parser = MarkdownIt('commonmark')
    validate_link = parser.validateLink
    # Только читаем адреса: file:// должен дойти до нашей явной диагностики.
    parser.validateLink = lambda url: url.lower().startswith('file:') or validate_link(url)
    parser.normalizeLink = lambda url: url  # Декодируем URI ровно один раз ниже.

    def file_citation(state, silent):
        match = re.match(r':codex-file-citation\{[^}\n]*\bpath="([^"]+)"[^}\n]*\}', state.src[state.pos:])
        if match is None:
            return False
        if not silent:
            state.push('file_citation', '', 0).attrSet('path', match[1])
        state.pos += match.end()
        return True

    # Путь цитаты читается целиком, даже если в имени есть Markdown-символы.
    parser.inline.ruler.before('text', 'file_citation', file_citation)
    hrefs = []
    has_html = False
    for block in parser.parse(text):
        if block.type == 'html_block':
            has_html = True
        for token in block.children or ():
            if token.type in {'link_open', 'image'}:
                hrefs.append(token.attrGet('href' if token.type == 'link_open' else 'src'))
            elif token.type == 'file_citation':
                hrefs.append(token.attrGet('path'))
            elif token.type == 'html_inline':
                has_html = True
            elif token.type == 'text':
                # Прежняя диагностика ошибочных inline-ссылок с пробелами без <…>.
                hrefs += re.findall(r'\]\((/[^)\n]*\s[^)\n]*)\)', token.content)
    if has_html:
        issues.append('HTML в ответе не поддерживается: используйте обычные Markdown-ссылки и изображения; '
                      'пример HTML поместите в блок кода')
    for href in hrefs:
        target = href.strip().removeprefix('<').removesuffix('>')
        if target.lower().startswith('file:'):
            issues.append(f'Локальная ссылка {target}: используйте абсолютный путь без file://')
            continue
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://', target) or target.startswith(('#', 'mailto:')):
            continue
        # Query/fragment относятся к URI; буквальные ?/# в имени должны быть закодированы.
        target = unquote(re.sub(r':\d+$', '', re.split(r'[?#]', target, maxsplit=1)[0]))
        path = Path(target)
        if not path.is_absolute():
            issues.append(f'В ответе нужен абсолютный путь: {target}')
            continue
        path = path.resolve()
        if not path.exists():
            issues.append(f'Ссылка ведёт на отсутствующий файл: {target}')
        elif not path.is_file():
            issues.append(f'Ссылка должна вести на конкретный файл: {target}')
        else:
            paths.append(path)
    return paths, issues


def check_links(response):
    """Проверить локальные ссылки без состояния сделки и записи выдачи."""
    response_path = Path(response).resolve()
    paths, issues = local_response_links(response_path.read_text(encoding='utf-8'))
    return {'valid': not issues, 'issues': issues, 'local_links': [str(path) for path in paths],
            'note': 'Проверены только локальные ссылки ответа. Состояние и реестр не читались и не изменялись; содержимое документов, внешние URL и факт отправки не проверены.'}


def links_plan_package(paths, plan_path):
    return bool(set(paths) & {plan_path, plan_path.parent / 'plan.md', plan_path.parent / 'reminders.ics'})


def plan_issue_history(state, state_path):
    """Только явная выдача задаёт базу; даты регистрации здесь не используются."""
    history = state.get('plan_issues', [])
    if not isinstance(history, list):
        raise ValueError('plan_issues: нужен упорядоченный список выдач')
    result = []
    for index, entry in enumerate(history, 1):
        if not isinstance(entry, dict):
            raise ValueError(f'plan_issues[{index}]: нужны plan и response с path и sha256')
        normalized = {}
        for kind in ('plan', 'response'):
            reference = entry.get(kind)
            if (not isinstance(reference, dict) or not isinstance(reference.get('path'), str)
                    or not reference['path'].strip() or not isinstance(reference.get('sha256'), str)
                    or re.fullmatch(r'[0-9a-f]{64}', reference['sha256']) is None):
                raise ValueError(f'plan_issues[{index}].{kind}: нужны path и SHA-256 файла')
            path = resolve(state_path.parent, reference['path'])
            if path == state_path or sha(path) != reference['sha256']:
                raise ValueError(f'plan_issues[{index}].{kind}: SHA не совпадает с сохранённой выдачей: {path}')
            normalized[kind] = {'path': relative(state_path.parent, path), 'sha256': reference['sha256']}
        plan_path = resolve(state_path.parent, normalized['plan']['path'])
        saved = deal.read_json(plan_path)
        if not isinstance(saved, dict) or saved.get('deal_id') != state.get('deal_id'):
            raise ValueError(f'plan_issues[{index}]: план относится к другой сделке: {plan_path}')
        for section in ('events', 'completed'):
            if not isinstance(saved.get(section), list):
                raise ValueError(f'plan_issues[{index}]: в плане нужен список {section}: {plan_path}')
        response_path = resolve(state_path.parent, normalized['response']['path'])
        links, link_issues = local_response_links(response_path.read_text(encoding='utf-8'))
        if link_issues:
            raise ValueError(f'plan_issues[{index}].response: {"; ".join(link_issues)}')
        if not links_plan_package(links, plan_path):
            raise ValueError(f'plan_issues[{index}].response: ответ {response_path} не содержит локальной ссылки '
                             f'на пакет плана {plan_path} (JSON, plan.md или reminders.ics)')
        result.append((normalized, plan_path, saved))
    return result


def check_plan_issue_basis(state, state_path, path, plan_data, history):
    """Пересчитать события и изменения относительно последнего выданного плана."""
    current_ref = {'path': relative(state_path.parent, path), 'sha256': sha(path)}
    # Несколько ответов могут выдавать тот же неизменный файл. Его собственные
    # changes относятся к предшествующему плану, а не к повтору самого себя.
    baseline = next(((old_path, saved) for record, old_path, saved in reversed(history)
                     if record['plan'] != current_ref), None)
    hint = (f'Используйте --previous "{baseline[0]}" — последний явно выданный план.' if baseline else
            'Выданной базы нет: пересоберите первый план без --previous. Регистрация внутренней версии не является выдачей.')
    try:
        expected = deal.plan(state, deal.stamp(plan_data['generated_at']), state_path,
                             previous=baseline[1] if baseline else None)
        if baseline:
            expected['changes'] = deal.changes(baseline[1], expected)
        elif 'changes' in plan_data:
            raise ValueError('Первый выданный план не должен содержать changes от внутренней версии')
        for section in ('events', 'completed'):
            for event in plan_data[section]:
                sequence = event.get('sequence')
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                    raise ValueError(f'{section}: недопустимый SEQUENCE события {event.get("id")}')
            if plan_data[section] != expected[section]:
                raise ValueError(f'{section}, включая SEQUENCE, не совпадает с расчётом от выданной базы')
        if plan_data.get('changes') != expected.get('changes'):
            raise ValueError('changes не совпадает с изменениями относительно выданной базы')
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f'{error}. {hint}') from error


def save(path, data):
    deal.write_state(path, data)


def link_report_source(path):
    """Связь отчёта inspect_links с исходником; прочие JSON сюда не относятся."""
    if path.suffix.lower() != '.json':
        return None
    try:
        value = deal.read_json(path)
    except ValueError:
        return None
    if not isinstance(value, dict) or not (value.get('kind') == 'link_inspection' or (
            value.get('format') in ('pdf', 'docx') and isinstance(value.get('links'), list)
            and isinstance(value.get('source'), dict))):
        return None
    source = value.get('source', {})
    if (not isinstance(source, dict) or not isinstance(source.get('path'), str) or not source['path'].strip()
            or not isinstance(source.get('sha256'), str) or re.fullmatch(r'[0-9a-f]{64}', source['sha256']) is None):
        raise ValueError(f'{path}: у отчёта ссылок нет пути и SHA исходного документа')
    original = resolve(path.parent, source['path'])
    if sha(original) != source['sha256']:
        raise ValueError(f'{path}: проверенный исходник изменён; нужен новый отчёт inspect_links')
    return original, source['sha256']


def archive_link_reports(state, base):
    """Проверка заменённого документа остаётся исторической независимо от группы."""
    documents = {resolve(base, item['path']): item for item in state.get('documents', [])}
    for path, item in documents.items():
        if not item.get('current'):
            continue
        source = link_report_source(path)
        if source and source[0] in documents and not documents[source[0]].get('current'):
            item.update(current=False, archive_reason='Проверенный документ заменён; отчёт относится к его исторической версии')


def check_link_comparison(path, expected_urls, expected_link_map=None):
    """Проверить извлечённые ссылки и расчёт сравнения с текущей картой сделки."""
    from inspect_links import compare_urls, inspect_links
    from pypdf.errors import PdfReadError
    from xml.etree.ElementTree import ParseError
    from zipfile import BadZipFile

    deal.validate_creative_links(expected_urls)
    value = deal.read_json(path)
    rows, warnings, comparison = (value.get(key) for key in ('links', 'warnings', 'comparison'))
    if (not isinstance(rows, list) or any(not isinstance(row, dict) or 'target' not in row
            or (row['target'] is not None and not isinstance(row['target'], str)) for row in rows)):
        raise ValueError(f'{path}: links должен содержать объекты с target: строка или null')
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ValueError(f'{path}: warnings должен быть списком строк')
    if not isinstance(comparison, dict):
        raise ValueError(f'{path}: нет comparison; пересоздайте отчёт inspect_links с --expected исходная-карта.json')
    original = resolve(path.parent, value['source']['path'])
    try:
        extracted = inspect_links(original)
    except (BadZipFile, ParseError, PdfReadError) as error:
        raise ValueError(f'{path}: не удалось прочитать ссылки исходного документа {original}: {error}') from error
    if extracted['source']['sha256'] != value['source']['sha256']:
        raise ValueError(f'{path}: проверенный исходник изменён; нужен новый отчёт inspect_links')
    for key in ('format', 'links', 'warnings'):
        if json.dumps(value.get(key), sort_keys=True) != json.dumps(extracted[key], sort_keys=True):
            raise ValueError(f'{path}: {key} не совпадает с извлечением из исходного документа; '
                             'пересоздайте отчёт inspect_links, сохранив адреса, якоря и предупреждения')
    actual_urls = [row['target'] for row in extracted['links'] if row['target'] is not None]
    computed = compare_urls(expected_urls, actual_urls)
    for key, expected in computed.items():
        if key == 'note':
            continue
        # JSON различает true и 1, в отличие от обычного сравнения Python.
        if key not in comparison or json.dumps(comparison[key], sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError(f'{path}: comparison.{key} не совпадает с текущей картой сделки и links[].target; '
                             'пересоздайте отчёт с правильным --expected')
    expected_pairs = None if expected_link_map is None else deal.creative_links.parse_links(expected_link_map)
    if expected_pairs is not None and [row['url'] for row in expected_pairs] != expected_urls:
        raise ValueError(f'{path}: creative.link_map и creative.links расходятся; заново импортируйте исходную карту')


def link_report_issues(documents, expected_urls, *, expected_link_map=None):
    issues, covered = [], set()
    for path, item in documents.items():
        if not item.get('current'):
            continue
        try:
            source = link_report_source(path)
            if source:
                original, digest = source
                record = documents.get(original, {})
                if not record.get('current') or record.get('sha256') != digest:
                    raise ValueError(f'{path}: отчёт ссылок относится к незарегистрированной или исторической версии; '
                                     'зарегистрируйте исходник либо сохраните этот отчёт как исторический')
                check_link_comparison(path, expected_urls, expected_link_map)
                covered.add((original, digest))
        except (KeyError, ValueError, OSError, TypeError) as error:
            issues.append(str(error))
    for path, item in documents.items():
        if (item.get('current') and path.suffix.lower() in {'.pdf', '.docx'}
                and item.get('document_kind') == 'creative'
                and (path, item['sha256']) not in covered):
            kind = 'полученного оригинала приложения' if received_status(item.get('status')) else 'собственного приложения'
            issues.append(f'{path}: нет актуального отчёта по адресам перехода {kind}. '
                          'Выполните inspect_links.py --input именно этого файла --expected исходная-карта.json '
                          '--output новый-отчёт.json, прочитайте якоря и различия, зарегистрируйте отчёт. '
                          'Каждый DOCX и PDF проверяется отдельно; отчёт другого файла не подходит')
    return issues


def layout_manifest_source(path):
    """Только manifest от render_docx; прочие JSON не загружают модуль проверки."""
    if path.suffix.lower() != '.json':
        return None
    try:
        value = deal.read_json(path)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('kind') != 'docx_layout':
        return None
    import render_docx
    return render_docx.source_reference(path, value)


def archive_layout_manifests(state, base):
    documents = {resolve(base, item['path']): item for item in state.get('documents', [])}
    for path, item in documents.items():
        if not item.get('current'):
            continue
        source = layout_manifest_source(path)
        if source and source[0] in documents and not documents[source[0]].get('current'):
            item.update(current=False, archive_reason='DOCX заменён; PDF и PNG относятся к его исторической версии')


def layout_issues(documents, soffice=None):
    issues, covered = [], set()
    for path, item in documents.items():
        if not item.get('current'):
            continue
        try:
            source = layout_manifest_source(path)
            if source:
                original, digest = source
                record = documents.get(original, {})
                if (not record.get('current') or received_status(record.get('status'))
                        or record.get('sha256') != digest):
                    raise ValueError(f'{path}: PDF и PNG относятся к незарегистрированному, полученному '
                                     'или историческому DOCX; зарегистрируйте текущий собственный исходник')
                import render_docx
                render_docx.check(path, soffice=soffice)
                covered.add((original, digest))
        except (ValueError, OSError, TypeError) as error:
            issues.append(f'Проверка вёрстки {path}: {error}')
    for path, item in documents.items():
        if (item.get('current') and not received_status(item.get('status')) and path.suffix.lower() == '.docx'
                and (path, item['sha256']) not in covered):
            issues.append(f'{path}: нет актуального PDF и PNG окончательного DOCX. '
                          'Выполните render_docx.py render в новую папку и зарегистрируйте manifest.json')
    return issues


def refresh(state_path, at):
    state_path = Path(state_path).resolve()
    state = deal.read_json(state_path)
    deal.stamp(at)
    archive_link_reports(state, state_path.parent)
    archive_layout_manifests(state, state_path.parent)
    save(state_path, state)
    return {'refreshed_at': at, 'note': 'Отчёты ссылок и вёрстки заменённых документов отмечены историческими'}


def contract_snapshot(documents):
    """Состав действующих договорных файлов, к которому относится подтверждение подписей."""
    return {item['path']: {'sha256': item['sha256'], 'document_kind': item['document_kind']}
            for item in documents
            if item.get('current') and item.get('document_kind') in CONTRACT_KINDS
            and Path(item['path']).suffix.lower() in {'.pdf', '.docx'}}


def register(state_path, files, status, version, at, supersedes=(), group='documents', document_kind=None, kind_reason=None):
    """Зарегистрировать подготовленные файлы; явную выдачу плана фиксирует check."""
    state_path = Path(state_path).resolve()
    base = state_path.parent
    state = deal.read_json(state_path)
    deal.stamp(at)
    if not status.strip() or not version.strip() or not group.strip():
        raise ValueError('status, version и постоянная group должны быть заполнены')
    if document_kind is not None:
        document_classification(document_kind, kind_reason)
    documents = state.setdefault('documents', [])
    by_path = {resolve(base, item['path']): item for item in documents}
    received_digests = {item['sha256'] for item in documents if received_status(item.get('status'))}
    prepared = []
    seen = set()
    for filename in files:
        path = Path(filename).resolve()
        if path in seen:
            raise ValueError(f'Файл указан повторно: {path}')
        seen.add(path)
        if path == state_path or not path.is_file():
            raise ValueError(f'Нужен существующий документ, не сам файл состояния: {path}')
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        previous = by_path.get(path)
        if previous and previous.get('sha256') != digest:
            raise ValueError(f'Ранее зарегистрированный файл изменён: {path}. Сохраните новую версию отдельно')
        if digest in received_digests and not received_status(status):
            raise ValueError(f'Полученный оригинал или его точная копия {path}: сохраните статус received или received_*. '
                             'Подписи и согласование учитываются в checks, а не заменой статуса получения')
        kind = document_kind if document_kind is not None else (previous or {}).get('document_kind')
        reason = kind_reason if kind_reason is not None else ((previous or {}).get('kind_reason') if kind == 'other' else None)
        document_classification(kind, reason, path.suffix.lower() in {'.pdf', '.docx'})
        prepared.append((path, digest, previous, kind, reason, payload))
    replaced = [Path(p).resolve() for p in supersedes]
    if any(path not in by_path for path in replaced):
        raise ValueError('supersedes: предыдущая версия должна быть зарегистрирована')
    if set(replaced) & {p for p, *_ in prepared}:
        raise ValueError('Нельзя одновременно выдать и заменить один файл')
    previous_contracts = contract_snapshot(documents)
    for item in documents:
        if item.get('group') == group and item.get('version') != version:
            item['current'] = False
    for path in replaced:
        by_path[path]['current'] = False
    for path, digest, previous, kind, reason, payload in prepared:
        item = previous if previous is not None else {'path': relative(base, path), 'sha256': digest}
        item.update(status=status, version=version, current=True, group=group, archive_path=archive_payload(base, payload))
        if kind is not None:
            item['document_kind'] = kind
            if kind == 'other':
                item['kind_reason'] = reason
            else:
                item.pop('kind_reason', None)
        item.setdefault('delivered_at', at)
        deliveries = item.setdefault('deliveries', [])
        if at not in deliveries:
            deliveries.append(at)
        if previous is None:
            documents.append(item)
    current_contracts = contract_snapshot(documents)
    proof = state.get('checks', {}).get('contract', {})
    if previous_contracts != current_contracts and proof.get('status') == 'confirmed':
        state['checks']['contract'] = dict(proof, status='pending',
                                          reason='Состав договорных документов изменён; требуется подтверждение текущего комплекта')
        state.setdefault('history', []).append({
            'at': at, 'action': 'register_documents',
            'previous_contract_documents': previous_contracts, 'contract_documents': current_contracts,
            'invalidated_checks': {'contract': dict(proof)}})
    archive_link_reports(state, base)
    archive_layout_manifests(state, base)
    save(state_path, state)
    return {'registered': [relative(base, p) for p, *_ in prepared], 'replaced': [relative(base, p) for p in replaced]}


MONTHS = dict(zip(('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split(), range(1, 13)))


def contract_date(value):
    """Не угадывать дату по году/подписи: принимается только полная дата поля."""
    if not isinstance(value, str):
        raise ValueError('CONTRACT_DATE: нужна строка')
    value = re.sub(r'[«»"“”]', '', value.strip().lower())
    value = re.sub(r'\s+(?:г\.?|года)$', '', value).strip()
    match = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', value)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', value)
        if match:
            day, month, year = map(int, match.groups())
        else:
            match = re.fullmatch(r'(\d{1,2})\s+([а-я]+)\s+(\d{4})', value)
            if not match or match[2] not in MONTHS:
                return None
            day, month, year = int(match[1]), MONTHS[match[2]], int(match[3])
    try:
        return date(year, month, day).isoformat()
    except ValueError as error:
        raise ValueError(f'CONTRACT_DATE: недопустимая дата {value}') from error


def check_contract_fields(documents):
    """Карта относится к точным байтам DOCX, а не к соседней версии."""
    for path, item in documents.items():
        if not item.get('current') or path.suffix.lower() != '.docx' or received_status(item.get('status')):
            continue
        sidecar = path.with_suffix(path.suffix + '.fields.json')
        if not sidecar.is_file():
            continue  # --patch и ручные документы не имеют достоверной карты полной регенерации.
        saved = deal.read_json(sidecar)
        if not isinstance(saved, dict) or not isinstance(saved.get('fields'), dict):
            raise ValueError(f'{sidecar}: нужна карта fields')
        fields = saved['fields']
        if not {'CONTRACT_NUMBER', 'CONTRACT_DATE'} <= fields.keys():
            if item.get('document_kind') in CONTRACT_KINDS:
                raise ValueError(f'{sidecar}: нужны CONTRACT_NUMBER и CONTRACT_DATE основного договора')
            continue
        record = documents.get(sidecar, {})
        if record and record.get('sha256') != sha(sidecar):
            raise ValueError(f'{path}: сохраните точную карту .fields.json; зарегистрированный SHA должен совпадать с файлом')
        if saved.get('output_sha256') != sha(path):
            if item.get('document_kind') == 'creative':
                continue  # После ручной правки текст сверяется отдельно; старая карта не доказывает дату.
            raise ValueError(f'{path}: сохраните точную карту .fields.json; зарегистрированный SHA и output_sha256 должны совпадать с файлами')
        if not isinstance(fields['CONTRACT_NUMBER'], str):
            raise ValueError('CONTRACT_NUMBER: нужна строка')
        contract_date(fields['CONTRACT_DATE'])


def audit(state_path, response=None, historical=(), record_issue=False, soffice=None):
    state_path = Path(state_path).resolve()
    base = state_path.parent
    original_state = state_path.read_bytes()
    state = json.loads(original_state)
    deal.validate(state, state_path)
    issues = []
    history = []
    try:
        history = plan_issue_history(state, state_path)
    except (ValueError, KeyError, TypeError, OSError) as error:
        issues.append(f'История выдачи планов: {error}')
    if record_issue and not response:
        issues.append('--record-issue требует --response с окончательным ответом; выдача не записана')
    # Полный креатив включает текст, URL и медиа; SHA одного файла — другое значение.
    creative_hashes = {deal.digest(state.get('creative', {}))}
    for entry in state.get('history', []):
        if isinstance(entry, dict) and entry.get('action') == 'import_creative':
            creative_hashes.update(entry.get(key) for key in ('creative_sha256', 'previous_creative_sha256') if entry.get(key))

    def check_history_hashes(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                item_path = f'{path}.{key}'
                if key.endswith('creative_sha256') and item and (not isinstance(item, str) or item not in creative_hashes):
                    issues.append(f'{item_path}: хеш не относится к полной текущей или импортированной версии креатива. SHA текста храните как text_sha256; полный хеш берите из import-creative/fingerprint')
                check_history_hashes(item, item_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check_history_hashes(item, f'{path}[{index}]')

    check_history_hashes(state.get('history', []), 'history')
    documents = {}
    current_groups = {}
    for index, item in enumerate(state.get('documents', []), 1):
        try:
            path = resolve(base, item['path'])
            if path in documents:
                raise ValueError('повторяется путь документа')
            archive = archive_file(base, item)
            if not path.is_file() or sha(path) != item.get('sha256'):
                raise ValueError(f'{path}: зарегистрированный файл утрачен или изменён. ' + restore_hint(state_path, path, item, archive))
            if item.get('delivered_at'):
                deal.stamp(item['delivered_at'])
                if not isinstance(item.get('current'), bool):
                    raise ValueError('для выданного документа нужен current: true/false')
                if not item.get('group'):
                    raise ValueError('для выданного документа нужна постоянная group без номера версии')
            if item.get('current') and item.get('group'):
                current_groups.setdefault(item['group'], set()).add(item.get('version'))
            if item.get('current') and path.suffix.lower() in {'.pdf', '.docx'}:
                document_classification(item.get('document_kind'), item.get('kind_reason'), required=True)
            documents[path] = item
        except (KeyError, ValueError, OSError, TypeError) as error:
            issues.append(f'documents[{index}]: {error}')
    for group, versions in current_groups.items():
        if len(versions) > 1:
            issues.append(f'documents: группа {group} содержит несколько актуальных версий; прежние версии должны быть историческими')
    issues.extend(link_report_issues(documents, state.get('creative', {}).get('links', []),
                                    expected_link_map=state.get('creative', {}).get('link_map')))
    issues.extend(layout_issues(documents, soffice=soffice))
    try:
        check_contract_fields(documents)
    except (ValueError, KeyError, TypeError, OSError) as error:
        issues.append(f'Карта полей договора: {error}')
    plan_path = None
    plan_files = {path for path, item in documents.items()
                  if item.get('current') and item.get('group') == 'plan' and item.get('delivered_at')}
    plan_required = state.get('schedule', {}).get('publication_at') and (
        state.get('stage') not in ('closed', 'cancelled', 'paused'))
    if plan_required or any(path.suffix.lower() == '.json' for path in plan_files):
        candidates = []
        for path in plan_files:
            if path.suffix.lower() == '.json':
                try:
                    value = deal.read_json(path)
                    if isinstance(value, dict) and 'events' in value and 'completed' in value:
                        candidates.append((path, value))
                except (ValueError, OSError) as error:
                    issues.append(f'План {path}: {error}')
        if len(candidates) != 1:
            issues.append('Дата размещения известна: нужен один актуальный локальный план JSON, Markdown и ICS в группе plan. Постройте deal.py plan и зарегистрируйте пакет; подготовка не устанавливает напоминания')
        else:
            path, plan_data = candidates[0]
            try:
                deal.validate_plan(state, plan_data, state_path)
                check_plan_issue_basis(state, state_path, path, plan_data, history)
                companion_paths = {path.parent / 'plan.md', path.parent / 'reminders.ics'}
                if not companion_paths <= plan_files:
                    raise ValueError('Зарегистрируйте вместе с JSON читаемый plan.md и reminders.ics той же версии')
                if (path.parent / 'plan.md').read_bytes() != deal.markdown(plan_data).encode('utf-8'):
                    raise ValueError('plan.md не соответствует текущему плану JSON; пересоберите пакет')
                expected_ics = deal.ics(plan_data, deal.stamp(plan_data['generated_at'])).encode('utf-8')
                if (path.parent / 'reminders.ics').read_bytes() != expected_ics:
                    raise ValueError('ICS не соответствует текущему плану JSON; пересоберите пакет')
                plan_path = path
            except (ValueError, KeyError, TypeError, OSError) as error:
                issues.append(f'План: {error}')
    response_ref = None
    response_links = []
    if response:
        response_path = Path(response).resolve()
        if response_path == state_path:
            issues.append('Ответ должен быть отдельным текстовым файлом, не файлом состояния')
        raw_response = response_path.read_bytes()
        text = raw_response.decode('utf-8')
        response_ref = {'path': relative(base, response_path),
                        'sha256': hashlib.sha256(raw_response).hexdigest()}
        historical = {Path(path).resolve() for path in historical}
        response_links, link_issues = local_response_links(text)
        issues.extend(link_issues)
        for path in response_links:
            if path == state_path:
                issues.append(f'Внутренний файл состояния сделки не входит в выдачу: {path}. '
                              'Дайте ссылку на план или зарегистрированные документы')
            elif path.is_file():
                item = documents.get(path)
                if not item or not item.get('delivered_at'):
                    issues.append(f'Выдаваемый файл не зарегистрирован, включая PDF: {path}')
                elif not item.get('current') and path not in historical:
                    issues.append(f'Ссылка на заменённую версию: {path}. Для явно архивной ссылки укажите --historical')
    recorded = False
    state_changed = False
    if record_issue and not issues and plan_path is not None:
        entry = {'plan': {'path': relative(base, plan_path), 'sha256': documents[plan_path]['sha256']}, 'response': response_ref}
        if not links_plan_package(response_links, plan_path):
            if not any(record['plan'] == entry['plan'] for record, _, _ in history):
                issues.append(f'Новый план ещё не выдан: добавьте в окончательный ответ локальную ссылку '
                              f'на пакет {plan_path} (JSON, plan.md или reminders.ics); выдача не записана')
        elif not history or history[-1][0] != entry:
            if state_path.read_bytes() != original_state:
                issues.append('Состояние изменилось во время проверки; повторите check --record-issue, выдача не записана')
            elif sha(plan_path) != entry['plan']['sha256']:
                issues.append('План изменился во время проверки; выдача не записана')
            elif sha(resolve(base, response_ref['path'])) != response_ref['sha256']:
                issues.append('Ответ изменился во время проверки; выдача не записана')
            else:
                state.setdefault('plan_issues', []).append(entry)
                state_changed = True
                recorded = True
    if state_changed:
        if state_path.read_bytes() != original_state or sha(resolve(base, response_ref['path'])) != response_ref['sha256']:
            issues.append('Состояние или ответ изменились во время проверки; выдача не записана')
            recorded = False
        else:
            save(state_path, state)
    result = {'valid': not issues, 'issues': issues,
            'checks': {key: {field: value.get(field) for field in ('status', 'source', 'checked_at')}
                       for key, value in state.get('checks', {}).items() if isinstance(value, dict)},
            'note': 'Проверены ссылки, контрольные суммы, форматы значений и схема. Это не проверка подписей, права, внешних фактов или готовности публикации.'}
    if record_issue:
        result['plan_issue_recorded'] = recorded
        result['plan_issue_count'] = len(state.get('plan_issues', [])) if isinstance(state.get('plan_issues', []), list) else 0
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    reg = sub.add_parser('register')
    reg.add_argument('--files', nargs='+', required=True, type=Path)
    reg.add_argument('--status', required=True)
    reg.add_argument('--version', required=True)
    reg.add_argument('--at', required=True)
    reg.add_argument('--group', required=True, help='Постоянное назначение пакета, без версии: contract, plan, review, correspondence, evidence')
    reg.add_argument('--supersedes', nargs='*', default=[], type=Path)
    reg.add_argument('--document-kind', choices=sorted(DOCUMENT_KINDS), help='Вид PDF/DOCX по содержанию; обязателен для собственных и полученных документов')
    reg.add_argument('--kind-reason', help='Обоснование document-kind=other')
    check = sub.add_parser('check')
    check.add_argument('--response', type=Path)
    check.add_argument('--historical', nargs='*', default=[], type=Path)
    check.add_argument('--record-issue', action='store_true', help='После всех успешных проверок зафиксировать выдачу плана с окончательным ответом по SHA')
    check.add_argument('--soffice', help='LibreOffice для проверки вёрстки DOCX и PDF исходного договора; иначе поиск в PATH')
    links = sub.add_parser('check-links', help='Проверить локальные ссылки ответа без состояния и записи выдачи')
    links.add_argument('--response', type=Path, required=True)
    restore_cmd = sub.add_parser('restore')
    restore_cmd.add_argument('--file', required=True, type=Path)
    restore_cmd.add_argument('--save-current', type=Path, help='Новый файл для сохранения текущих правок перед восстановлением; обязателен, если исходник изменён')
    refresh_cmd = sub.add_parser('refresh')
    refresh_cmd.add_argument('--at', required=True)
    for command in (reg, check, refresh_cmd, restore_cmd):
        command.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'register':
            result = register(args.state, args.files, args.status, args.version, args.at, args.supersedes, args.group,
                              args.document_kind, args.kind_reason)
        elif args.command == 'refresh':
            result = refresh(args.state, args.at)
        elif args.command == 'check-links':
            result = check_links(args.response)
        elif args.command == 'restore':
            result = restore(args.state, args.file, args.save_current)
        else:
            result = audit(args.state, args.response, args.historical, args.record_issue, soffice=args.soffice)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('valid') is False:
            sys.exit(2)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Ошибка: {error}\n')


if __name__ == '__main__':
    main()
