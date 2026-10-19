#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7", "pymupdf>=1.26,<2", "markdown-it-py>=3,<5"]
# ///
"""Учёт выдачи, дословные поручения и проверка файлов перед ответом. Без сети."""
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
REVIEW_FILES = {'manifest.json', 'review-input.json', 'terms-review.md', 'corrections.md'}


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


def received_document(item, path):
    return item.get('current') is True and received_status(item.get('status')) and path.suffix.lower() in {'.pdf', '.docx'}


def amendment_source(path):
    sidecar = path.with_suffix(path.suffix + '.fields.json')
    saved = deal.read_json(sidecar) if sidecar.is_file() else {}
    if not isinstance(saved, dict) or (saved.get('review_source') is not None and not isinstance(saved['review_source'], dict)):
        raise ValueError(f'{sidecar}: карта полей и review_source должны быть JSON-объектами')
    return saved.get('review_source')


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
    parser.normalizeLink = lambda url: url  # Не смешивать пробел с буквальным %20 в имени файла.

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
        if '://' in target or target.startswith(('#', 'mailto:')):
            continue
        target = re.sub(r':\d+$', '', target.split('#', 1)[0])
        path = Path(target)
        if not path.is_absolute():
            issues.append(f'В ответе нужен абсолютный путь: {target}')
            continue
        path = path.resolve()
        if not path.exists() and '%' in target:
            path = Path(unquote(target)).resolve()
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


def fact_basis(state):
    fields = ('stage', 'policy', 'user_role', 'parties', 'channel', 'checks', 'commercial', 'responsibilities', 'schedule', 'publication', 'creative')
    return hashlib.sha256(json.dumps({key: state.get(key) for key in fields}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fact_bound(item):
    """Письма и авторские заметки описывают факты на момент составления."""
    if received_status(item.get('status')):
        return False
    return item.get('group') in {'correspondence', 'analysis'} or (
        item.get('group') in {'evidence', 'source'} and Path(item.get('path', '')).suffix.lower() == '.md')


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


def check_link_comparison(path, documents, expected_urls, expected_link_map=None):
    """Сверить адреса и якоря; неизвестная карта или якорь требуют явного разбора."""
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
    # anchor_hint PDF приблизителен даже при буквальном совпадении: нужен просмотр.
    # У старого состояния без link_map видимые якоря также неизвестны.
    anchors_need_review = bool(rows or expected_urls) and (
        extracted['format'] != 'docx' or expected_pairs is None
        or [{'anchor': row.get('anchor'), 'url': row['target']} for row in rows] != expected_pairs)
    if (not computed['ordered_lists_equal'] or warnings or any(row['target'] is None for row in rows)
            or anchors_need_review):
        resolution = value.get('resolution')
        if (not isinstance(resolution, dict) or resolution.get('result') != 'accepted'
                or any(not isinstance(resolution.get(key), str) or not resolution[key].strip()
                       for key in ('notes', 'source'))):
            raise ValueError(f'{path}: различия или предупреждения не разобраны либо якоря не сверены; '
                             'нужны resolution.result=accepted, '
                             'notes с объяснением якорей и source с путём зарегистрированной сверки')
        source = resolve(path.parent, resolution['source'])
        record = documents.get(source, {})
        if (source in {path, resolve(path.parent, value['source']['path'])}
                or record.get('group') not in {'review', 'analysis'} or not record.get('current')
                or not source.is_file() or record.get('sha256') != sha(source)):
            raise ValueError(f'{path}: resolution.source должен указывать на актуальный зарегистрированный файл сверки')


def check_creative_document(path, report_path, documents, state, state_path):
    """Приложение содержит текущий импортированный пост; его согласование отдельно."""
    creative = state.get('creative', {})
    provenance = creative.get('provenance')
    provenance = provenance.get('text') if isinstance(provenance, dict) else None
    text = creative.get('text')
    if not isinstance(provenance, dict) or not isinstance(text, str) or not text.strip():
        raise ValueError(f'{path}: сначала импортируйте исходный пост через deal.py import-creative; '
                         'одинаковые URL не подтверждают текст приложения')
    creative_hash = deal.fingerprint(state, state_path)
    normal_lines = lambda value: value.replace('\r\n', '\n').replace('\r', '\n')
    sidecar = path.with_suffix(path.suffix + '.fields.json')
    saved = (deal.read_json(sidecar) if path.suffix.lower() == '.docx'
             and not received_status(documents[path].get('status')) and sidecar.is_file() else None)
    if isinstance(saved, dict) and saved.get('output_sha256') == sha(path):
        if not isinstance(saved.get('fields'), dict):
            raise ValueError(f'{sidecar}: нужна сохранённая карта fields')
        source = saved.get('post_source')
        if (saved['fields'].get('POST_TEXT') != text
                or not isinstance(source, dict) or source.get('sha256') != provenance.get('sha256')
                or not isinstance(source.get('path'), str) or sha(resolve(sidecar.parent, source['path'])) != source['sha256']):
            raise ValueError(f'{path}: POST_TEXT, post_source или SHA результата не совпадает с текущим импортированным постом; '
                             'сохраните отдельную версию, не подменяйте карту полей')
        import render_contract
        if not any(normal_lines(row['text']) == normal_lines(text)
                   for row in render_contract.inspect_document(path)['paragraphs'] if row['part'] == 'word/document.xml'):
            raise ValueError(f'{path}: точный POST_TEXT не найден в самом DOCX; карта полей не заменяет проверку текста')
        return

    report = deal.read_json(report_path)
    proof = report.get('creative_review')
    if (not isinstance(proof, dict) or proof.get('creative_sha256') != creative_hash
            or not isinstance(proof.get('source'), str) or not proof['source'].strip()):
        raise ValueError(f'{path}: нужна явная creative_review текущего текста в отчёте ссылок: '
                         'creative_sha256, source зарегистрированной сверки и citations по документу')
    evidence = resolve(report_path.parent, proof['source'])
    record = documents.get(evidence, {})
    if (evidence in {path, report_path} or record.get('group') not in {'review', 'analysis'}
            or not record.get('current') or not evidence.is_file() or record.get('sha256') != sha(evidence)):
        raise ValueError(f'{path}: creative_review.source должен указывать на актуальную зарегистрированную сверку')
    citations = proof.get('citations')
    if (not isinstance(citations, list) or not citations
            or any(not isinstance(row, dict) or not isinstance(row.get('quote'), str)
                   or (path.suffix.lower() == '.pdf' and not row['quote']) for row in citations)):
        raise ValueError(f'{path}: creative_review.citations должен содержать дословные части всего поста')
    if path.suffix.lower() == '.pdf':
        import review
        pages = [row.get('page') for row in citations]
        if (any(type(page) is not int or page < 1 for page in pages)
                or pages != list(range(pages[0], pages[-1] + 1))):
            raise ValueError(f'{path}: citations требуют одну цитату на страницу, страницы идут подряд в порядке PDF')
        readers = {}
        for index, citation in enumerate(citations, 1):
            review.verify_citation({'id': f'creative-{index}', 'source': dict(citation, document='creative')},
                                   {'path': str(path)}, readers)
        actual = ''.join(row['quote'] for row in citations)
    else:
        import render_contract
        paragraphs = {row['paragraph']: row['text'] for row in render_contract.inspect_document(path)['paragraphs']
                      if row['part'] == 'word/document.xml'}
        indices = [row.get('paragraph') for row in citations]
        if (any(type(index) is not int or index not in paragraphs for index in indices)
                or indices != list(range(indices[0], indices[-1] + 1))):
            raise ValueError(f'{path}: citations должны указывать последовательные абзацы word/document.xml из --inspect')
        for row in citations:
            if normal_lines(row['quote']) != normal_lines(paragraphs[row['paragraph']]):
                raise ValueError(f'{path}: цитата поста не совпадает с указанным абзацем DOCX')
        actual = '\n'.join(row['quote'] for row in citations)
    if normal_lines(actual) != normal_lines(text):
        raise ValueError(f'{path}: полный текст из creative_review не совпадает с текущим импортированным постом')


def link_report_issues(documents, expected_urls, state=None, state_path=None, *, expected_link_map=None):
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
                check_link_comparison(path, documents, expected_urls, expected_link_map)
                if state is not None and record.get('document_kind') == 'creative':
                    check_creative_document(original, path, documents, state, state_path)
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
            item.update(current=False, archive_reason='DOCX заменён; просмотр вёрстки относится к его исторической версии')


def layout_issues(documents):
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
                    raise ValueError(f'{path}: просмотр вёрстки относится к незарегистрированному, полученному '
                                     'или историческому DOCX; зарегистрируйте текущий собственный исходник')
                import render_docx
                render_docx.check(path)
                covered.add((original, digest))
        except (ValueError, OSError, TypeError) as error:
            issues.append(f'Проверка вёрстки {path}: {error}')
    for path, item in documents.items():
        if (item.get('current') and not received_status(item.get('status')) and path.suffix.lower() == '.docx'
                and (path, item['sha256']) not in covered):
            issues.append(f'{path}: нет актуального подтверждённого просмотра вёрстки окончательного DOCX. '
                          'Выполните render_docx.py render в новую папку, просмотрите все PNG, затем confirm '
                          '--result passed --viewed-pages ... --notes ... и зарегистрируйте manifest.json. '
                          'needs_changes или просмотр прежней версии не разрешают выдачу')
    return issues


def refresh_records(state, at):
    """Советы и письма — снимки фактов; архивирование не завершает действия сделки."""
    deal.stamp(at)
    basis = fact_basis(state)
    for index, item in enumerate(state.get('recommendations', [])):
        if not isinstance(item, dict):
            state['recommendations'][index] = {'text': str(item), 'current': False,
                                               'archive_reason': 'Старая заметка без даты и проверяемого основания'}
            continue
        if item.get('at') == at and item.get('current') is not False and item.get('basis_sha256', basis) == basis:
            item.update(current=True, basis_sha256=basis)
        else:
            item.update(current=False, archive_reason='Совет относится к прежнему состоянию; актуальные действия см. в проверках и плане')
    for item in state.get('documents', []):
        if fact_bound(item) and item.get('basis_sha256') != basis:
            item.update(current=False, archive_reason='Письмо или аналитическая заметка составлены до изменения фактов сделки')


def refresh(state_path, at):
    state_path = Path(state_path).resolve()
    state = deal.read_json(state_path)
    refresh_records(state, at)
    archive_link_reports(state, state_path.parent)
    archive_layout_manifests(state, state_path.parent)
    save(state_path, state)
    return {'refreshed_at': at, 'note': 'Прежние советы и устаревшие письма сохранены как исторические; проверки и обязательства не закрывались'}


def contract_snapshot(documents):
    """Состав действующих договорных файлов, к которому относится подтверждение подписей."""
    return {item['path']: {'sha256': item['sha256'], 'document_kind': item['document_kind']}
            for item in documents
            if item.get('current') and item.get('document_kind') in CONTRACT_KINDS
            and Path(item['path']).suffix.lower() in {'.pdf', '.docx'}}


def register(state_path, files, status, version, at, supersedes=(), group='documents', document_kind=None, kind_reason=None, source_docx=None):
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
    exported_from = None
    if source_docx is not None:
        source_docx = Path(source_docx).resolve()
        source_record = by_path.get(source_docx, {})
        if (source_docx.suffix.lower() != '.docx' or not source_docx.is_file()
                or source_record.get('sha256') != sha(source_docx) or not source_record.get('current')
                or source_record.get('document_kind') != 'amendment'):
            raise ValueError('--source-docx: сначала зарегистрируйте исходный DOCX как актуальный document-kind=amendment')
        exported_from = {'path': relative(base, source_docx), 'sha256': sha(source_docx)}
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
        if previous and fact_bound(previous) and previous.get('basis_sha256') != fact_basis(state):
            raise ValueError(f'Основание прежнего письма или заметки изменилось: {path}. Подготовьте актуальную редакцию отдельным файлом; повторная регистрация не обновляет его содержание')
        kind = document_kind if document_kind is not None else (previous or {}).get('document_kind')
        reason = kind_reason if kind_reason is not None else ((previous or {}).get('kind_reason') if kind == 'other' else None)
        document_classification(kind, reason, path.suffix.lower() in {'.pdf', '.docx'})
        if exported_from and (path.suffix.lower() != '.pdf' or kind != 'amendment' or received_status(status)):
            raise ValueError('--source-docx применяется только к собственному PDF с document-kind=amendment')
        if exported_from and previous and previous.get('source_docx') not in (None, exported_from):
            raise ValueError('Исходный DOCX ранее выданного PDF нельзя менять повторной регистрацией; сохраните новую версию PDF')
        review_source = (previous or {}).get('review_source')
        if kind == 'amendment' and path.suffix.lower() == '.docx' and not received_status(status):
            current_source = amendment_source(path)
            if review_source is not None and review_source != current_source:
                raise ValueError('Связь выданного допсоглашения со сверкой изменилась; сохраните новую версию')
            review_source = current_source
        prepared.append((path, digest, previous, kind, reason, review_source, payload))
    replaced = [Path(p).resolve() for p in supersedes]
    if any(path not in by_path for path in replaced):
        raise ValueError('supersedes: предыдущая версия должна быть зарегистрирована')
    if set(replaced) & {p for p, *_ in prepared}:
        raise ValueError('Нельзя одновременно выдать и заменить один файл')
    previous_contracts = contract_snapshot(documents)
    refresh_records(state, at)
    for item in documents:
        if item.get('group') == group and item.get('version') != version:
            item['current'] = False
    for path in replaced:
        by_path[path]['current'] = False
    for path, digest, previous, kind, reason, review_source, payload in prepared:
        item = previous if previous is not None else {'path': relative(base, path), 'sha256': digest}
        item.update(status=status, version=version, current=True, group=group, archive_path=archive_payload(base, payload))
        if kind is not None:
            item['document_kind'] = kind
            if kind == 'other':
                item['kind_reason'] = reason
            else:
                item.pop('kind_reason', None)
        if fact_bound(item):
            item['basis_sha256'] = fact_basis(state)
        if exported_from:
            item['source_docx'] = exported_from
        if review_source is not None:
            item['review_source'] = review_source
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
        refresh_records(state, at)
    archive_link_reports(state, base)
    archive_layout_manifests(state, base)
    save(state_path, state)
    return {'registered': [relative(base, p) for p, *_ in prepared], 'replaced': [relative(base, p) for p in replaced]}


def check_contract_review(documents, state_path, plan_path, required=False):
    received = {path: item for path, item in documents.items()
                if received_document(item, path) and item.get('document_kind') in CONTRACT_KINDS}
    if not received and not required:
        return
    review_files = {path for path, item in documents.items()
                    if item.get('current') and item.get('group') == 'review' and item.get('delivered_at')}
    manifests = [path for path in review_files if path.name == 'manifest.json']
    if len(manifests) != 1:
        raise ValueError('Полученные договорные документы требуют один актуальный зарегистрированный пакет review: '
                         'manifest.json, review-input.json, terms-review.md и corrections.md. Произвольная заметка сверку не заменяет')
    directory = manifests[0].parent
    if not {directory / name for name in REVIEW_FILES} <= review_files:
        raise ValueError('Пакет review неполон: зарегистрируйте manifest.json, review-input.json, terms-review.md и corrections.md одной версии')
    import review  # Платёжные документы и первоначальная анкета не загружают PDF-зависимость.
    review.check_state(directory, state_path, plan_path)
    manifest = deal.read_json(manifests[0])
    reviewed = {source['sha256'] for source in manifest['sources']}
    reviewed.update(source['original']['sha256'] for source in manifest['sources'] if 'original' in source)
    missing = [str(path) for path, item in received.items() if item['sha256'] not in reviewed]
    if missing:
        raise ValueError('Текущая сверка не охватывает полученные договорные источники по SHA: ' + ', '.join(missing))
    return directory


def check_amendments(documents, state_path, plan_path, review_dir):
    """Классификация явная; DOCX допсоглашения связан с точными поправками review."""
    own = {path: item for path, item in documents.items() if item.get('current')
           and not received_status(item.get('status')) and path.suffix.lower() in {'.docx', '.pdf'}}
    for path, item in own.items():
        if review_dir is not None:
            document_classification(item.get('document_kind'), item.get('kind_reason'), required=True)
        if item.get('document_kind') != 'amendment':
            continue
        source = path
        if path.suffix.lower() == '.pdf':
            original = item.get('source_docx')
            if original is None and review_dir is None:
                continue  # Самостоятельное допсоглашение, полученной редакции нет.
            if not isinstance(original, dict):
                raise ValueError(f'{path}: PDF допсоглашения требует register --source-docx FILE')
            source = resolve(state_path.parent, original['path'])
            record = documents.get(source, {})
            if (source.suffix.lower() != '.docx' or not record.get('current')
                    or record.get('document_kind') != 'amendment' or received_status(record.get('status'))
                    or record.get('sha256') != original.get('sha256')):
                raise ValueError(f'{path}: исходный DOCX допсоглашения не зарегистрирован как текущий или SHA не совпадает')
        sidecar = source.with_suffix(source.suffix + '.fields.json')
        proof = amendment_source(source)
        recorded = documents.get(source, {}).get('review_source')
        if recorded is not None and recorded != proof:
            raise ValueError(f'{source}: сохранённая в реестре связь со сверкой не совпадает с картой .fields.json')
        if review_dir is None and not proof:
            continue
        if review_dir is None:
            review_dir = check_contract_review(documents, state_path, plan_path, required=True)
        if not documents.get(sidecar, {}).get('current'):
            raise ValueError(f'{source}: зарегистрируйте текущую карту .fields.json вместе с DOCX допсоглашения')
        import render_contract
        render_contract.verify_review_document(source, review_dir)


def instruction(state_path, source, quote, at):
    state_path = Path(state_path).resolve()
    source = Path(source).resolve()
    deal.stamp(at)
    if not quote.strip() or quote not in source.read_text(encoding='utf-8'):
        raise ValueError('Поручение должно быть дословным фрагментом сообщения пользователя; пересказ и собственный совет не подходят')
    state = deal.read_json(state_path)
    record = {'text': quote, 'source': relative(state_path.parent, source),
              'source_sha256': sha(source), 'recorded_at': at}
    entries = state.setdefault('user_instructions', [])
    if not any(isinstance(item, dict) and item.get('text') == quote and item.get('source') == record['source'] for item in entries):
        entries.append(record)
    save(state_path, state)
    return record


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


def contract_number(value):
    if not isinstance(value, str):
        raise ValueError('CONTRACT_NUMBER: нужна строка')
    value = value.strip().removeprefix('№').strip()
    return value if value and not re.fullmatch(r'_+|\{\{[^{}]+\}\}|\[[^\[\]]+\]', value) else None


def own_contract_identity(path, documents, base):
    """Карта относится к точным байтам DOCX, а не к соседней версии."""
    item = documents.get(path, {})
    if path.suffix.lower() != '.docx' or received_status(item.get('status')):
        return None
    sidecar = path.with_suffix(path.suffix + '.fields.json')
    if not sidecar.is_file():
        return None  # --patch и ручные документы не имеют достоверной карты полной регенерации.
    saved = deal.read_json(sidecar)
    if not isinstance(saved, dict) or not isinstance(saved.get('fields'), dict):
        raise ValueError(f'{sidecar}: нужна карта fields')
    fields = saved['fields']
    if not {'CONTRACT_NUMBER', 'CONTRACT_DATE'} <= fields.keys():
        if item.get('document_kind') in CONTRACT_KINDS:
            raise ValueError(f'{sidecar}: нужны CONTRACT_NUMBER и CONTRACT_DATE основного договора')
        return None
    record = documents.get(sidecar, {})
    if record and record.get('sha256') != sha(sidecar):
        raise ValueError(f'{path}: сохраните точную карту .fields.json; зарегистрированный SHA должен совпадать с файлом')
    if saved.get('output_sha256') != sha(path) and item.get('document_kind') == 'creative':
        return None  # После ручной правки текст сверяется отдельно; старая карта не доказывает дату.
    if saved.get('output_sha256') != sha(path):
        raise ValueError(f'{path}: сохраните точную карту .fields.json; зарегистрированный SHA и output_sha256 должны совпадать с файлами')
    number = contract_number(fields['CONTRACT_NUMBER'])
    return {'number': number, 'date': contract_date(fields['CONTRACT_DATE']),
            'document': {'path': relative(base, path), 'sha256': item['sha256']},
            'fields': {'path': relative(base, sidecar), 'sha256': sha(sidecar)}}


def validate_contract_date_changes(state, base):
    changes = state.get('contract_date_changes', [])
    if not isinstance(changes, list):
        raise ValueError('contract_date_changes: нужен список подтверждённых изменений')
    for entry in changes:
        if not isinstance(entry, dict) or not contract_number(entry.get('number')):
            raise ValueError('contract_date_changes: нужен номер договора')
        if not entry.get('from_date') or not entry.get('to_date'):
            raise ValueError('contract_date_changes: обе даты должны быть известны')
        if (contract_date(entry['from_date']) != entry['from_date']
                or contract_date(entry['to_date']) != entry['to_date']
                or entry['from_date'] == entry['to_date']):
            raise ValueError('contract_date_changes: нужны разные даты в формате ГГГГ-ММ-ДД')
        source = resolve(base, entry['source'])
        if (sha(source) != entry['source_sha256'] or not entry.get('quote')
                or entry['quote'] not in source.read_text(encoding='utf-8')):
            raise ValueError('contract_date_changes: изменён источник или нет точной цитаты поручения')
        deal.stamp(entry['at'])
    return changes


def contract_issue_history(state, documents, base, plan_history):
    """Старые состояния восстанавливают выдачу по ссылкам сохранённых ответов."""
    entries = state.get('contract_issues')
    if entries is None or entries == []:
        entries = []
        seen_documents = set()
        for record, _, _ in plan_history:
            response = resolve(base, record['response']['path'])
            links, _ = local_response_links(response.read_text(encoding='utf-8'))
            for path in dict.fromkeys(links):
                identity = own_contract_identity(path, documents, base) if path in documents else None
                if identity:
                    key = (identity['document']['path'], identity['document']['sha256'])
                    if key not in seen_documents:
                        entries.append({name: identity[name] for name in ('document', 'fields')} | {'response': record['response']})
                        seen_documents.add(key)  # Повторная архивная ссылка не является новой редакцией договора.
    if not isinstance(entries, list):
        raise ValueError('contract_issues: нужен список выдач собственных договорных документов')
    known = {}
    for entry in entries:
        for key in ('document', 'fields', 'response'):
            ref = entry[key]
            path = resolve(base, ref['path'])
            if sha(path) != ref['sha256']:
                raise ValueError(f'contract_issues: изменён выданный {key}: {path}')
        path = resolve(base, entry['document']['path'])
        identity = own_contract_identity(path, documents, base)
        if not identity or any(identity[key] != entry[key] for key in ('document', 'fields')):
            raise ValueError('contract_issues: карта или собственный документ не соответствуют реестру')
        response = resolve(base, entry['response']['path'])
        links, issues = local_response_links(response.read_text(encoding='utf-8'))
        if issues or path not in links:
            raise ValueError(f'contract_issues: ответ не подтверждает выдачу документа {path}')
        if identity['number'] and identity['date']:
            known[identity['number']] = identity['date']
    return entries, known


def check_contract_dates(state, documents, base, plan_history):
    changes = validate_contract_date_changes(state, base)
    entries, known = contract_issue_history(state, documents, base, plan_history)
    current = {}
    for path, item in documents.items():
        if item.get('current'):
            identity = own_contract_identity(path, documents, base)
            if identity:
                current[path] = identity
    problems = []
    for path, identity in current.items():
        number, value = identity['number'], identity['date']
        old = known.get(number)
        if old and value != old:
            allowed = value is not None and any(entry['number'] == number and entry['from_date'] == old
                                                and entry['to_date'] == value for entry in changes)
            if not allowed:
                problems.append(f'{path}: CONTRACT_DATE договора № {number} была выдана как {old}, '
                                 f'теперь {value or "неизвестна"}. Сохраните известную дату документа; фактические даты подписей отдельны. '
                                 'Для явного изменения пользователем выполните contract-date-change с точным источником и цитатой')
    for number in {item['number'] for item in current.values()} - {None}:
        dates = {item['date'] for item in current.values() if item['number'] == number}
        if len(dates) > 1:
            problems.append(f'CONTRACT_DATE договора № {number}: текущие собственные документы содержат разные даты; обновите или архивируйте прежнюю редакцию')
    if problems:
        raise ValueError('\n'.join(problems))
    return entries, current


def contract_date_change(state_path, number, from_date, to_date, source, quote, at):
    state_path, source = Path(state_path).resolve(), Path(source).resolve()
    state = deal.read_json(state_path)
    number, before, after = contract_number(number), contract_date(from_date), contract_date(to_date)
    if not number or not before or not after or before == after:
        raise ValueError('Смена даты требует номер и две разные известные даты; очистка даты запрещена')
    deal.stamp(at)
    if not quote.strip() or quote not in source.read_text(encoding='utf-8'):
        raise ValueError('Нужна точная цитата явного поручения пользователя из указанного источника')
    documents = {resolve(state_path.parent, item['path']): item for item in state.get('documents', [])}
    _, known = contract_issue_history(state, documents, state_path.parent, plan_issue_history(state, state_path))
    if known.get(number) != before:
        raise ValueError('from-date должна совпадать с последней известной выданной датой этого номера договора')
    validate_contract_date_changes(state, state_path.parent)
    entry = {'number': number, 'from_date': before, 'to_date': after,
             'source': relative(state_path.parent, source), 'source_sha256': sha(source), 'quote': quote, 'at': at}
    changes = state.setdefault('contract_date_changes', [])
    if entry not in changes:
        changes.append(entry)
        save(state_path, state)
    return entry


def audit(state_path, response=None, historical=(), record_issue=False):
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
    basis = fact_basis(state)
    for index, item in enumerate(state.get('recommendations', []), 1):
        if not isinstance(item, dict) or not isinstance(item.get('current'), bool):
            issues.append(f'recommendations[{index}]: выполните refresh с временем текущей реплики, чтобы отделить исторические советы')
        elif item['current'] and item.get('basis_sha256') != basis:
            issues.append(f'recommendations[{index}]: факты изменились после совета; обновите его либо выполните refresh для архивирования')
    for index, item in enumerate(state.get('user_instructions', []), 1):
        try:
            if not isinstance(item, dict) or not item.get('text') or not item.get('source'):
                raise ValueError('нужны text с дословным поручением и source, а не пересказ')
            path = resolve(base, item['source'])
            if sha(path) != item.get('source_sha256'):
                raise ValueError('не совпадает контрольная сумма исходного сообщения')
            if item['text'] not in path.read_text(encoding='utf-8'):
                raise ValueError('такого дословного поручения нет в источнике')
        except (ValueError, OSError, TypeError) as error:
            issues.append(f'user_instructions[{index}]: {error}')
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
                if fact_bound(item) and item.get('basis_sha256') != basis:
                    raise ValueError('письмо или аналитическая заметка основаны на прежних фактах; выполните refresh и подготовьте новую версию при необходимости')
            if item.get('current') and path.suffix.lower() in {'.pdf', '.docx'}:
                document_classification(item.get('document_kind'), item.get('kind_reason'), required=True)
            documents[path] = item
        except (KeyError, ValueError, OSError, TypeError) as error:
            issues.append(f'documents[{index}]: {error}')
    for group, versions in current_groups.items():
        if len(versions) > 1:
            issues.append(f'documents: группа {group} содержит несколько актуальных версий; прежние версии должны быть историческими')
    issues.extend(link_report_issues(documents, state.get('creative', {}).get('links', []), state, state_path,
                                    expected_link_map=state.get('creative', {}).get('link_map')))
    issues.extend(layout_issues(documents))
    contract_history, contract_documents = [], {}
    unchecked_contract_dates = [
        {'path': str(path), 'reason': 'Нет сохранённой карты .fields.json: дата основного договора автоматически не проверена. '
         'Сверьте её с предыдущей выданной редакцией вручную; не создавайте фиктивную карту и не пересобирайте ручные правки из старых полей.'}
        for path, item in documents.items() if item.get('current') and not received_status(item.get('status'))
        and path.suffix.lower() == '.docx' and item.get('document_kind') in CONTRACT_KINDS
        and not path.with_suffix(path.suffix + '.fields.json').is_file()]
    try:
        contract_history, contract_documents = check_contract_dates(state, documents, base, history)
    except (ValueError, KeyError, TypeError, OSError) as error:
        issues.append(f'Дата договора: {error}')
    unchecked_contract_dates += [
        {'path': str(path), 'reason': 'Сохранённая карта прежней версии не подтверждает дату после ручной правки приложения. '
         'Сверьте номер и дату по документу; текст поста требует отдельной creative_review.'}
        for path, item in documents.items() if item.get('current') and not received_status(item.get('status'))
        and item.get('document_kind') == 'creative' and path.suffix.lower() == '.docx'
        and path not in contract_documents and path.with_suffix(path.suffix + '.fields.json').is_file()]
    plan_path = None
    needs_review = any(received_document(item, path) and item.get('document_kind') in CONTRACT_KINDS
                       for path, item in documents.items())
    for path, item in documents.items():
        if (item.get('current') and not received_status(item.get('status'))
                and item.get('document_kind') == 'amendment' and path.suffix.lower() == '.docx'):
            try:
                needs_review = needs_review or bool(item.get('review_source')) or bool(amendment_source(path))
            except (OSError, ValueError):
                needs_review = True  # Точную ошибку карты ниже вернёт check_amendments.
    plan_files = {path for path, item in documents.items()
                  if item.get('current') and item.get('group') == 'plan' and item.get('delivered_at')}
    plan_required = state.get('schedule', {}).get('publication_at') and (
        needs_review or state.get('stage') not in ('closed', 'cancelled', 'paused'))
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
    try:
        review_dir = check_contract_review(documents, state_path, plan_path)
        check_amendments(documents, state_path, plan_path, review_dir)
    except (ValueError, KeyError, TypeError, OSError) as error:
        issues.append(f'Сверка договора: {error}')
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
    contract_recorded = False
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
    if record_issue and not issues:
        new_contract_history = list(contract_history)
        for path in dict.fromkeys(response_links):
            identity = contract_documents.get(path)
            if identity and identity['number']:
                entry = {key: identity[key] for key in ('document', 'fields')} | {'response': response_ref}
                if entry not in new_contract_history:
                    new_contract_history.append(entry)
        if new_contract_history != contract_history:
            state['contract_issues'] = new_contract_history
            state_changed = True
            contract_recorded = True
        if state_changed:
            if state_path.read_bytes() != original_state or sha(resolve(base, response_ref['path'])) != response_ref['sha256']:
                issues.append('Состояние или ответ изменились во время проверки; выдача не записана')
                recorded = False
                contract_recorded = False
            else:
                save(state_path, state)
    result = {'valid': not issues, 'issues': issues,
            'contract_date_unchecked': unchecked_contract_dates,
            'checks': {key: {field: value.get(field) for field in ('status', 'source', 'checked_at')}
                       for key, value in state.get('checks', {}).items() if isinstance(value, dict)},
            'note': 'Проверены ссылки, контрольные суммы, дословность поручений и схема. Это не проверка подписей, права, внешних фактов или готовности публикации.'}
    if record_issue:
        result['contract_issue_recorded'] = contract_recorded
        result['contract_issue_count'] = len(state.get('contract_issues', [])) if isinstance(state.get('contract_issues', []), list) else 0
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
    reg.add_argument('--source-docx', type=Path, help='Зарегистрированный исходный DOCX собственного PDF-допсоглашения; качество экспорта проверяется визуально')
    ins = sub.add_parser('instruction')
    ins.add_argument('--source', type=Path, required=True)
    ins.add_argument('--quote', required=True)
    ins.add_argument('--at', required=True)
    change_date = sub.add_parser('contract-date-change', help='Подтверждённая пользователем смена известной даты основного договора')
    change_date.add_argument('--number', required=True)
    change_date.add_argument('--from-date', required=True)
    change_date.add_argument('--to-date', required=True)
    change_date.add_argument('--source', type=Path, required=True)
    change_date.add_argument('--quote', required=True)
    change_date.add_argument('--at', required=True)
    check = sub.add_parser('check')
    check.add_argument('--response', type=Path)
    check.add_argument('--historical', nargs='*', default=[], type=Path)
    check.add_argument('--record-issue', action='store_true', help='После всех успешных проверок зафиксировать выдачу плана и собственных договорных DOCX с окончательным ответом по SHA')
    links = sub.add_parser('check-links', help='Проверить локальные ссылки ответа без состояния и записи выдачи')
    links.add_argument('--response', type=Path, required=True)
    restore_cmd = sub.add_parser('restore')
    restore_cmd.add_argument('--file', required=True, type=Path)
    restore_cmd.add_argument('--save-current', type=Path, help='Новый файл для сохранения текущих правок перед восстановлением; обязателен, если исходник изменён')
    refresh_cmd = sub.add_parser('refresh')
    refresh_cmd.add_argument('--at', required=True)
    for command in (reg, ins, check, refresh_cmd, restore_cmd, change_date):
        command.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'register':
            result = register(args.state, args.files, args.status, args.version, args.at, args.supersedes, args.group,
                              args.document_kind, args.kind_reason, args.source_docx)
        elif args.command == 'instruction':
            result = instruction(args.state, args.source, args.quote, args.at)
        elif args.command == 'contract-date-change':
            result = contract_date_change(args.state, args.number, args.from_date, args.to_date,
                                          args.source, args.quote, args.at)
        elif args.command == 'refresh':
            result = refresh(args.state, args.at)
        elif args.command == 'check-links':
            result = check_links(args.response)
        elif args.command == 'restore':
            result = restore(args.state, args.file, args.save_current)
        else:
            result = audit(args.state, args.response, args.historical, args.record_issue)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('valid') is False:
            sys.exit(2)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Ошибка: {error}\n')


if __name__ == '__main__':
    main()
