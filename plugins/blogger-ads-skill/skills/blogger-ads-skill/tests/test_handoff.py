import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('handoff', ROOT / 'scripts/handoff.py')
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        from test_docx_layout import fake_convert_to_pdf
        self.converter = patch('render_docx.convert_to_pdf', side_effect=fake_convert_to_pdf)
        self.converter.start()
        self.addCleanup(self.converter.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'deal-state.json'
        self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
        self.at = '2026-11-01T12:00:00+03:00'

    def file(self, name, text='Текст документа'):
        path = self.root / name
        path.write_text(text)
        return path

    def layout(self, source):
        from test_docx_layout import make_layout_fixture
        import render_docx
        manifest = make_layout_fixture(source, self.root / ('layout-' + source.stem))
        render_docx.confirm(manifest, [1, 2], 'passed', 'Синтетический просмотр для проверки реестра')
        handoff.register(self.state, [manifest], 'checked', 'v1', self.at, group='layout-' + source.stem)
        return manifest

    def fixture_hash(self, data, path):
        # Для проверки содержательных связей создаём согласованные технические SHA тестового файла.
        item = next(item for item in data['documents'] if handoff.resolve(self.root, item['path']) == path.resolve())
        item.update(sha256=handoff.sha(path), archive_path=handoff.archive_payload(self.root, path.read_bytes()))

    def data(self):
        return json.loads(self.state.read_text())

    def scheduled_state(self):
        data = self.data()
        data['schedule']['publication_at'] = '2026-11-09T09:00:00+03:00'
        data['schedule']['reporting_deadlines'] = [
            {'id': key, 'title': label, 'at': self.at, 'source': 'Организационная проверка до выхода',
             'kind': 'organizational', 'checks': [key]}
            for key, label in handoff.deal.CHECK_LABELS.items()]
        handoff.save(self.state, data)

    def plan_package(self, name='plan-v1', previous=None):
        folder = self.root / name
        command = [sys.executable, str(ROOT / 'scripts/deal.py'), 'plan', '--state', str(self.state),
                   '--now', self.at, '--output-dir', str(folder)]
        if previous:
            command += ['--previous', str(previous)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [folder / name for name in ('plan.json', 'plan.md', 'reminders.ics')]

    def register_plan(self, files):
        handoff.register(self.state, files, 'prepared', files[0].parent.name, self.at, group='plan')

    def issue_plan(self, files, reply='reply-v1.txt'):
        response = self.file(reply, f'Готов план: [план](<{files[1]}>)')
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        return response

    def change_plan_state(self, duration, revision):
        data = self.data()
        data['schedule'].update(duration_hours=duration, revision=revision)
        handoff.save(self.state, data)

    def rewrite_plan(self, files, change):
        data = json.loads(files[0].read_text())
        change(data)
        files[0].write_text(json.dumps(data))
        files[2].write_text(handoff.deal.ics(data, handoff.deal.stamp(data['generated_at'])))

    def link_report(self, source, name='links-report.json', expected=None):
        import inspect_links
        return self.file(name, json.dumps(inspect_links.inspect_links(source, [] if expected is None else expected)))

    def import_post(self, text, urls=()):
        post = self.file('post-source.txt', text)
        links = self.file('post-links.json', json.dumps(list(urls)))
        handoff.deal.import_creative(self.state, post, links, self.at)
        return post

    def creative_review(self, report, citations):
        note = self.file(report.stem + '-text-review.md', 'Весь текст поста сопоставлен с указанными страницами/абзацами приложения.')
        handoff.register(self.state, [note], 'reviewed', 'v1', self.at, group='analysis')
        data = json.loads(report.read_text())
        data['creative_review'] = {'creative_sha256': handoff.deal.fingerprint(self.data(), self.state),
                                   'source': note.name, 'citations': citations}
        if data['format'] == 'pdf':
            original = handoff.resolve(report.parent, data['source']['path'])
            data['creative_review']['viewed_pages'] = list(range(1, len(PdfReader(original).pages) + 1))
        report.write_text(json.dumps(data))
        return note

    def link_issues(self):
        creative = self.data()['creative']
        return handoff.link_report_issues(self.registered(), creative['links'],
                                         expected_link_map=creative.get('link_map'))

    def received_links(self, expected, actual):
        from pypdf.annotations import Link
        data = self.data()
        data['creative']['links'] = expected
        handoff.save(self.state, data)
        source = self.root / 'received-links.pdf'
        writer = PdfWriter()
        writer.add_blank_page(300, 300)
        for index, url in enumerate(actual):
            writer.add_annotation(0, Link(rect=(10, 10 + index * 20, 100, 20 + index * 20), url=url))
        with source.open('wb') as output:
            writer.write(output)
        handoff.register(self.state, [source], 'received', 'v1', self.at,
                         group='received', document_kind='creative')
        return source, self.link_report(source, expected=expected)

    def register_link_report(self, report):
        handoff.register(self.state, [report], 'checked', 'v1', self.at, group='link-evidence')

    def resolved_links(self, report):
        note = self.file('link-resolution.md', 'Адрес в печатной таблице повторён: якоря проверены на странице 1.')
        handoff.register(self.state, [note], 'reviewed', 'v1', self.at, group='analysis')
        data = json.loads(report.read_text())
        data['resolution'] = {'result': 'accepted', 'notes': 'Дополнительная ссылка относится к печатной таблице.',
                              'source': note.name}
        report.write_text(json.dumps(data))
        return note

    def registered(self):
        return {handoff.resolve(self.root, item['path']): item for item in self.data()['documents']}

    def test_received_creative_requires_its_own_link_inspection(self):
        received = self.received_pdf('received-creative.pdf')
        own = self.received_pdf('own-creative.pdf', 'Our revised creative.')
        handoff.register(self.state, [received], 'received', 'v1', self.at,
                         group='received', document_kind='creative')
        handoff.register(self.state, [own], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        own_report = self.link_report(own)
        handoff.register(self.state, [own_report], 'checked', 'v1', self.at, group='own-evidence')
        self.assertTrue(any(str(received) in issue for issue in self.link_issues()))
        received_report = self.link_report(received, 'received-links.json')
        handoff.register(self.state, [received_report], 'checked', 'v1', self.at, group='received-evidence')
        self.assertEqual(self.link_issues(), [])

    def test_received_link_report_with_matching_urls_and_resolved_anchors_passes_readonly(self):
        urls = ['https://example.test/?b=2&a=1', 'https://example.test/?a=1&b=2'] * 2
        source, report = self.received_links(urls, urls)
        self.resolved_links(report)
        self.register_link_report(report)
        before = {path: path.read_bytes() for path in (self.state, source, report)}
        self.assertEqual(self.link_issues(), [])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_link_report_without_expected_blocks_otherwise_valid_handoff(self):
        import inspect_links
        text = 'The approved post.'
        self.import_post(text)
        source = self.received_pdf(text=text)
        handoff.register(self.state, [source], 'draft', 'v1', self.at, group='received', document_kind='creative')
        report = self.link_report(source)
        self.creative_review(report, [{'page': 1, 'quote': text}])
        # Собственный PDF не требует договорной сверки. Отчёт с comparison
        # позволяет выдачу, замена на отчёт без comparison блокирует её.
        handoff.register(self.state, [source], 'draft', 'v1', self.at, group='received')
        self.register_link_report(report)
        self.assertTrue(handoff.audit(self.state)['valid'])
        unchecked = self.file('unchecked-links.json', json.dumps(inspect_links.inspect_links(source)))
        handoff.register(self.state, [unchecked], 'checked', 'v2', self.at, group='link-evidence')
        self.assertTrue(any('нет comparison' in issue for issue in self.link_issues()))
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('нет comparison' in issue for issue in result['issues']))

    def test_outgoing_docx_and_pdf_each_require_current_compared_link_report(self):
        from pypdf.annotations import Link
        url = 'https://example.test/product'
        self.import_post('Продукт', [{'anchor': 'Продукт', 'url': url}])
        docx, pdf = self.root / 'outgoing.docx', self.root / 'outgoing.pdf'
        with ZipFile(docx, 'w') as archive:
            archive.writestr('word/document.xml',
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
                '<w:p><w:hyperlink r:id="r1"><w:r><w:t>Продукт</w:t></w:r></w:hyperlink></w:p>'
                '</w:body></w:document>')
            archive.writestr('word/_rels/document.xml.rels',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                f'Target="{url}" TargetMode="External"/></Relationships>')
        writer = PdfWriter()
        writer.add_blank_page(300, 300)
        writer.add_annotation(0, Link(rect=(10, 10, 100, 20), url=url))
        with pdf.open('wb') as output:
            writer.write(output)
        handoff.register(self.state, [docx, pdf], 'draft_for_review', 'v1', self.at,
                         group='creative', document_kind='creative')
        self.layout(docx)
        response = self.file('reply.txt', f'[Приложение](<{docx}>) и [PDF](<{pdf}>)')
        result = handoff.audit(self.state, response)
        self.assertFalse(result['valid'])
        for path in (docx, pdf):
            self.assertTrue(any(str(path) in issue and 'нет актуального отчёта' in issue for issue in result['issues']))

        docx_report = self.link_report(docx, 'docx-links.json', [url])
        self.creative_review(docx_report, [{'paragraph': 1, 'quote': 'Продукт'}])
        self.register_link_report(docx_report)
        result = handoff.audit(self.state, response)
        self.assertFalse(result['valid'])
        self.assertTrue(any(str(pdf) in issue and 'нет актуального отчёта' in issue for issue in result['issues']))
        self.assertFalse(any(str(docx) in issue and 'нет актуального отчёта' in issue for issue in result['issues']))

        pdf_report = self.link_report(pdf, 'pdf-links.json', [url])
        self.creative_review(pdf_report, [{'page': 1, 'quote': 'Продукт',
                                          'verification': {'method': 'visual', 'basis': 'Учебная страница без текстового слоя.'}}])
        self.resolved_links(pdf_report)
        handoff.register(self.state, [docx_report, pdf_report], 'checked', 'v1', self.at, group='link-evidence')
        before = {path: path.read_bytes() for path in (self.state, docx, pdf, docx_report, pdf_report)}
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        for path in (docx, pdf):
            with self.subTest(changed_source=path.name):
                path.write_bytes(before[path] + b'\nchanged')
                result = handoff.audit(self.state, response)
                self.assertFalse(result['valid'])
                self.assertTrue(any('проверенный исходник изменён' in issue for issue in result['issues']))
                path.write_bytes(before[path])

    def test_received_link_report_uses_current_deal_map(self):
        _, report = self.received_links([], [])
        self.register_link_report(report)
        data = self.data()
        data['creative']['links'] = ['https://example.test/expected']
        handoff.save(self.state, data)
        self.assertTrue(any('comparison.expected_urls' in issue for issue in self.link_issues()))

    def test_received_link_differences_require_explicit_resolution(self):
        urls = ['https://example.test/product', 'https://example.test/pricing']
        _, report = self.received_links(urls, urls[::-1])
        # Числа одинаковы, но изменённый порядок тоже нужно разобрать по якорям.
        self.assertEqual(json.loads(report.read_text())['comparison']['differences'], [])
        self.register_link_report(report)
        self.assertTrue(any('не разобраны' in issue for issue in self.link_issues()))

    def test_extra_table_link_with_registered_resolution_passes(self):
        url = 'https://example.test/product'
        _, report = self.received_links([url], [url, url])
        note = self.resolved_links(report)
        self.register_link_report(report)
        before = {path: path.read_bytes() for path in (self.state, report, note)}
        self.assertEqual(self.link_issues(), [])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_received_link_warnings_require_resolution_even_when_lists_match(self):
        from pypdf.annotations import Link
        source = self.root / 'warning.pdf'
        writer = PdfWriter()
        writer.add_blank_page(300, 300)
        writer.add_annotation(0, Link(rect=(10, 10, 100, 20), url='https://example.test/product'))
        writer.pages[0]['/Annots'][0].get_object()['/A'].pop('/URI')
        with source.open('wb') as output:
            writer.write(output)
        handoff.register(self.state, [source], 'draft', 'v1', self.at, document_kind='creative')
        report = self.link_report(source)
        data = json.loads(report.read_text())
        self.assertTrue(data['warnings'])
        self.register_link_report(report)
        self.assertTrue(any('не разобраны' in issue for issue in self.link_issues()))
        data['warnings'] = []
        report.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'warnings не совпадает'):
            handoff.check_link_comparison(report, self.registered(), [])

    def test_report_cannot_replace_actual_pdf_docx_links_or_anchors_with_invented_rows(self):
        import inspect_links
        from pypdf.annotations import Link
        wanted, actual = 'https://example.test/product', 'https://example.test/wrong-target'
        data = self.data()
        data['creative']['links'] = [wanted]
        handoff.save(self.state, data)
        for suffix in ('pdf', 'docx'):
            with self.subTest(suffix=suffix):
                source = self.root / ('creative.' + suffix)
                if suffix == 'pdf':
                    writer = PdfWriter()
                    writer.add_blank_page(300, 300)
                    writer.add_annotation(0, Link(rect=(10, 10, 100, 20), url=actual))
                    with source.open('wb') as output:
                        writer.write(output)
                else:
                    with ZipFile(source, 'w') as archive:
                        archive.writestr('word/document.xml',
                            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                            '<w:body><w:p><w:hyperlink r:id="r1"><w:r><w:t>Продукт</w:t></w:r></w:hyperlink>'
                            '</w:p></w:body></w:document>')
                        archive.writestr('word/_rels/document.xml.rels',
                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                            f'<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                            f'Target="{actual}" TargetMode="External"/></Relationships>')
                handoff.register(self.state, [source], 'draft', 'v1', self.at,
                                 group='creative-' + suffix, document_kind='creative')
                report = self.link_report(source, suffix + '-links.json', [wanted])
                original = json.loads(report.read_text())
                forged = json.loads(json.dumps(original))
                forged['links'][0]['target'] = wanted
                forged['comparison'] = inspect_links.compare_urls([wanted], [wanted])
                report.write_text(json.dumps(forged))
                handoff.register(self.state, [report], 'checked', 'v1', self.at, group='evidence-' + suffix)
                before = {path: path.read_bytes() for path in (self.state, source, report)}
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('links не совпадает с извлечением' in issue for issue in result['issues']))
                self.assertEqual(before, {path: path.read_bytes() for path in before})
                # Подмена якорей или формата тоже не исправляется верными URL/SHA.
                for key in ('anchor', 'format'):
                    altered = json.loads(json.dumps(original))
                    if key == 'anchor':
                        altered['links'][0]['anchor' if suffix == 'docx' else 'anchor_hint'] = 'Другой продукт'
                    else:
                        altered['format'] = 'docx' if suffix == 'pdf' else 'pdf'
                    report.write_text(json.dumps(altered))
                    with self.assertRaisesRegex(ValueError, 'не совпадает с извлечением'):
                        handoff.check_link_comparison(report, self.registered(), [wanted])
                report.write_bytes(before[report])

    def test_link_audit_reports_damaged_pdf_zip_and_xml_without_traceback(self):
        import inspect_links
        for name in ('damaged.pdf', 'damaged.docx', 'malformed-xml.docx'):
            with self.subTest(name=name):
                source = self.file(name, 'Повреждённый документ')
                if name == 'malformed-xml.docx':
                    with ZipFile(source, 'w') as archive:
                        archive.writestr('word/document.xml', '<unfinished')
                handoff.register(self.state, [source], 'draft', name, self.at,
                                 group='creative', document_kind='creative')
                report = self.file(name + '.json', json.dumps({
                    'kind': 'link_inspection', 'source': {'path': str(source), 'sha256': handoff.sha(source)},
                    'format': source.suffix[1:], 'links': [], 'warnings': [],
                    'comparison': inspect_links.compare_urls([], [])}))
                handoff.register(self.state, [report], 'checked', name, self.at, group='link-evidence')
                before = self.state.read_bytes()
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('не удалось прочитать ссылки' in issue for issue in result['issues']))
                self.assertEqual(before, self.state.read_bytes())

    def test_link_report_rejects_tampered_comparison_and_malformed_rows(self):
        url = 'https://example.test/product'
        _, report = self.received_links([url], [url, url])
        original = json.loads(report.read_text())
        mutations = {
            'forged_match': lambda data: data['comparison'].update(ordered_lists_equal=True),
            'forged_actual': lambda data: data['comparison'].update(actual_urls=[url]),
            'forged_expected': lambda data: data['comparison'].update(expected_urls=[url, url]),
            'hidden_difference': lambda data: data['comparison'].update(differences=[]),
            'forged_counts': lambda data: data['comparison']['counts'][0].update(actual=1),
            'bool_as_count': lambda data: data['comparison'].update(expected_count=True),
            'null_comparison': lambda data: data.update(comparison=None),
            'list_comparison': lambda data: data.update(comparison=[]),
            'dict_links': lambda data: data.update(links={}),
            'string_row': lambda data: data.update(links=['url']),
            'missing_target': lambda data: data.update(links=[{}]),
            'object_target': lambda data: data.update(links=[{'target': {'url': url}}]),
            'string_warnings': lambda data: data.update(warnings=''),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                data = json.loads(json.dumps(original))
                mutate(data)
                report.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    handoff.check_link_comparison(report, self.registered(), [url])

    def test_link_report_rejects_malformed_current_expected_map(self):
        _, report = self.received_links([], [])
        for invalid in (None, {}, '', [True], [{}], ['not-a-url']):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'creative.links'):
                handoff.check_link_comparison(report, self.registered(), invalid)

    def test_link_resolution_requires_current_unchanged_registered_review(self):
        url = 'https://example.test/product'
        source, report = self.received_links([url], [url, url])
        note = self.resolved_links(report)
        original = json.loads(report.read_text())
        for change in ({'result': 'pending'}, {'notes': ' '}, {'notes': []}, {'source': {}},
                       {'source': 'missing.md'}, {'source': source.name}, {'source': report.name}):
            with self.subTest(change=change):
                data = json.loads(json.dumps(original))
                data['resolution'].update(change)
                report.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    handoff.check_link_comparison(report, self.registered(), [url])
        report.write_text(json.dumps(original))
        records = self.registered()
        for change in ({'current': False}, {'sha256': '0' * 64}, {'group': 'source'}):
            with self.subTest(change=change):
                changed = {path: dict(item) for path, item in records.items()}
                changed[note.resolve()].update(change)
                with self.assertRaisesRegex(ValueError, 'resolution.source'):
                    handoff.check_link_comparison(report, changed, [url])

    def test_replacing_document_archives_its_link_report_across_groups(self):
        old = self.received_pdf('old.pdf')
        handoff.register(self.state, [old], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        report = self.link_report(old)
        handoff.register(self.state, [report], 'checked', 'v1', self.at, group='evidence')
        contents = {p: p.read_bytes() for p in (old, report)}
        new = self.received_pdf('new.pdf', 'A revised creative.')
        handoff.register(self.state, [new], 'draft', 'v2', self.at, group='creative', document_kind='creative')
        records = self.registered()
        self.assertFalse(records[old.resolve()]['current'])
        self.assertFalse(records[report.resolve()]['current'])
        self.assertTrue(records[new.resolve()]['current'])
        self.assertEqual(contents, {p: p.read_bytes() for p in contents})
        # Ручное возвращение старого отчёта в current не обходит проверку.
        data = self.data()
        next(item for item in data['documents'] if item['path'] == report.name)['current'] = True
        handoff.save(self.state, data)
        self.assertTrue(any('исторической' in issue for issue in handoff.audit(self.state)['issues']))
        handoff.refresh(self.state, self.at)
        self.assertFalse(self.registered()[report.resolve()]['current'])

    def test_link_report_rejects_changed_source_without_mutation(self):
        source = self.received_pdf()
        handoff.register(self.state, [source], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        report = self.link_report(source)
        source.write_bytes(source.read_bytes() + b'\nchanged')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'исходник изменён'):
            handoff.register(self.state, [report], 'checked', 'v1', self.at, group='evidence')
        self.assertEqual(before, self.state.read_bytes())

    def test_malformed_link_report_source_is_a_validation_error(self):
        report = self.file('malformed-report.json', json.dumps({'kind': 'link_inspection', 'source': None, 'links': []}))
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'нет пути и SHA'):
            handoff.register(self.state, [report], 'checked', 'v1', self.at, group='evidence')
        self.assertEqual(before, self.state.read_bytes())
        data = self.data()
        data['documents'] = [{'path': report.name, 'sha256': handoff.sha(report), 'current': True,
                              'archive_path': handoff.archive_payload(self.root, report.read_bytes())}]
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('нет пути и SHA' in issue for issue in result['issues']))

    def test_plan_registration_and_readonly_check_do_not_record_issue(self):
        self.scheduled_state()
        files = self.plan_package()
        self.register_plan(files)
        response = self.file('reply.txt', f'[План](<{files[1]}>)')
        before = {path: path.read_bytes() for path in (self.state, *files, response)}
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])
        self.assertNotIn('plan_issues', self.data())
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        # Даже повторная регистрация с delivered_at не становится базой.
        self.register_plan(files)
        self.assertNotIn('plan_issues', self.data())

    def test_first_issue_rejects_changes_based_on_registered_internal_plan(self):
        self.scheduled_state()
        internal = self.plan_package('internal')
        self.register_plan(internal)
        first = self.plan_package('first-final', internal[0])
        self.register_plan(first)
        before = self.state.read_bytes()
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('без --previous' in item for item in result['issues']))
        self.assertEqual(before, self.state.read_bytes())
        fixed = self.plan_package('first-without-baseline')
        self.register_plan(fixed)
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_later_plan_preserves_issued_plan_as_historical_baseline(self):
        self.scheduled_state()
        first_at = self.at
        first = self.plan_package()
        self.register_plan(first)
        response = self.issue_plan(first)
        original_files = {path: path.read_bytes() for path in [*first, response]}
        first_issue = self.data()['plan_issues'][0]

        self.at = '2026-11-01T15:00:00+03:00'
        current = self.plan_package('plan-v2', first[0])
        self.register_plan(current)
        self.issue_plan(current, 'reply-v2.txt')

        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(len(self.data()['plan_issues']), 2)
        self.assertEqual(self.data()['plan_issues'][0], first_issue)
        self.assertEqual(json.loads(first[0].read_text())['generated_at'], first_at)
        self.assertEqual(json.loads(current[0].read_text())['generated_at'], self.at)
        self.assertTrue(all(not self.registered()[path.resolve()]['current'] for path in first))
        self.assertTrue(all(self.registered()[path.resolve()]['current'] for path in current))
        self.assertEqual(original_files, {path: path.read_bytes() for path in original_files})

    def test_multiple_internal_rebuilds_cannot_replace_the_issued_baseline(self):
        self.scheduled_state()
        first = self.plan_package()
        self.register_plan(first)
        self.issue_plan(first)
        self.change_plan_state(49, 2)
        internal = self.plan_package('internal-v2', first[0])
        self.register_plan(internal)
        self.change_plan_state(50, 3)
        second_internal = self.plan_package('internal-v3', internal[0])
        self.register_plan(second_internal)
        self.assertEqual(len(self.data()['plan_issues']), 1)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any(f'--previous "{first[0].resolve()}"' in item for item in result['issues']))
        final = self.plan_package('final-v3', first[0])
        self.register_plan(final)
        self.issue_plan(final, 'reply-final.txt')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(len(self.data()['plan_issues']), 2)
        self.assertEqual(self.data()['plan_issues'][0]['plan']['path'], 'plan-v1/plan.json')
        # Все регистрации и выдачи имели одинаковое время; порядок задан журналом.
        self.assertTrue(all(item['delivered_at'] == self.at for item in self.data()['documents']))

    def test_wrong_changes_fail_even_when_sequences_are_correct(self):
        self.scheduled_state()
        first = self.plan_package()
        self.register_plan(first)
        self.issue_plan(first)
        self.change_plan_state(49, 2)
        final = self.plan_package('final-v2', first[0])
        correct = json.loads(final[0].read_text())
        self.assertTrue(correct['changes']['update'])
        self.rewrite_plan(final, lambda data: data.update(changes={'create': [], 'update': [], 'cancel': []}))
        self.register_plan(final)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('changes не совпадает' in item and str(first[0]) in item for item in result['issues']))
        self.assertEqual(json.loads(final[0].read_text())['events'], correct['events'])

    def test_wrong_sequence_fails_even_when_calendar_and_changes_match(self):
        self.scheduled_state()
        first = self.plan_package()
        self.register_plan(first)
        self.issue_plan(first)
        self.change_plan_state(49, 2)
        final = self.plan_package('final-v2', first[0])
        self.rewrite_plan(final, lambda data: data['events'][0].update(sequence=99))
        self.register_plan(final)
        response = self.file('reply-v2.txt', f'[План](<{final[1]}>)')
        before = self.state.read_bytes()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('SEQUENCE' in item and str(first[0]) in item for item in result['issues']))
        self.assertEqual(before, self.state.read_bytes())

    def test_completed_event_sequence_is_checked_too(self):
        self.scheduled_state()
        data = self.data()
        proof = {'status': 'confirmed', 'checked_at': self.at, 'source': 'Получено подтверждение законности'}
        data['checks']['legality'] = proof
        data['completed_events']['report-legality'] = dict(proof)
        handoff.save(self.state, data)
        files = self.plan_package()
        self.assertTrue(json.loads(files[0].read_text())['completed'])
        self.rewrite_plan(files, lambda plan: plan['completed'][0].update(sequence=99))
        self.register_plan(files)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('completed, включая SEQUENCE' in item for item in result['issues']))

    def test_inactive_status_does_not_skip_existing_current_plan_sequences(self):
        self.scheduled_state()
        initial = self.data()
        for stage in ('closed', 'paused', 'cancelled'):
            with self.subTest(stage=stage):
                handoff.save(self.state, dict(initial, stage=stage))
                files = self.plan_package('plan-' + stage)
                self.rewrite_plan(files, lambda data: data['events'][0].update(sequence=99))
                self.register_plan(files)
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('SEQUENCE' in item for item in result['issues']), result['issues'])

    def test_record_issue_is_idempotent_and_same_plan_can_have_another_reply(self):
        self.scheduled_state()
        files = self.plan_package()
        self.register_plan(files)
        response = self.issue_plan(files)
        before, mtime = self.state.read_bytes(), self.state.stat().st_mtime_ns
        for record in (False, True, True):
            result = handoff.audit(self.state, response, record_issue=record)
            self.assertTrue(result['valid'], result['issues'])
            if record:
                self.assertFalse(result['plan_issue_recorded'])
        self.assertEqual(before, self.state.read_bytes())
        self.assertEqual(mtime, self.state.stat().st_mtime_ns)
        self.issue_plan(files, 'reply-again.txt')
        self.assertEqual(len(self.data()['plan_issues']), 2)
        self.assertTrue(handoff.audit(self.state)['valid'])
        self.change_plan_state(49, 2)
        next_plan = self.plan_package('next', files[0])
        self.register_plan(next_plan)
        self.issue_plan(next_plan, 'reply-next.txt')
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_record_issue_missing_or_invalid_response_does_not_write(self):
        self.scheduled_state()
        files = self.plan_package()
        self.register_plan(files)
        before = self.state.read_bytes()
        result = handoff.audit(self.state, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('--response' in item for item in result['issues']))
        self.assertEqual(before, self.state.read_bytes())
        response = self.file('reply.txt', '[Файл](relative-path.pdf)')
        self.assertFalse(handoff.audit(self.state, response, record_issue=True)['valid'])
        self.assertEqual(before, self.state.read_bytes())
        self.assertFalse(handoff.audit(self.state, self.state, record_issue=True)['valid'])
        self.assertEqual(before, self.state.read_bytes())

    def test_new_plan_requires_a_link_in_the_final_response(self):
        self.scheduled_state()
        files = self.plan_package()
        self.register_plan(files)
        before, mtime = self.state.read_bytes(), self.state.stat().st_mtime_ns
        for text in ('План ещё не выдаю.', f'План готов: {files[1]}',
                     '[План](https://example.org/plan.md)', '[План](#plan)'):
            with self.subTest(text=text):
                response = self.file('reply.txt', text)
                self.assertTrue(handoff.audit(self.state, response)['valid'])
                result = handoff.audit(self.state, response, record_issue=True)
                self.assertFalse(result['valid'])
                self.assertFalse(result['plan_issue_recorded'])
                self.assertTrue(any('Новый план ещё не выдан' in item for item in result['issues']))
                self.assertEqual(before, self.state.read_bytes())
                self.assertEqual(mtime, self.state.stat().st_mtime_ns)

    def test_encoded_link_cannot_deliver_an_unregistered_file_with_a_similar_name(self):
        literal = self.file('file%20name.txt', 'Зарегистрированная версия')
        spaced = self.file('file name.txt', 'Другая версия')
        handoff.register(self.state, [literal], 'prepared', 'v1', self.at, group='reference')
        before = self.state.read_bytes()
        response = self.file('reply.txt', f'[Документ](<{literal}>)')
        result = handoff.audit(self.state, response)
        self.assertFalse(result['valid'])
        self.assertTrue(any('не зарегистрирован' in issue and str(spaced) in issue for issue in result['issues']),
                        result['issues'])
        self.assertEqual(self.state.read_bytes(), before)

        encoded_literal = str(literal).replace('%', '%25')
        response.write_text(f'[Документ](<{encoded_literal}>)')
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(self.state.read_bytes(), before)

    def test_query_link_cannot_deliver_an_unregistered_file_hidden_by_a_registered_name(self):
        actual = self.file('document.txt', 'Незарегистрированная версия')
        literal = self.file('document.txt?download=1', 'Зарегистрированная версия')
        handoff.register(self.state, [literal], 'prepared', 'v1', self.at, group='reference')
        for query in ('download=1', 'next=https://example.org'):
            with self.subTest(query=query):
                response = self.file('reply.txt', f'[Документ](<{actual}?{query}#page=2>)')
                before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
                result = handoff.audit(self.state, response, record_issue=True)
                self.assertFalse(result['valid'])
                self.assertTrue(any('не зарегистрирован' in issue and str(actual) in issue for issue in result['issues']),
                                result['issues'])
                self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
        encoded = str(literal).replace('?', '%3F')
        response.write_text(f'[Документ](<{encoded}>)')
        before = self.state.read_bytes()
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(self.state.read_bytes(), before)

    def test_previous_or_foreign_plan_link_cannot_issue_current_plan(self):
        self.scheduled_state()
        first = self.plan_package()
        self.register_plan(first)
        self.issue_plan(first)
        self.change_plan_state(49, 2)
        current = self.plan_package('current', first[0])
        self.register_plan(current)
        foreign = self.root / 'foreign'
        foreign.mkdir()
        foreign_plan = foreign / 'plan.md'
        foreign_plan.write_text('План другого проекта')
        handoff.register(self.state, [foreign_plan], 'prepared', 'v1', self.at, group='reference')
        before = self.state.read_bytes()
        for path in (first[1], foreign_plan):
            with self.subTest(path=path):
                response = self.file('reply-current.txt', f'[План](<{path}>)')
                self.assertTrue(handoff.audit(self.state, response, historical=first)['valid'])
                result = handoff.audit(self.state, response, historical=first, record_issue=True)
                self.assertFalse(result['valid'])
                self.assertFalse(result['plan_issue_recorded'])
                self.assertTrue(any('Новый план ещё не выдан' in item for item in result['issues']))
                self.assertEqual(before, self.state.read_bytes())

    def test_any_current_package_file_can_be_issued_with_supported_link_formats(self):
        self.scheduled_state()
        files = self.plan_package('План (1) #2')
        self.register_plan(files)
        registered = self.state.read_bytes()
        for path in files:
            encoded = str(path).replace(' ', '%20').replace('#', '%23')
            for text in (f'[План](<{encoded}>)', f'[План](<{encoded}:1>)',
                         f':codex-file-citation{{path="{encoded}" purpose="source"}}'):
                with self.subTest(file=path.name, text=text):
                    self.state.write_bytes(registered)
                    response = self.file('reply.txt', text)
                    result = handoff.audit(self.state, response, record_issue=True)
                    self.assertTrue(result['valid'], result['issues'])
                    self.assertTrue(result['plan_issue_recorded'])
                    self.assertEqual(len(self.data()['plan_issues']), 1)
                    # История читает ссылки тем же способом, включая цитаты файлов.
                    self.assertTrue(handoff.audit(self.state)['valid'])

    def test_repeated_status_without_link_does_not_create_an_issue(self):
        self.scheduled_state()
        files = self.plan_package()
        self.register_plan(files)
        self.issue_plan(files)
        response = self.file('status.txt', 'Ожидаем подтверждение оплаты.')
        before, mtime = self.state.read_bytes(), self.state.stat().st_mtime_ns
        for _ in range(2):
            result = handoff.audit(self.state, response, record_issue=True)
            self.assertTrue(result['valid'], result['issues'])
            self.assertFalse(result['plan_issue_recorded'])
            self.assertEqual(result['plan_issue_count'], 1)
        self.assertEqual(before, self.state.read_bytes())
        self.assertEqual(mtime, self.state.stat().st_mtime_ns)

    def test_history_requires_its_own_plan_link_even_after_response_sha_is_replaced(self):
        self.scheduled_state()
        first = self.plan_package()
        self.register_plan(first)
        old_response = self.issue_plan(first)
        self.change_plan_state(49, 2)
        current = self.plan_package('current', first[0])
        self.register_plan(current)
        response = self.issue_plan(current, 'reply-current.txt')
        for text in ('План ещё не выдаю.', f'[План](<{current[1]}>)'):
            with self.subTest(text=text):
                old_response.write_text(text)
                data = self.data()
                data['plan_issues'][0]['response']['sha256'] = handoff.sha(old_response)
                handoff.save(self.state, data)
                before = self.state.read_bytes()
                for record in (False, True):
                    result = handoff.audit(self.state, response, record_issue=record)
                    self.assertFalse(result['valid'])
                    self.assertTrue(any('plan_issues[1].response' in item and 'не содержит локальной ссылки' in item
                                        for item in result['issues']))
                    self.assertEqual(before, self.state.read_bytes())

    def test_issue_checks_every_recorded_plan_and_response_sha(self):
        self.scheduled_state()
        first = self.plan_package()
        self.register_plan(first)
        response = self.issue_plan(first)
        self.change_plan_state(49, 2)
        final = self.plan_package('final-v2', first[0])
        self.register_plan(final)
        self.issue_plan(final, 'reply-v2.txt')
        initial_state = self.state.read_bytes()
        for path in (first[0], response):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                data = self.data()
                # Обновление старого реестра не меняет SHA явной выдачи.
                for item in data['documents']:
                    if handoff.resolve(self.root, item['path']) == path:
                        item['sha256'] = handoff.sha(path)
                handoff.save(self.state, data)
                before = self.state.read_bytes()
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('plan_issues[1]' in item and str(path) in item for item in result['issues']))
                self.assertEqual(before, self.state.read_bytes())
                path.write_bytes(original)
                self.state.write_bytes(initial_state)

    def test_record_issue_cli_and_unknown_publication(self):
        response = self.file('reply.txt', 'Уточним дату размещения.')
        command = [sys.executable, str(ROOT / 'scripts/handoff.py'), 'check', '--state', str(self.state),
                   '--response', str(response), '--record-issue']
        before = self.state.read_bytes()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['plan_issue_recorded'])
        self.assertEqual(before, self.state.read_bytes())
        self.scheduled_state()
        files = self.plan_package()
        self.register_plan(files)
        response.write_text(f'[План](<{files[1]}>)')
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['plan_issue_recorded'])
        record = self.data()['plan_issues'][0]
        self.assertEqual(record['plan']['sha256'], handoff.sha(files[0]))
        self.assertEqual(record['response']['sha256'], handoff.sha(response))

    def test_issue_waits_for_contract_review_and_rejects_malformed_history(self):
        _, _, plans = self.review_package()
        response = self.file('reply.txt', f'[План](<{plans[1]}>)')
        data = self.data()
        review_record = next(item for item in data['documents'] if item['path'].endswith('/review-input.json'))
        review_record['current'] = False
        handoff.save(self.state, data)
        before = self.state.read_bytes()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('Сверка договора' in issue for issue in result['issues']))
        self.assertEqual(before, self.state.read_bytes())
        review_record['current'] = True
        for malformed in (None, {}, ['plan.json'], [{'plan': {'path': 'missing.json'}}]):
            data['plan_issues'] = malformed
            handoff.save(self.state, data)
            before = self.state.read_bytes()
            result = handoff.audit(self.state, response, record_issue=True)
            self.assertFalse(result['valid'])
            self.assertTrue(any('plan_issues' in issue for issue in result['issues']))
            self.assertEqual(before, self.state.read_bytes())

    def received_pdf(self, name='received.pdf', text='The parties agree on advertising services.'):
        path = self.root / name
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
            NameObject('/F1'): DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})})})
        content = DecodedStreamObject()
        content.set_data(f'BT /F1 12 Tf 10 200 Td ({text}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(content)
        with path.open('wb') as output:
            writer.write(output)
        return path

    def review_package(self, source=None, with_changes=False):
        import review
        self.scheduled_state()
        source = source or self.received_pdf()
        handoff.register(self.state, [source], 'received', 'v1', self.at, group='source', document_kind='contract')
        data = {
            'title': 'Учебная сверка полученного договора', 'scope': {'mode': 'full'},
            'comparison_at': self.at,
            'review_basis': {'edition': 'received', 'reason': 'Предварительный план по полученной редакции.'},
            'documents': [{'id': 'contract', 'label': 'Полученный договор', 'path': source.name, 'viewed_pages': [1]}],
            'signature_matrix': [{'label': 'Учебный договор', 'source': {'document': 'contract', 'page': 1},
                                  'not_applicable': 'Учебный текст без подписной части; просмотрена вся страница.'}],
            'decisions': [{
                'id': 'services', 'category': 'Общие условия', 'topic': 'Рекламные услуги',
                'source': {'document': 'contract', 'clause': '1', 'page': 1, 'quote': 'The parties agree on advertising services.'},
                'previous': {'source': {'kind': 'none', 'group': 'contract',
                             'reason': 'Первое полученное предложение, собственного проекта ещё не было.'}},
                'status': 'pending',
                'decision': {'action': 'defer', 'reason': 'Недостаточно условий для принятия',
                             'selected': 'Уточнить до принятия', 'plan': 'Запросить уточнение при согласовании договора'},
            }]
        }
        plans = self.plan_package()
        handoff.register(self.state, plans, 'prepared', 'v1', self.at, group='plan')
        if with_changes:
            data['decisions'][0]['decision'].update(action='replace', target='Договор, пункт 1',
                                                   original='agree', text='agree in writing')
        input_path = self.file('review-decisions.json', json.dumps(data))
        folder = self.root / 'review-v1'
        review.build(input_path, folder)
        handoff.register(self.state, [folder / name for name in handoff.REVIEW_FILES], 'reviewed', 'v1', self.at, group='review')
        return source, folder, plans

    def amendment_package(self, review_dir=None, version='v1'):
        import render_contract
        schema = json.loads((ROOT / 'assets/template-fields.json').read_text())
        fields = {key: item['example'] for key, item in schema['fields'].items() if '<N>' not in key}
        if review_dir:
            fields.pop('AMENDMENT_CHANGES')
        path = self.root / (version + '-unrelated-name.docx')
        render_contract.render(ROOT / 'assets/templates/amendment.docx', fields, path, review_dir=review_dir)
        sidecar = path.with_suffix('.docx.fields.json')
        handoff.register(self.state, [path, sidecar], 'draft_for_review', version, self.at,
                         group='amendment', document_kind='amendment')
        self.layout(path)
        return path, sidecar

    def test_received_review_requires_classification_of_own_docx_without_name_guessing(self):
        self.review_package()
        own = self.file('unrelated.docx', 'Проект документа')
        handoff.register(self.state, [own], 'draft_for_review', 'v1', self.at, group='own', document_kind='contract')
        data = self.data()
        next(item for item in data['documents'] if item['path'] == own.name).pop('document_kind')
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('document_kind' in issue for issue in result['issues']))
        self.assertEqual(own.read_text(), 'Проект документа')

    def test_received_review_rejects_manual_amendment_but_standalone_still_works(self):
        self.amendment_package()
        self.assertTrue(handoff.audit(self.state)['valid'])
        self.review_package(with_changes=True)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('требует сборки' in issue and '--review' in issue for issue in result['issues']))

    def test_bound_amendment_and_explicit_pdf_source_pass_without_changing_files(self):
        _, folder, _ = self.review_package(with_changes=True)
        docx, sidecar = self.amendment_package(folder)
        pdf = self.received_pdf('export-with-another-name.pdf', 'Our amendment with agreed corrections.')
        handoff.register(self.state, [pdf], 'draft_for_review', 'v1', self.at, group='amendment',
                         document_kind='amendment', source_docx=docx)
        paths = [self.state, docx, sidecar, pdf, *folder.iterdir()]
        before = {path: path.read_bytes() for path in paths}
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        record = next(item for item in self.data()['documents'] if item['path'] == pdf.name)
        self.assertEqual(record['source_docx'], {'path': docx.name, 'sha256': handoff.sha(docx)})

    def test_pdf_amendment_requires_explicit_current_source_and_matching_hash(self):
        _, folder, _ = self.review_package(with_changes=True)
        docx, _ = self.amendment_package(folder)
        pdf = self.received_pdf('export.pdf', 'Our amendment with agreed corrections.')
        handoff.register(self.state, [pdf], 'draft_for_review', 'v1', self.at, group='amendment', document_kind='amendment')
        self.assertTrue(any('--source-docx' in issue for issue in handoff.audit(self.state)['issues']))
        handoff.register(self.state, [pdf], 'draft_for_review', 'v1', self.at, group='amendment',
                         document_kind='amendment', source_docx=docx)
        state = self.data()
        next(item for item in state['documents'] if item['path'] == pdf.name)['source_docx']['sha256'] = '0' * 64
        handoff.save(self.state, state)
        self.assertTrue(any('SHA не совпадает' in issue for issue in handoff.audit(self.state)['issues']))

    def test_amendment_requires_current_registered_sidecar_and_detects_manual_document_change(self):
        _, folder, _ = self.review_package(with_changes=True)
        docx, sidecar = self.amendment_package(folder)
        initial = self.data()
        state = self.data()
        next(item for item in state['documents'] if item['path'] == sidecar.name)['current'] = False
        handoff.save(self.state, state)
        self.assertTrue(any('карту .fields.json' in issue for issue in handoff.audit(self.state)['issues']))
        handoff.save(self.state, initial)
        docx.write_bytes(docx.read_bytes() + b'human-change')
        before = docx.read_bytes()
        state = self.data()
        self.fixture_hash(state, docx)
        handoff.save(self.state, state)
        self.assertTrue(any('DOCX допсоглашения изменён' in issue for issue in handoff.audit(self.state)['issues']))
        self.assertEqual(docx.read_bytes(), before)

    def test_new_review_requires_new_amendment_preserving_old_delivery(self):
        import review
        _, folder, _ = self.review_package(with_changes=True)
        old, old_sidecar = self.amendment_package(folder)
        before = old.read_bytes(), old_sidecar.read_bytes()
        data = json.loads((folder / 'review-input.json').read_text())
        data['decisions'][0]['decision']['text'] = 'agree after written approval'
        input_path = self.file('review-v2-input.json', json.dumps(data))
        second = self.root / 'review-v2'
        review.build(input_path, second)
        handoff.register(self.state, [second / name for name in handoff.REVIEW_FILES], 'reviewed', 'v2', self.at, group='review')
        self.assertTrue(any('прежней сверкой' in issue for issue in handoff.audit(self.state)['issues']))
        self.amendment_package(second, 'v2')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual((old.read_bytes(), old_sidecar.read_bytes()), before)
        for entry in self.data()['documents']:
            if entry['path'] in (old.name, old_sidecar.name):
                self.assertFalse(entry['current'])
                self.assertEqual(entry['delivered_at'], self.at)

    def test_bound_amendment_keeps_review_check_after_received_source_is_archived(self):
        state = self.data()
        state['stage'] = 'closed'
        handoff.save(self.state, state)
        source, folder, _ = self.review_package(with_changes=True)
        self.amendment_package(folder)
        state = self.data()
        next(item for item in state['documents'] if item['path'] == source.name)['current'] = False
        handoff.save(self.state, state)
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        state = self.data()
        next(item for item in state['documents'] if item['path'].endswith('/review-input.json'))['current'] = False
        handoff.save(self.state, state)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('review-input.json' in issue for issue in result['issues']), result['issues'])

    def test_render_amendment_cli_uses_checked_review(self):
        _, folder, _ = self.review_package(with_changes=True)
        schema = json.loads((ROOT / 'assets/template-fields.json').read_text())
        fields = {key: item['example'] for key, item in schema['fields'].items() if '<N>' not in key and key != 'AMENDMENT_CHANGES'}
        field_file = self.file('fields.json', json.dumps(fields))
        output = self.root / 'cli.docx'
        command = [sys.executable, str(ROOT / 'scripts/render_contract.py'), '--template', 'amendment',
                   '--review', str(folder), '--fields', str(field_file), '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        sidecar = output.with_suffix('.docx.fields.json')
        handoff.register(self.state, [output, sidecar], 'draft_for_review', 'v1', self.at,
                         group='amendment', document_kind='amendment')
        self.layout(output)
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_registered_amendment_cannot_drop_review_provenance_after_source_is_archived(self):
        source, folder, _ = self.review_package(with_changes=True)
        _, sidecar = self.amendment_package(folder)
        saved = json.loads(sidecar.read_text())
        saved['review_source'] = None
        sidecar.write_text(json.dumps(saved))
        state = self.data()
        next(item for item in state['documents'] if item['path'] == source.name)['current'] = False
        next(item for item in state['documents'] if item['path'] == sidecar.name)['sha256'] = handoff.sha(sidecar)
        handoff.save(self.state, state)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('сохранённая в реестре связь' in issue for issue in result['issues']))

    def test_known_date_requires_a_registered_plan_but_unknown_date_does_not(self):
        self.assertTrue(handoff.audit(self.state)['valid'])
        self.scheduled_state()
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('локальный план' in issue for issue in result['issues']))
        files = self.plan_package()
        self.assertFalse(handoff.audit(self.state)['valid'])
        handoff.register(self.state, files, 'prepared', 'v1', self.at, group='plan')
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_handoff_cli_rejects_malformed_creative_and_related_containers_without_writes(self):
        original = self.data()
        for field, value in (('creative', []), ('publication', [])):
            with self.subTest(field=field, value=value):
                data = json.loads(json.dumps(original))
                data[field] = value
                message = field
                handoff.save(self.state, data)
                response = self.file('reply.txt', 'Документы пока не подготовлены.')
                before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/handoff.py'), 'check',
                                         '--state', str(self.state), '--response', str(response), '--record-issue'],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Ошибка: ' + message, result.stderr)
                self.assertIn('требуется объект', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_issued_plan_keeps_nested_document_references_as_plain_text(self):
        from markdown_it import MarkdownIt
        self.scheduled_state()
        historical = self.file('old-contract.docx', 'Исторический документ')
        unregistered = self.file('unregistered.docx', 'Незарегистрированный документ')
        missing = self.root / 'missing.docx'
        payload = (f'[старый договор](<{historical}>) [новый](<{unregistered}>) '
                   f'[отсутствующий](<{missing}>) <a href="{missing}">HTML</a>')
        data = self.data()
        data['schedule']['reporting_deadlines'][0].update(title=payload, source=payload)
        handoff.save(self.state, data)
        files = self.plan_package()
        tokens = MarkdownIt('commonmark').parse(files[1].read_text())
        all_tokens = [token for block in tokens for token in [block, *(block.children or [])]]
        self.assertFalse({token.type for token in all_tokens} & {'link_open', 'image', 'html_block', 'html_inline'})
        self.assertIn(payload, ''.join(token.content for token in all_tokens if token.type == 'text'))
        self.register_plan(files)
        self.issue_plan(files)

    def test_plan_package_must_include_readable_plan_and_calendar(self):
        self.scheduled_state()
        files = self.plan_package()
        handoff.register(self.state, files[:1], 'prepared', 'v1', self.at, group='plan')
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('plan.md и reminders.ics' in issue for issue in result['issues']))

    def test_current_plan_must_match_state_and_calendar_even_with_correct_file_hashes(self):
        self.scheduled_state()
        files = self.plan_package()
        handoff.register(self.state, files, 'prepared', 'v1', self.at, group='plan')
        data = self.data()
        data['schedule']['duration_hours'] = 49
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('План' in issue for issue in result['issues']))
        data['schedule']['duration_hours'] = 48
        files[2].write_text('BEGIN:VCALENDAR\nEND:VCALENDAR\n')
        self.fixture_hash(data, files[2])
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('ICS не соответствует' in issue for issue in result['issues']))

    def test_matching_plan_exports_with_tampered_warnings_or_controls_cannot_be_issued(self):
        self.scheduled_state()
        for field in ('warnings', 'deadline_controls'):
            with self.subTest(field=field):
                files = self.plan_package('tampered-' + field)
                plan = json.loads(files[0].read_text())
                if field == 'warnings':
                    self.assertTrue(plan[field])
                    plan[field] = []
                else:
                    self.assertEqual(plan[field], [])
                    plan[field] = [{'check': 'payment', 'date': '2026-11-08'}]
                # Все три файла согласованы между собой и зарегистрированы с честными SHA.
                files[0].write_text(json.dumps(plan))
                files[1].write_text(handoff.deal.markdown(plan))
                files[2].write_text(handoff.deal.ics(plan, handoff.deal.stamp(plan['generated_at'])))
                self.register_plan(files)
                response = self.file('reply-' + field + '.txt', f'[План](<{files[1]}>)')
                before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
                for record in (False, True):
                    result = handoff.audit(self.state, response, record_issue=record)
                    self.assertFalse(result['valid'])
                    self.assertTrue(any(f'План {field} не воспроизводится' in issue for issue in result['issues']),
                                    result['issues'])
                    if record:
                        self.assertFalse(result['plan_issue_recorded'])
                    self.assertNotIn('plan_issues', self.data())
                    self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_plan_markdown_tampered_before_registration_blocks_issue_without_writes(self):
        self.scheduled_state()
        old = self.plan_package('old-deadlines')
        self.change_plan_state(49, 2)
        for name in ('old-deadlines', 'arbitrary', 'manually-edited', 'crlf'):
            with self.subTest(content=name):
                files = self.plan_package('current-' + name)
                generated = files[1].read_bytes()
                if name == 'old-deadlines':
                    changed = old[1].read_bytes()
                elif name == 'arbitrary':
                    changed = 'Публиковать можно сразу, остальные действия не нужны.\n'.encode('utf-8')
                elif name == 'manually-edited':
                    changed = generated.replace('ещё не установлены'.encode('utf-8'), 'установлены'.encode('utf-8'))
                else:
                    changed = generated.replace(b'\n', b'\r\n')
                self.assertNotEqual(generated, changed)
                # Подмена до регистрации даёт честный SHA и архив, но неверный читаемый план.
                files[1].write_bytes(changed)
                self.register_plan(files)
                entry = self.registered()[files[1].resolve()]
                self.assertEqual(entry['sha256'], handoff.sha(files[1]))
                response = self.file('reply-' + name + '.txt', f'[План](<{files[1]}>)')
                before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
                for record in (False, True):
                    result = handoff.audit(self.state, response, record_issue=record)
                    self.assertFalse(result['valid'])
                    self.assertTrue(any('plan.md не соответствует' in issue for issue in result['issues']),
                                    result['issues'])
                    if record:
                        self.assertFalse(result['plan_issue_recorded'])
                    self.assertNotIn('plan_issues', self.data())
                    self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_inactive_deal_does_not_require_new_plan(self):
        self.scheduled_state()
        for stage in ('paused', 'cancelled', 'closed'):
            with self.subTest(stage=stage):
                data = self.data()
                data['stage'] = stage
                handoff.save(self.state, data)
                self.assertTrue(handoff.audit(self.state)['valid'])

    def test_superseding_preserves_prior_delivery_and_registers_pdf(self):
        old = self.file('old.docx')
        new = self.file('new.docx')
        pdf = self.file('new.pdf')
        handoff.register(self.state, [old], 'draft', 'v1', self.at, document_kind='contract')
        handoff.register(self.state, [new, pdf], 'draft', 'v2', '2026-11-02T12:00:00+03:00', [old], document_kind='contract')
        by_name = {item['path']: item for item in self.data()['documents']}
        self.assertFalse(by_name['old.docx']['current'])
        self.assertEqual(by_name['old.docx']['delivered_at'], self.at)
        self.assertTrue(by_name['new.pdf']['current'])
        self.assertEqual(by_name['new.pdf']['sha256'], handoff.sha(pdf))

    def test_registration_refuses_changed_original_without_mutating_state(self):
        doc = self.file('document.docx')
        handoff.register(self.state, [doc], 'draft', 'v1', self.at, document_kind='contract')
        before = self.state.read_bytes()
        doc.write_text('Ручная правка')
        with self.assertRaisesRegex(ValueError, 'изменён'):
            handoff.register(self.state, [doc], 'draft', 'v2', self.at)
        self.assertEqual(before, self.state.read_bytes())
        self.assertFalse(handoff.audit(self.state)['valid'])

    def test_unregistered_pdf_and_broken_links_block_handoff(self):
        pdf = self.file('invoice.pdf')
        response = self.file('reply.txt', f'[Счёт]({pdf})')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        handoff.register(self.state, [pdf], 'unsigned', 'v1', self.at, document_kind='payment')
        self.assertTrue(handoff.audit(self.state, response)['valid'])
        response.write_text(f'[Счёт]({self.root / "missing.pdf"})')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        response.write_text('[Счёт](invoice.pdf)')
        self.assertFalse(handoff.audit(self.state, response)['valid'])

    def test_reference_links_enforce_registration_version_and_document_integrity(self):
        initial_state = self.state.read_bytes()
        for syntax, link, label in (
                ('full', '[Счёт][документ]', 'документ'),
                ('collapsed', '[Счёт][]', 'Счёт'),
                ('shortcut', '[Счёт]', 'Счёт'),
                ('image', '![Счёт][документ]', 'документ')):
            with self.subTest(syntax=syntax):
                self.state.write_bytes(initial_state)
                old = self.file(f'{syntax}-invoice-v1.pdf', 'Счёт v1')
                new = self.file(f'{syntax}-invoice-v2.pdf', 'Счёт v2')
                response = self.file('reply.txt', f'{link}\n\n[{label}]: <{old}>')
                result = handoff.audit(self.state, response)
                self.assertFalse(result['valid'], result['issues'])
                self.assertTrue(any('не зарегистрирован' in issue and str(old) in issue
                                    for issue in result['issues']), result['issues'])
                response.write_text(f'{link}\n\n[{label}]: <{self.root / "missing.pdf"}>')
                result = handoff.audit(self.state, response)
                self.assertFalse(result['valid'], result['issues'])
                self.assertTrue(any('отсутствующий файл' in issue for issue in result['issues']))
                response.write_text(f'{link}\n\n[{label}]: <{old}>')

                handoff.register(self.state, [old], 'unsigned', 'v1', self.at, document_kind='payment')
                result = handoff.audit(self.state, response)
                self.assertTrue(result['valid'], result['issues'])
                handoff.register(self.state, [new], 'unsigned', 'v2', self.at, [old], document_kind='payment')
                result = handoff.audit(self.state, response)
                self.assertFalse(result['valid'], result['issues'])
                self.assertTrue(any('заменённую версию' in issue and str(old) in issue
                                    for issue in result['issues']), result['issues'])
                result = handoff.audit(self.state, response, historical=[old])
                self.assertTrue(result['valid'], result['issues'])

                response.write_text(f'{link}\n\n[{label}]: <{new}>')
                result = handoff.audit(self.state, response)
                self.assertTrue(result['valid'], result['issues'])
                new.write_text('Изменённый после регистрации счёт')
                before = self.state.read_bytes()
                result = handoff.audit(self.state, response)
                self.assertFalse(result['valid'], result['issues'])
                self.assertTrue(any('изменён' in issue and str(new) in issue
                                    for issue in result['issues']), result['issues'])
                self.assertEqual(before, self.state.read_bytes())

    def test_native_file_citation_requires_the_same_source_registration(self):
        source = self.file('полученная квитанция.pdf', 'Подтверждение перевода')
        response = self.file('reply.txt', f':codex-file-citation{{path="{source}" purpose="source"}}')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        handoff.register(self.state, [source], 'received', 'v1', self.at, group='source', document_kind='payment')
        self.assertTrue(handoff.audit(self.state, response)['valid'])
        source.write_text('Другой исходный документ')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        response.write_text('[Счёт](file:///definitely-missing-invoice.pdf)')
        self.assertFalse(handoff.audit(self.state, response)['valid'])

    def test_raw_html_response_is_rejected_without_changing_files(self):
        document = self.file('invoice.pdf')
        handoff.register(self.state, [document], 'unsigned', 'v1', self.at, document_kind='payment')
        response = self.file('reply.txt')

        def snapshot():
            return {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in self.root.rglob('*') if path.is_file()}

        for html in (f'<a href="{document}">Счёт</a>', f'<img src="{document}" alt="Счёт">'):
            for text in (f'Готово: {html}', f'<div>\n{html}\n</div>'):
                for record in (False, True):
                    with self.subTest(text=text, record=record):
                        response.write_text(text)
                        before = snapshot()
                        result = handoff.audit(self.state, response, record_issue=record)
                        self.assertFalse(result['valid'], result['issues'])
                        self.assertTrue(any('HTML в ответе не поддерживается' in issue
                                            for issue in result['issues']), result['issues'])
                        self.assertEqual(before, snapshot())

        ignored_html = f'<a href="{self.root / "missing.pdf"}">Пример</a>'
        for text in (f'[Счёт](<{document}>)',
                     f'[Счёт](<{document}>)\n\n`{ignored_html}`',
                     f'[Счёт](<{document}>)\n\n```html\n<div>\n{ignored_html}\n</div>\n```'):
            with self.subTest(markdown=text):
                response.write_text(text)
                before = snapshot()
                result = handoff.audit(self.state, response)
                self.assertTrue(result['valid'], result['issues'])
                self.assertEqual(before, snapshot())

    def test_directory_link_cannot_deliver_unregistered_or_superseded_files(self):
        folder = self.root / 'documents'
        folder.mkdir()
        old = folder / 'invoice-v1.pdf'
        old.write_text('Счёт v1')
        response = self.file('reply.txt', f'[Документы]({folder})')
        result = handoff.audit(self.state, response)
        self.assertFalse(result['valid'], result['issues'])
        self.assertTrue(any('конкретный файл' in issue for issue in result['issues']))
        handoff.register(self.state, [old], 'unsigned', 'v1', self.at, document_kind='payment')
        new = folder / 'invoice-v2.pdf'
        new.write_text('Счёт v2')
        handoff.register(self.state, [new], 'unsigned', 'v2', self.at, [old], document_kind='payment')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        response.write_text(f'[Счёт]({new})')
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])

    def test_missing_input_does_not_partially_register_other_files(self):
        doc = self.file('first.docx')
        before = self.state.read_bytes()
        with self.assertRaises(ValueError):
            handoff.register(self.state, [doc, self.root / 'missing.pdf'], 'draft', 'v1', self.at, document_kind='contract')
        self.assertEqual(before, self.state.read_bytes())

    def test_duplicate_files_are_rejected_before_registration(self):
        doc = self.file('first.docx')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'повторно'):
            handoff.register(self.state, [doc, doc], 'draft', 'v1', self.at, document_kind='contract')
        self.assertEqual(before, self.state.read_bytes())

    def test_parentheses_spaces_and_encoded_symbols_in_local_links(self):
        pdf = self.file('Счёт (1) #2.pdf')
        handoff.register(self.state, [pdf], 'unsigned', 'v1', self.at, document_kind='payment')
        response = self.file('reply.txt', f'[Счёт](<{str(pdf).replace("#", "%23")}>)')
        self.assertTrue(handoff.audit(self.state, response)['valid'])

    def test_superseded_link_requires_explicit_historical_use(self):
        old = self.file('old.docx')
        new = self.file('new.docx')
        handoff.register(self.state, [old], 'draft', 'v1', self.at, document_kind='contract')
        handoff.register(self.state, [new], 'draft', 'v2', self.at, [old], document_kind='contract')
        self.layout(new)
        response = self.file('reply.txt', f'[Прежняя версия]({old})')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        self.assertTrue(handoff.audit(self.state, response, [old])['valid'])

    def test_empty_initial_state_can_be_handed_off_without_claiming_readiness(self):
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'])
        self.assertEqual(result['checks']['erid']['status'], 'pending')
        self.assertNotIn('ready', result)

    def test_history_cannot_call_text_hash_a_full_creative_hash(self):
        text = self.file('post.txt', 'Точный пост https://example.org')
        links = self.file('links.json', '["https://example.org"]')
        handoff.deal.import_creative(self.state, text, links, handoff.deal.stamp(self.at))
        data = self.data()
        data['history'].append({'facts': {'approved_creative_sha256': handoff.sha(text)}})
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertIn('полной', result['issues'][0])
        data['history'][-1]['facts'] = {'text_sha256': handoff.sha(text), 'approved_creative_sha256': handoff.deal.digest(data['creative'])}
        handoff.save(self.state, data)
        self.assertTrue(handoff.audit(self.state)['valid'])
        # Новый импорт не делает прежнее корректное подтверждение ложным задним числом.
        text.write_text('Новая согласуемая редакция https://example.org')
        handoff.deal.import_creative(self.state, text, links, handoff.deal.stamp(self.at))
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_group_replaces_a_plan_even_when_paths_and_file_names_differ(self):
        old = self.file('old-plan.md')
        old_calendar = self.file('old-calendar.ics')
        new = self.file('new-plan.md')
        contract = self.file('contract.docx')
        handoff.register(self.state, [contract], 'draft', 'v1', self.at, group='contract', document_kind='contract')
        self.layout(contract)
        handoff.register(self.state, [old, old_calendar], 'draft', 'v1', self.at, group='plan')
        handoff.register(self.state, [new], 'draft', 'v2', '2026-11-02T12:00:00+03:00', group='plan')
        docs = {item['path']: item for item in self.data()['documents']}
        self.assertFalse(docs[old.name]['current'])
        self.assertFalse(docs[old_calendar.name]['current'])
        self.assertEqual(docs[old.name]['delivered_at'], self.at)
        self.assertTrue(docs[contract.name]['current'])
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_changed_facts_do_not_automatically_archive_advice_or_letters(self):
        note = self.file('review.md', 'Срок оплаты нужно согласовать.')
        letter = self.file('letter.txt', 'Предлагаем согласовать срок оплаты.')
        data = self.data()
        data['recommendations'] = [{'at': self.at, 'text': 'Согласовать срок оплаты'}]
        data['history'].append({'at': self.at, 'text': 'Пользователь: ничего не отправлять.'})
        handoff.save(self.state, data)
        handoff.register(self.state, [note], 'reviewed', 'v1', self.at, group='analysis')
        handoff.register(self.state, [letter], 'draft', 'v1', self.at, group='correspondence')
        data = self.data()
        data['policy']['require_prepayment'] = False
        handoff.save(self.state, data)
        self.assertTrue(handoff.audit(self.state)['valid'])
        later = '2026-11-02T12:00:00+03:00'
        handoff.refresh(self.state, later)
        after = self.data()
        self.assertEqual(after, data)
        self.assertTrue(all(item['current'] for item in after['documents']))
        handoff.register(self.state, [letter], 'draft', 'v1', later, group='correspondence')
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_manual_multiple_current_versions_of_one_group_are_rejected(self):
        old = self.file('old-plan.md')
        new = self.file('new-plan.md')
        handoff.register(self.state, [old], 'draft', 'v1', self.at, group='plan')
        handoff.register(self.state, [new], 'draft', 'v2', self.at, group='plan')
        data = self.data()
        data['documents'][0]['current'] = True
        handoff.save(self.state, data)
        self.assertFalse(handoff.audit(self.state)['valid'])

    def test_all_pdf_docx_require_classification_before_mutating_state(self):
        for status in ('received', 'draft_for_review'):
            for suffix in ('.pdf', '.docx'):
                path = self.file('source' + suffix)
                before = self.state.read_bytes()
                with self.subTest(status=status, suffix=suffix), self.assertRaisesRegex(ValueError, 'document_kind'):
                    handoff.register(self.state, [path], status, 'v1', self.at, group='source')
                self.assertEqual(self.state.read_bytes(), before)
        path = self.file('own-draft.docx')
        handoff.register(self.state, [path], 'draft_for_review', 'v1', self.at, group='contract', document_kind='contract')
        self.layout(path)
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_own_pdf_docx_without_kind_cannot_bypass_audit_or_require_creative_report_for_other_kinds(self):
        for suffix in ('.pdf', '.docx'):
            path = self.file('own' + suffix)
            handoff.register(self.state, [path], 'draft', 'v1', self.at, group=suffix, document_kind='contract')
            if suffix == '.docx':
                self.layout(path)
            for kind in ('contract', 'payment', 'other'):
                with self.subTest(suffix=suffix, kind=kind):
                    reason = 'Описание продукта без договорных условий.' if kind == 'other' else None
                    handoff.register(self.state, [path], 'draft', 'v1', self.at,
                                     group=suffix, document_kind=kind, kind_reason=reason)
                    # Неизменная повторная регистрация сохраняет классификацию.
                    handoff.register(self.state, [path], 'draft', 'v1', self.at, group=suffix)
                    self.assertTrue(handoff.audit(self.state)['valid'])
            data = self.data()
            record = next(item for item in data['documents'] if item['path'] == path.name)
            record.pop('document_kind')
            record.pop('kind_reason')
            handoff.save(self.state, data)
            before = self.state.read_bytes()
            result = handoff.audit(self.state)
            self.assertFalse(result['valid'])
            self.assertTrue(any('document_kind' in issue for issue in result['issues']))
            self.assertEqual(before, self.state.read_bytes())
            record['current'] = False
            handoff.save(self.state, data)

    def test_other_needs_reason_and_payment_does_not_require_contract_review(self):
        path = self.file('received.pdf', 'Подтверждение поступления оплаты')
        for kind, reason in [('other', None), ('other', ' '), ('invalid', None), ('payment', 'Причина лишняя')]:
            before = self.state.read_bytes()
            with self.subTest(kind=kind, reason=reason), self.assertRaises(ValueError):
                handoff.register(self.state, [path], 'received', 'v1', self.at, group='source', document_kind=kind, kind_reason=reason)
            self.assertEqual(self.state.read_bytes(), before)
        handoff.register(self.state, [path], 'received', 'v1', self.at, group='source', document_kind='payment')
        self.assertTrue(handoff.audit(self.state)['valid'])
        other = self.file('product-description.pdf', 'Описание продукта без условий сделки')
        handoff.register(self.state, [other], 'received', 'v1', self.at, group='product', document_kind='other',
                         kind_reason='Информационное описание продукта без договорных условий и платёжных сведений.')
        self.assertTrue(handoff.audit(self.state)['valid'])
        self.assertEqual(self.data()['documents'][-1]['document_kind'], 'other')

    def test_manually_missing_classification_or_other_reason_blocks_audit(self):
        path = self.file('received.pdf')
        handoff.register(self.state, [path], 'received', 'v1', self.at, group='source', document_kind='payment')
        data = self.data()
        data['documents'][0].pop('document_kind')
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('document_kind' in issue for issue in result['issues']))
        data['documents'][0]['document_kind'] = 'other'
        handoff.save(self.state, data)
        self.assertTrue(any('kind_reason' in issue for issue in handoff.audit(self.state)['issues']))

    def test_contract_review_cannot_be_replaced_by_freeform_note_or_just_json(self):
        self.scheduled_state()
        source = self.received_pdf()
        handoff.register(self.state, [source], 'received', 'v1', self.at, group='source', document_kind='contract')
        handoff.register(self.state, self.plan_package(), 'prepared', 'v1', self.at, group='plan')
        note = self.file('freeform.md', 'Всё проверено, договор подходит.')
        fake_input = self.file('fake-review.json', '{"reviewed":true}')
        handoff.register(self.state, [note, fake_input], 'reviewed', 'v1', self.at, group='review')
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('Произвольная заметка' in issue for issue in result['issues']))

    def test_real_registered_review_passes_without_mutating_state_or_sources(self):
        source, folder, plans = self.review_package()
        paths = [self.state, source, *folder.iterdir(), *plans]
        before = {path: path.read_bytes() for path in paths}
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_each_review_file_must_be_current_registered_and_same_package(self):
        _, folder, _ = self.review_package()
        original = self.data()
        for name in handoff.REVIEW_FILES:
            data = json.loads(json.dumps(original))
            entry = next(item for item in data['documents'] if item['path'] == f'review-v1/{name}')
            entry['current'] = False
            handoff.save(self.state, data)
            with self.subTest(name=name):
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('пакет review' in issue or 'Пакет review' in issue for issue in result['issues']))
        handoff.save(self.state, original)
        table = folder / 'terms-review.md'
        table.write_text('Ручная заметка вместо таблицы')
        data = self.data()
        self.fixture_hash(data, table)
        handoff.save(self.state, data)
        self.assertTrue(any('Изменён сохранённый файл' in issue for issue in handoff.audit(self.state)['issues']))

    def test_uncovered_received_contractual_source_blocks_existing_valid_review(self):
        self.review_package()
        attachment = self.received_pdf('new-terms.pdf', 'New placement terms.')
        handoff.register(self.state, [attachment], 'received', 'v1', self.at, group='new-source', document_kind='placement_terms')
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('не охватывает' in issue and str(attachment) in issue for issue in result['issues']))

    def test_identical_source_copy_is_covered_by_sha_but_original_stays_registered(self):
        text = 'The parties agree on advertising services.'
        self.import_post(text)
        source, _, _ = self.review_package()
        copied = self.root / 'renamed.pdf'
        copied.write_bytes(source.read_bytes())
        handoff.register(self.state, [copied], 'received', 'v1', self.at, group='copy', document_kind='creative')
        report = self.link_report(copied)
        self.creative_review(report, [{'page': 1, 'quote': text}])
        handoff.register(self.state, [report], 'checked', 'v1', self.at, group='links')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        data = self.data()
        data['documents'] = [entry for entry in data['documents'] if entry['path'] != source.name]
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('исходник не зарегистрирован' in issue for issue in result['issues']))

    def test_review_handoff_rechecks_plan_contents(self):
        _, _, plans = self.review_package()
        data = self.data()
        plan = json.loads(plans[0].read_text())
        plan['events'][0]['title'] = 'Подменённое действие'
        plans[0].write_text(json.dumps(plan))
        next(item for item in data['documents'] if item['path'] == 'plan-v1/plan.json')['sha256'] = handoff.sha(plans[0])
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('План' in issue or 'план' in issue for issue in result['issues']))

    def test_archived_received_sources_do_not_require_new_review_or_classification(self):
        old = self.received_pdf()
        handoff.register(self.state, [old], 'received', 'v1', self.at, group='source', document_kind='contract')
        receipt = self.file('receipt.pdf', 'Подтверждение платежа')
        handoff.register(self.state, [receipt], 'received', 'v2', '2026-11-02T12:00:00+03:00',
                         group='source', document_kind='payment')
        data = self.data()
        old_entry = next(item for item in data['documents'] if item['path'] == old.name)
        old_entry.pop('document_kind')
        handoff.save(self.state, data)
        before = old.read_bytes(), self.state.read_bytes()
        self.assertTrue(handoff.audit(self.state)['valid'])
        self.assertEqual(before, (old.read_bytes(), self.state.read_bytes()))
        self.assertFalse(old_entry['current'])
        self.assertEqual(old_entry['delivered_at'], self.at)

    def test_register_cli_persists_classification_and_reason(self):
        source = self.file('source.pdf', 'Справочная информация')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/handoff.py'), 'register', '--state', str(self.state),
                                 '--files', str(source), '--status', 'received', '--version', 'v1', '--at', self.at,
                                 '--group', 'source', '--document-kind', 'other', '--kind-reason',
                                 'Справочный материал без договорных условий.'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.data()['documents'][0]['document_kind'], 'other')
        self.assertEqual(self.data()['documents'][0]['kind_reason'], 'Справочный материал без договорных условий.')

    def test_received_status_suffixes_cannot_bypass_classification_or_review(self):
        for index, status in enumerate(('received_unsigned', 'received_customer_signed', ' RECEIVED ')):
            path = self.received_pdf(f'received-{index}.pdf')
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'document_kind'):
                handoff.register(self.state, [path], status, 'v1', self.at, group=f'source-{index}')
            handoff.register(self.state, [path], status, 'v1', self.at, group=f'source-{index}', document_kind='contract')
            result = handoff.audit(self.state)
            self.assertFalse(result['valid'])
            self.assertTrue(any('пакет review' in issue for issue in result['issues']))

    def test_docx_received_source_is_covered_only_by_verified_pdf_original_link(self):
        import review
        _, folder, _ = self.review_package()
        original = self.root / 'received.docx'
        with ZipFile(original, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body><w:p><w:r><w:t>The parties agree on advertising services.</w:t></w:r></w:p></w:body></w:document>')
        def convert(source, destination, soffice=None):
            with ZipFile(source) as archive:
                text = review.word_text(review.ElementTree.fromstring(archive.read('word/document.xml'))).strip()
            return self.received_pdf(str(destination / (source.stem + '.pdf')), text)
        conversion = patch.object(review.render_docx, 'convert_to_pdf', side_effect=convert)
        converter = conversion.start()
        self.addCleanup(conversion.stop)
        handoff.register(self.state, [original], 'received', 'v1', self.at, group='docx-source', document_kind='contract')
        self.assertTrue(any('не охватывает' in issue for issue in handoff.audit(self.state)['issues']))
        decisions = json.loads((folder / 'review-input.json').read_text())
        decisions['documents'][0]['original_path'] = original.name
        source = self.file('decisions-v2.json', json.dumps(decisions))
        second = self.root / 'review-v2'
        review.build(source, second, soffice='/chosen/soffice')
        handoff.register(self.state, [second / name for name in handoff.REVIEW_FILES], 'reviewed', 'v2', self.at, group='review')
        result = handoff.audit(self.state, soffice='/chosen/soffice')
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(converter.call_count, 2)
        self.assertTrue(all(call.args[2] == '/chosen/soffice' for call in converter.call_args_list))
        # Даже согласованные SHA исходника и реестра не доказывают происхождение PDF.
        with ZipFile(original) as archive:
            xml = archive.read('word/document.xml')
        with ZipFile(original, 'w') as archive:
            archive.writestr('word/document.xml', xml.replace(b'agree on', b'disagree on'))
        manifest_path = second / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['sources'][0]['original']['sha256'] = handoff.sha(original)
        manifest_path.write_text(json.dumps(manifest))
        state = self.data()
        for path in (original, manifest_path):
            self.fixture_hash(state, path)
        handoff.save(self.state, state)
        result = handoff.audit(self.state, soffice='/chosen/soffice')
        self.assertFalse(result['valid'])
        self.assertTrue(any('original_path' in issue and 'PDF страницы 1' in issue for issue in result['issues']), result['issues'])
        original.write_bytes(original.read_bytes() + b'changed')
        self.assertFalse(handoff.audit(self.state)['valid'])

    def test_unknown_publication_allows_review_without_a_plan(self):
        self.review_package()
        state = self.data()
        state['schedule']['publication_at'] = None
        for entry in state['documents']:
            if entry['group'] == 'plan':
                entry['current'] = False
        handoff.save(self.state, state)
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertIsNone(self.data()['schedule']['publication_at'])
        state = self.data()
        state['schedule']['publication_at'] = '2026-11-09T09:00:00+03:00'
        handoff.save(self.state, state)
        self.assertFalse(handoff.audit(self.state)['valid'])


class ContractSignatureRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'deal-state.json'
        self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
        self.at = '2026-11-01T12:00:00+03:00'
        self.later = '2026-11-02T12:00:00+03:00'
        self.proof = {'status': 'confirmed', 'source': 'Подписанный пакет v1',
                      'checked_at': self.at, 'signatures': ['заказчик', 'исполнитель']}

    def document(self, name):
        path = self.root / name
        path.write_text(name)
        return path

    def confirm_contract(self):
        state = handoff.deal.read_json(self.state)
        state['checks']['contract'] = dict(self.proof)
        handoff.save(self.state, state)

    def test_replacing_each_contract_kind_invalidates_signature_and_preserves_proof(self):
        for kind in sorted(handoff.CONTRACT_KINDS):
            with self.subTest(kind=kind):
                old = self.document(f'{kind}-v1.pdf')
                new = self.document(f'{kind}-v2.pdf')
                handoff.register(self.state, [old], 'received', 'v1', self.at,
                                 group=kind, document_kind=kind)
                self.confirm_contract()
                handoff.register(self.state, [new], 'received', 'v2', self.later,
                                 group=kind, document_kind=kind)
                state = handoff.deal.read_json(self.state)
                self.assertEqual(state['checks']['contract']['status'], 'pending')
                self.assertIn('contract', handoff.deal.readiness_issues(state, handoff.deal.stamp(self.later)))
                history = state['history'][-1]
                self.assertEqual(history['invalidated_checks'], {'contract': self.proof})
                self.assertEqual(history['previous_contract_documents'][old.name],
                                 {'sha256': handoff.sha(old), 'document_kind': kind})
                self.assertNotIn(old.name, history['contract_documents'])
                self.assertEqual(history['contract_documents'][new.name],
                                 {'sha256': handoff.sha(new), 'document_kind': kind})
                by_path = {item['path']: item for item in state['documents']}
                self.assertFalse(by_path[old.name]['current'])
                self.assertTrue(by_path[new.name]['current'])
                self.assertEqual(handoff.archive_file(self.root, by_path[old.name]).read_bytes(), old.read_bytes())

    def test_explicit_replacement_across_groups_invalidates_signature(self):
        old = self.document('received-contract.pdf')
        new = self.document('revised-contract.pdf')
        handoff.register(self.state, [old], 'received', 'v1', self.at,
                         group='received-contract', document_kind='contract')
        self.confirm_contract()
        handoff.register(self.state, [new], 'draft', 'v1', self.later, [old],
                         group='own-contract', document_kind='contract')
        state = handoff.deal.read_json(self.state)
        self.assertEqual(state['checks']['contract']['status'], 'pending')
        self.assertEqual(state['history'][-1]['invalidated_checks'], {'contract': self.proof})

    def test_unchanged_registration_and_unrelated_files_preserve_signature(self):
        contract = self.document('contract.pdf')
        invoice = self.document('invoice.pdf')
        handoff.register(self.state, [contract], 'received', 'v1', self.at,
                         group='contract', document_kind='contract')
        self.confirm_contract()
        for version in ('v1', 'v2'):
            handoff.register(self.state, [contract], 'received', version, self.later,
                             group='contract', document_kind='contract')
        handoff.register(self.state, [invoice], 'draft', 'v1', self.later,
                         group='payment', document_kind='payment')
        revised_invoice = self.document('invoice-v2.pdf')
        handoff.register(self.state, [revised_invoice], 'draft', 'v2', self.later,
                         group='payment', document_kind='payment')
        state = handoff.deal.read_json(self.state)
        self.assertEqual(state['checks']['contract'], self.proof)
        self.assertEqual(state['history'], [])
        self.assertNotIn('contract', handoff.deal.readiness_issues(state, handoff.deal.stamp(self.later)))

    def test_adding_contractual_file_in_new_group_or_same_version_invalidates_signature(self):
        for new_group in (False, True):
            with self.subTest(new_group=new_group):
                group = f'contract-{new_group}'
                contract = self.document(group + '.pdf')
                addition = self.document(f'amendment-{new_group}.pdf')
                handoff.register(self.state, [contract], 'received', 'v1', self.at,
                                 group=group, document_kind='contract')
                self.confirm_contract()
                handoff.register(self.state, [addition], 'received', 'v1', self.later,
                                 group='amendment' if new_group else group, document_kind='amendment')
                state = handoff.deal.read_json(self.state)
                self.assertEqual(state['checks']['contract']['status'], 'pending')
                self.assertIn('contract', handoff.deal.readiness_issues(state, handoff.deal.stamp(self.later)))
                history = state['history'][-1]
                self.assertEqual(history['invalidated_checks'], {'contract': self.proof})
                self.assertNotIn(addition.name, history['previous_contract_documents'])
                self.assertIn(contract.name, history['contract_documents'])
                self.assertEqual(history['contract_documents'][addition.name],
                                 {'sha256': handoff.sha(addition), 'document_kind': 'amendment'})
                by_path = {item['path']: item for item in state['documents']}
                self.assertTrue(by_path[contract.name]['current'])
                self.assertTrue(by_path[addition.name]['current'])

    def test_reclassification_changes_contractual_set_and_invalidates_signature(self):
        document = self.document('reclassified.pdf')
        handoff.register(self.state, [document], 'draft', 'v1', self.at,
                         group='contract', document_kind='payment')
        for kind in ('contract', 'amendment', 'payment'):
            with self.subTest(kind=kind):
                self.confirm_contract()
                handoff.register(self.state, [document], 'draft', 'v1', self.later,
                                 group='contract', document_kind=kind)
                state = handoff.deal.read_json(self.state)
                self.assertEqual(state['checks']['contract']['status'], 'pending')
                history = state['history'][-1]
                self.assertEqual(history['invalidated_checks'], {'contract': self.proof})
                self.assertNotEqual(history['previous_contract_documents'], history['contract_documents'])
                if kind in handoff.CONTRACT_KINDS:
                    self.assertEqual(history['contract_documents'][document.name]['document_kind'], kind)
                else:
                    self.assertNotIn(document.name, history['contract_documents'])

    def test_replacing_only_fields_map_preserves_signature_of_unchanged_document(self):
        contract = self.document('contract.docx')
        old_map = self.document('contract.docx.fields.json')
        new_map = self.document('contract-v2.fields.json')
        old_map.write_text('{}')
        new_map.write_text('{"note": "Уточнена карта"}')
        handoff.register(self.state, [contract, old_map], 'draft', 'v1', self.at,
                         group='contract', document_kind='contract')
        self.confirm_contract()
        handoff.register(self.state, [contract, new_map], 'draft', 'v2', self.later,
                         group='contract', document_kind='contract')
        state = handoff.deal.read_json(self.state)
        self.assertEqual(state['checks']['contract'], self.proof)
        self.assertEqual(state['history'], [])
        by_path = {item['path']: item for item in state['documents']}
        self.assertFalse(by_path[old_map.name]['current'])
        self.assertTrue(by_path[new_map.name]['current'])
        self.assertTrue(by_path[contract.name]['current'])

    def test_already_historical_replacement_preserves_new_confirmation(self):
        old = self.document('old-contract.pdf')
        current = self.document('current-contract.pdf')
        next_document = self.document('invoice.pdf')
        handoff.register(self.state, [old], 'received', 'v1', self.at,
                         group='contract', document_kind='contract')
        handoff.register(self.state, [current], 'received', 'v2', self.later,
                         group='contract', document_kind='contract')
        self.confirm_contract()
        handoff.register(self.state, [next_document], 'draft', 'v1', self.later, [old],
                         group='payment', document_kind='payment')
        state = handoff.deal.read_json(self.state)
        self.assertEqual(state['checks']['contract'], self.proof)
        self.assertEqual(state['history'], [])


class ReceivedDocumentStatusTests(unittest.TestCase):
    received_pdf = HandoffTests.received_pdf

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'deal-state.json'
        self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
        self.at = '2026-11-01T12:00:00+03:00'
        self.later = '2026-11-02T12:00:00+03:00'
        self.original = self.received_pdf()
        handoff.register(self.state, [self.original], 'received', 'v1', self.at,
                         group='source', document_kind='contract')
        state = handoff.deal.read_json(self.state)
        self.proof = {'status': 'confirmed', 'source': 'Подписи полученного договора', 'checked_at': self.at}
        state['checks']['contract'] = dict(self.proof)
        handoff.save(self.state, state)

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def assert_review_required(self):
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('пакет review' in issue for issue in result['issues']), result['issues'])

    def test_received_origin_cannot_be_removed_or_partially_register_a_package(self):
        additional = self.received_pdf('new.pdf', 'A new document in the same registration batch.')
        for previous_status in ('received', 'received_unsigned', ' RECEIVED_CUSTOMER_SIGNED '):
            handoff.register(self.state, [self.original], previous_status, 'v1', self.at,
                             group='source', document_kind='contract')
            for status in ('reviewed', 'signed', 'draft'):
                with self.subTest(previous=previous_status, new=status):
                    before = self.snapshot()
                    with self.assertRaisesRegex(ValueError, 'сохраните статус received'):
                        handoff.register(self.state, [additional, self.original], status, 'v2', self.later,
                                         group='source', document_kind='contract')
                    self.assertEqual(self.snapshot(), before)
                    state = handoff.deal.read_json(self.state)
                    self.assertEqual(state['checks']['contract'], self.proof)
                    self.assertEqual(state['documents'][0]['status'], previous_status)
                    self.assert_review_required()
                    self.assertEqual(self.snapshot(), before)

    def test_exact_received_copy_cannot_bypass_review_or_partially_register_files(self):
        copied = self.root / 'working-copy.pdf'
        copied.write_bytes(self.original.read_bytes())
        additional = self.received_pdf('new.pdf', 'A different document in the same batch.')
        self.assert_review_required()
        for status in ('draft', 'reviewed', 'signed'):
            with self.subTest(status=status):
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'точная копия.*сохраните статус received'):
                    handoff.register(self.state, [additional, copied], status, 'v2', self.later,
                                     group='source', document_kind='contract')
                self.assertEqual(self.snapshot(), before)
                self.assert_review_required()
                self.assertEqual(self.snapshot(), before)
        handoff.register(self.state, [copied], 'received', 'v2', self.later,
                         group='source', document_kind='contract')
        state = handoff.deal.read_json(self.state)
        by_path = {item['path']: item for item in state['documents']}
        self.assertFalse(by_path[self.original.name]['current'])
        self.assertTrue(by_path[copied.name]['current'])
        self.assertEqual(by_path[copied.name]['status'], 'received')
        self.assert_review_required()

    def test_changed_own_revision_is_allowed_but_historical_received_copy_keeps_origin(self):
        revised = self.received_pdf('own-revision.pdf', 'Our revised advertising contract.')
        handoff.register(self.state, [revised], 'draft', 'v2', self.later,
                         group='source', document_kind='contract')
        state = handoff.deal.read_json(self.state)
        by_path = {item['path']: item for item in state['documents']}
        self.assertFalse(by_path[self.original.name]['current'])
        self.assertEqual(by_path[revised.name]['status'], 'draft')
        self.assertNotEqual(by_path[self.original.name]['sha256'], by_path[revised.name]['sha256'])
        self.assertTrue(handoff.audit(self.state)['valid'])
        copied = self.root / 'historical-copy.pdf'
        copied.write_bytes(self.original.read_bytes())
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'точная копия.*сохраните статус received'):
            handoff.register(self.state, [copied], 'draft', 'v3', self.later,
                             group='source', document_kind='contract')
        self.assertEqual(self.snapshot(), before)
        handoff.register(self.state, [copied], 'received_customer_signed', 'v3', self.later,
                         group='source', document_kind='contract')
        self.assert_review_required()

    def test_received_reregistration_and_status_refinement_keep_origin_and_review(self):
        original_bytes = self.original.read_bytes()
        initial = handoff.deal.read_json(self.state)['documents'][0]
        archive = handoff.archive_file(self.root, initial)
        for status in ('received', 'received_unsigned', 'received_customer_signed', ' RECEIVED '):
            with self.subTest(status=status):
                handoff.register(self.state, [self.original], status, 'v1', self.later,
                                 group='source', document_kind='contract')
                state = handoff.deal.read_json(self.state)
                self.assertEqual(state['documents'][0]['status'], status)
                self.assertTrue(state['documents'][0]['current'])
                self.assertEqual(state['checks']['contract'], self.proof)
                self.assertEqual(state['history'], [])
                self.assertEqual(self.original.read_bytes(), original_bytes)
                self.assertEqual(archive.read_bytes(), original_bytes)
                self.assert_review_required()


class HandoffLinkCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / 'учебный комплект 2026-10-18' / 'счёт.pdf'
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b'local artifact')
        # Команда не должна читать даже находящийся рядом файл состояния.
        (self.root / 'deal-state.json').write_text('не JSON')
        self.response = self.root / 'reply.txt'

    def run_check(self):
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/handoff.py'),
            'check-links', '--response', str(self.response)], cwd=self.root, capture_output=True, text=True)
        after = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after, 'Проверка ссылок не должна менять состояние, ответ или документы')
        return result, json.loads(result.stdout)

    def test_unregistered_accounting_files_are_checked_without_deal_state(self):
        self.response.write_text(f'Учебный [счёт](<{self.artifact}>) и [справка](https://example.com/guide).')
        result, data = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data['valid'])
        self.assertEqual(data['local_links'], [str(self.artifact.resolve())])

    def test_retyped_path_missing_year_is_rejected_without_mutation(self):
        wrong = str(self.artifact).replace('2026-10-18', '10-18')
        self.response.write_text(f'Учебный [счёт](<{wrong}>).')
        result, data = self.run_check()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(data['valid'])
        self.assertTrue(any(wrong in issue for issue in data['issues']))

    def test_reference_links_preserve_paths_titles_and_missing_file_checks(self):
        for use in ('[Счёт][документ]', '[документ][]', '[документ]'):
            with self.subTest(use=use):
                self.response.write_text(f'{use}\n\n[документ]: <{self.artifact}> "Счёт за размещение"\n')
                result, data = self.run_check()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data['local_links'], [str(self.artifact.resolve())])
                missing = self.artifact.with_name('нет такого счёта.pdf')
                self.response.write_text(f'{use}\n\n[документ]: <{missing}>\n  "Счёт за размещение"\n')
                result, data = self.run_check()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(any('отсутствующий файл' in issue for issue in data['issues']))

    def test_references_handle_labels_uri_errors_line_suffix_and_images(self):
        encoded = str(self.artifact).replace(' ', '%20')
        for text in (
            f'[Счёт][ДОКУМЕНТ]\n\n[документ]: <{encoded}:12> \'Заголовок\'',
            f'[документ]\n\n[документ]: <{self.artifact}:12> (Заголовок)',
            f'![Превью][doc]\n\n[doc]: <{self.artifact}> "Превью"',
            f'![Превью](<{self.artifact}>)'):
            with self.subTest(text=text):
                self.response.write_text(text)
                result, data = self.run_check()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data['local_links'], [str(self.artifact.resolve())])
        for target, diagnostic in (('relative.pdf', 'абсолютный путь'),
                                   ('file:///tmp/contract.pdf', 'без file://')):
            self.response.write_text(f'[Документ][doc]\n\n[doc]: {target}')
            result, data = self.run_check()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertTrue(any(diagnostic in issue for issue in data['issues']))

    def test_only_used_links_and_citations_outside_code_are_checked(self):
        missing = self.root / 'missing.pdf'
        self.response.write_text(
            f'`[пример]({missing})`\n\n'
            f'`:codex-file-citation{{path="{missing}"}}`\n\n'
            f'```markdown\n[пример][doc]\n\n[doc]: {missing}\n'
            f':codex-file-citation{{path="{missing}"}}\n```\n\n'
            f'    [пример]({missing})\n\n'
            f'[unused]: {missing}\n\n'
            '[Справка][web]\n\n[web]: https://example.org/help "Справка"\n\n'
            f':codex-file-citation{{path="{self.artifact}" purpose="source"}}')
        result, data = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['local_links'], [str(self.artifact.resolve())])

    def test_raw_html_links_and_images_are_rejected_but_code_examples_are_allowed(self):
        for tag in (f'<a href="{self.artifact}">Счёт</a>', f'<img src="{self.artifact}" alt="Счёт">'):
            for content in (f'Готово: {tag}', f'<div>\n{tag}\n</div>'):
                with self.subTest(content=content):
                    self.response.write_text(content)
                    result, data = self.run_check()
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(data['valid'])
                    self.assertTrue(any('HTML в ответе не поддерживается' in issue for issue in data['issues']))
            self.response.write_text(f'`{tag}`\n\n```html\n{tag}\n```\n\n[Счёт](<{self.artifact}>)')
            result, data = self.run_check()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['local_links'], [str(self.artifact.resolve())])

    def test_literal_percent_filename_is_not_confused_with_space_filename(self):
        literal = self.root / 'file%20name.pdf'
        spaced = self.root / 'file name.pdf'
        literal.write_text('Символы процента в имени')
        spaced.write_text('Пробел в имени')
        for target, expected in ((str(spaced), spaced), (str(literal), spaced),
                                 (str(literal).replace('%', '%25'), literal)):
            for text in (f'[Файл](<{target}>)', f'![Превью](<{target}>)',
                         f'[Файл][doc]\n\n[doc]: <{target}>'):
                with self.subTest(text=text):
                    self.response.write_text(text)
                    result, data = self.run_check()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(data['local_links'], [str(expected.resolve())])

        spaced.unlink()
        self.response.write_text(f'[Файл](<{literal}>)')
        result, data = self.run_check()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(data['local_links'], [])
        self.assertTrue(any('отсутствующий файл' in issue for issue in data['issues']))

    def test_query_and_fragment_are_removed_before_single_percent_decode(self):
        actual = self.root / 'file.pdf'
        literal = self.root / 'file.pdf?download=1'
        with_fragment = self.root / 'file.pdf?download=1#page'
        percent = self.root / 'file.pdf%3Fdownload=1'
        for path in (actual, literal, with_fragment, percent):
            path.write_text(path.name)
        encoded = str(literal).replace('?', '%3F')
        cases = ((f'{actual}?download=1#page', actual),
                 (f'{actual}?next=https://example.org#page', actual),
                 (f'{actual}#page?download=1', actual),
                 (f'{actual}:12?download=1#page', actual),
                 (f'{encoded}?download=2#page', literal),
                 (str(with_fragment).replace('?', '%3F').replace('#', '%23'), with_fragment),
                 (str(percent).replace('%', '%25') + '?download=2#page', percent))
        for target, expected in cases:
            for text in (f'[Файл](<{target}>)', f'![Превью](<{target}>)',
                         f'[Файл][doc]\n\n[doc]: <{target}>'):
                with self.subTest(text=text):
                    self.response.write_text(text)
                    result, data = self.run_check()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(data['local_links'], [str(expected.resolve())])
        actual.unlink()
        for query in ('download=1', 'next=https://example.org'):
            self.response.write_text(f'[Файл](<{actual}?{query}>)')
            result, data = self.run_check()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(data['local_links'], [])
            self.assertTrue(any('отсутствующий файл' in issue for issue in data['issues']))

    def test_native_citation_preserves_markdown_characters_in_path(self):
        path = self.root / '_черновик_' / '[счёт] `итог`.pdf'
        path.parent.mkdir()
        path.write_text('Счёт')
        self.response.write_text(f'Готово: :codex-file-citation{{path="{path}" purpose="source"}}')
        result, data = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['local_links'], [str(path.resolve())])

    def test_directory_links_are_rejected_in_all_supported_forms(self):
        folder = self.artifact.parent
        alias = self.root / 'alias'
        alias.symlink_to(folder, target_is_directory=True)
        for link in (
                f'[Документы]({folder})',
                f'[Документы](<{folder}>)',
                f'[Документы](<{str(folder).replace(" ", "%20")}>)',
                f':codex-file-citation{{path="{folder}" purpose="source"}}',
                f'[Документы]({alias})'):
            with self.subTest(link=link):
                self.response.write_text(link)
                result, data = self.run_check()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(data['valid'])
                self.assertEqual(data['local_links'], [])
                self.assertTrue(any('конкретный файл' in issue for issue in data['issues']))


class CreativeDocumentBindingTests(unittest.TestCase):
    setUp = HandoffTests.setUp
    file = HandoffTests.file
    data = HandoffTests.data
    registered = HandoffTests.registered
    layout = HandoffTests.layout
    import_post = HandoffTests.import_post
    link_report = HandoffTests.link_report
    register_link_report = HandoffTests.register_link_report
    creative_review = HandoffTests.creative_review
    received_pdf = HandoffTests.received_pdf

    def docx(self, text, post_file=True, status='draft'):
        import render_contract
        fields = {key: value['example'] for key, value in json.loads((ROOT / 'assets/template-fields.json').read_text())['fields'].items()
                  if '<N>' not in key}
        fields.update(POST_TEXT=text, LINK_COUNT='0')
        source = self.file('render-post.txt', text) if post_file else None
        path = self.root / 'creative.docx'
        render_contract.render(ROOT / 'assets/templates/creative.docx', fields, path, post_file=source)
        sidecar = path.with_suffix('.docx.fields.json')
        handoff.register(self.state, [path, sidecar], status, 'v1', self.at, group='creative', document_kind='creative')
        if status != 'received':
            self.layout(path)
        return path, sidecar, self.link_report(path)

    def assert_audit_readonly(self, valid):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = handoff.audit(self.state)
        self.assertEqual(result['valid'], valid, result['issues'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        return result

    def test_generated_docx_matches_import_without_requiring_approval_or_erid(self):
        self.import_post('The agreed text.\r\nSecond line.')
        _, _, report = self.docx(self.data()['creative']['text'])
        self.register_link_report(report)
        result = self.assert_audit_readonly(True)
        self.assertNotEqual(result['checks']['creative_approval']['status'], 'confirmed')
        self.assertNotEqual(result['checks']['ord_creative']['status'], 'confirmed')

    def test_matching_urls_do_not_allow_another_post_or_unimported_manual_field(self):
        for actual, source in (('A different post.', True), ('The agreed text.', False)):
            with self.subTest(actual=actual, post_file=source), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder)
                self.state = self.root / 'deal-state.json'
                self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
                self.import_post('The agreed text.')
                _, _, report = self.docx(actual, post_file=source)
                self.register_link_report(report)
                result = self.assert_audit_readonly(False)
                self.assertTrue(any('POST_TEXT, post_source' in issue for issue in result['issues']))

    def test_correct_fields_and_sha_cannot_hide_wrong_text_in_docx(self):
        self.import_post('The agreed text.')
        path, sidecar, report = self.docx('The agreed text.')
        with ZipFile(path) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts['word/document.xml'] = parts['word/document.xml'].replace(b'The agreed text.', b'A different post.')
        with ZipFile(path, 'w') as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        saved = json.loads(sidecar.read_text())
        saved['output_sha256'] = handoff.sha(path)
        sidecar.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'не найден в самом DOCX'):
            handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)

    def test_pdf_manual_review_checks_real_quote_and_current_creative_hash(self):
        text = 'The agreed text.'
        self.import_post(text)
        path = self.received_pdf(text=text)
        handoff.register(self.state, [path], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        report = self.link_report(path)
        with self.assertRaisesRegex(ValueError, 'нужна явная creative_review'):
            handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)
        self.creative_review(report, [{'page': 1, 'quote': text}])
        original = report.read_bytes()
        for mutation in ('quote', 'hash', 'reverse', 'duplicate', 'gap'):
            data = json.loads(original)
            if mutation == 'hash':
                data['creative_review']['creative_sha256'] = '0' * 64
            elif mutation == 'quote':
                data['creative_review']['citations'][0]['quote'] = 'Wrong post.'
            else:
                pages = {'reverse': [2, 1], 'duplicate': [1, 1], 'gap': [1, 3]}[mutation]
                data['creative_review']['citations'] = [{'page': page, 'quote': text} for page in pages]
            report.write_text(json.dumps(data))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)
        report.write_bytes(original)
        self.register_link_report(report)
        self.assert_audit_readonly(True)

    def test_received_docx_requires_explicit_review_even_with_generator_metadata(self):
        text = 'The agreed text.'
        self.import_post(text)
        path, _, report = self.docx(text, status='received')
        with self.assertRaisesRegex(ValueError, 'нужна явная creative_review'):
            handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)
        self.creative_review(report, [{'paragraph': 5, 'quote': text}])
        handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)
        data = json.loads(report.read_text())
        data['creative_review']['citations'][0]['paragraph'] = 6
        report.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'не совпадает с указанным абзацем'):
            handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)

    def pdf_with_pages(self, texts, name='creative'):
        writer = PdfWriter()
        for index, text in enumerate(texts, 1):
            source = self.received_pdf(f'{name}-page-{index}.pdf', text)
            writer.add_page(PdfReader(source).pages[0])
        path = self.root / f'{name}.pdf'
        writer.write(path)
        handoff.register(self.state, [path], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        return path, self.link_report(path, f'{name}-links.json')

    def test_pdf_uncited_leading_and_trailing_pages_require_explicit_review(self):
        text = 'The agreed text.'
        self.import_post(text)
        cases = ((['An unapproved advertisement.', text], 2),
                 ([text, 'An unapproved advertisement.'], 1),
                 (['Another advertisement.', text, 'An extra offer.'], 2))
        for index, (texts, cited_page) in enumerate(cases, 1):
            with self.subTest(texts=texts):
                path, report = self.pdf_with_pages(texts, f'creative-{index}')
                self.creative_review(report, [{'page': cited_page, 'quote': text}])
                original = report.read_bytes()
                for viewed in (None, [cited_page]):
                    data = json.loads(original)
                    if viewed is None:
                        del data['creative_review']['viewed_pages']
                    else:
                        data['creative_review']['viewed_pages'] = viewed
                    report.write_text(json.dumps(data))
                    with self.subTest(viewed=viewed), self.assertRaisesRegex(ValueError, 'просмотр всех страниц PDF'):
                        handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)

    def test_pdf_all_pages_review_allows_pages_without_post_fragments(self):
        self.import_post('First part. Second part.')
        path, report = self.pdf_with_pages(['Appendix title and parties.', 'First part.',
                                           ' Second part.', 'Signature lines.'])
        self.creative_review(report, [{'page': 2, 'quote': 'First part.'}, {'page': 3, 'quote': ' Second part.'}])
        self.register_link_report(report)
        self.assert_audit_readonly(True)

    def test_pdf_viewed_pages_must_match_actual_count_in_order(self):
        text = 'The agreed text.'
        self.import_post(text)
        path, report = self.pdf_with_pages(['Appendix title.', text, 'Signature lines.'])
        self.creative_review(report, [{'page': 2, 'quote': text}])
        original = report.read_bytes()
        for viewed in ([], [1, 2], [2, 3], [1, 2, 3, 4], [3, 2, 1], [1, 2, 2, 3], [True, 2, 3], '1, 2, 3'):
            with self.subTest(viewed=viewed):
                data = json.loads(original)
                data['creative_review']['viewed_pages'] = viewed
                report.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'просмотр всех страниц PDF'):
                    handoff.check_creative_document(path.resolve(), report, self.registered(), self.data(), self.state)

    def test_manual_docx_can_preserve_old_fields_with_explicit_text_review(self):
        text = 'The agreed text.'
        self.import_post(text)
        original, fields, _ = self.docx(text)
        path = self.root / 'manual.docx'
        with ZipFile(original) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts['word/document.xml'] = parts['word/document.xml'].replace(b'</w:body>', b'<w:p><w:r><w:t>Manual note.</w:t></w:r></w:p></w:body>')
        with ZipFile(path, 'w') as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        sidecar = path.with_suffix('.docx.fields.json')
        sidecar.write_bytes(fields.read_bytes())
        handoff.register(self.state, [path, sidecar], 'draft', 'v2', self.at, group='creative', document_kind='creative')
        self.layout(path)
        report = self.link_report(path, 'manual-links.json')
        self.creative_review(report, [{'paragraph': 5, 'quote': text}])
        self.register_link_report(report)
        result = self.assert_audit_readonly(True)
        self.assertTrue(any(item['path'] == str(path.resolve()) for item in result['contract_date_unchecked']))
        self.assertEqual(sidecar.read_bytes(), fields.read_bytes())

    def test_manual_docx_preserves_blank_paragraph_inside_the_complete_post(self):
        self.import_post('Первый абзац\n\nВторой абзац')
        path = self.root / 'manual.docx'
        with ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml',
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                '<w:p><w:r><w:t>Первый абзац</w:t></w:r></w:p><w:p/>'
                '<w:p><w:r><w:t>Второй абзац</w:t></w:r></w:p></w:body></w:document>')
        handoff.register(self.state, [path], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        self.layout(path)
        report = self.link_report(path)
        self.creative_review(report, [{'paragraph': 1, 'quote': 'Первый абзац'},
                                      {'paragraph': 2, 'quote': ''}, {'paragraph': 3, 'quote': 'Второй абзац'}])
        self.register_link_report(report)
        self.assert_audit_readonly(True)


class AnchorPairAuditTests(unittest.TestCase):
    setUp = HandoffTests.setUp
    file = HandoffTests.file
    data = HandoffTests.data
    layout = HandoffTests.layout
    import_post = HandoffTests.import_post
    link_report = HandoffTests.link_report
    creative_review = HandoffTests.creative_review
    register_link_report = HandoffTests.register_link_report
    assert_audit_readonly = CreativeDocumentBindingTests.assert_audit_readonly

    def docx_package(self, expected, actual, legacy=False):
        from xml.sax.saxutils import escape, quoteattr
        text = 'The agreed post.'
        self.import_post(text, [row['url'] for row in expected] if legacy else expected)
        if legacy:
            data = self.data()
            data['creative'].pop('link_map')
            handoff.save(self.state, data)
        path = self.root / 'creative.docx'
        paragraphs = [f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>']
        relationships = []
        for index, row in enumerate(actual, 1):
            paragraphs.append(f'<w:p><w:hyperlink r:id="r{index}"><w:r><w:t xml:space="preserve">'
                              f'{escape(row["anchor"])}</w:t></w:r></w:hyperlink></w:p>')
            relationships.append(f'<Relationship Id="r{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                f'Target={quoteattr(row["url"])} TargetMode="External"/>')
        with ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml',
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
                + ''.join(paragraphs) + '</w:body></w:document>')
            archive.writestr('word/_rels/document.xml.rels',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + ''.join(relationships) + '</Relationships>')
        handoff.register(self.state, [path], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        self.layout(path)
        report = self.link_report(path, expected=[row['url'] for row in expected])
        self.creative_review(report, [{'paragraph': 1, 'quote': text}])
        self.register_link_report(report)
        return report

    def pdf_package(self, visible_anchor):
        import pymupdf
        text, url, anchor = 'The agreed post.', 'https://example.test/product', 'Product'
        self.import_post(text, [{'anchor': anchor, 'url': url}])
        path = self.root / 'creative.pdf'
        with pymupdf.open() as document:
            page = document.new_page(width=300, height=300)
            page.insert_text((20, 30), text)
            if visible_anchor:
                page.insert_text((20, 80), anchor)
            rectangle = pymupdf.Rect(19, 65, 100, 85)
            page.insert_link({'kind': pymupdf.LINK_URI, 'from': rectangle, 'uri': url})
            document.save(path)
        handoff.register(self.state, [path], 'draft', 'v1', self.at, group='creative', document_kind='creative')
        report = self.link_report(path, expected=[url])
        self.creative_review(report, [{'page': 1, 'quote': text}])
        self.register_link_report(report)
        return report

    def accept_resolution(self, report):
        note = self.file('anchor-resolution.md', 'Подписи и адреса проверены по исходной карте и самому приложению; различие принято.')
        handoff.register(self.state, [note], 'reviewed', 'v1', self.at, group='analysis')
        value = json.loads(report.read_text())
        value['resolution'] = {'result': 'accepted', 'notes': 'Сопоставлены все пары подпись — адрес в порядке приложения.',
                               'source': note.name}
        resolved = report.with_stem(report.stem + '-resolved')
        resolved.write_text(json.dumps(value))
        handoff.register(self.state, [resolved], 'checked', 'v2', self.at, group='link-evidence')

    def assert_anchor_resolution_needed(self):
        result = self.assert_audit_readonly(False)
        self.assertTrue(any('resolution.result=accepted' in issue for issue in result['issues']), result['issues'])

    def test_exact_ordered_docx_pairs_pass_including_unicode_and_repeated_url(self):
        pairs = [{'anchor': 'Тариф «Плюс» — 1\u00a0000 ₽', 'url': 'https://example.test/?b=2&a=1'},
                 {'anchor': 'Условия Café', 'url': 'https://example.test/?b=2&a=1'}]
        report = self.docx_package(pairs, pairs)
        self.assertNotIn('resolution', json.loads(report.read_text()))
        self.assert_audit_readonly(True)

    def test_changed_real_docx_anchor_with_unchanged_url_requires_resolution(self):
        expected = [{'anchor': 'Условия подписки', 'url': 'https://example.test/product'}]
        actual = [{'anchor': 'Бесплатный доступ', 'url': expected[0]['url']}]
        report = self.docx_package(expected, actual)
        value = json.loads(report.read_text())
        self.assertTrue(value['comparison']['ordered_lists_equal'])
        self.assertEqual(value['links'][0]['anchor'], actual[0]['anchor'])
        self.assert_anchor_resolution_needed()
        self.accept_resolution(report)
        self.assert_audit_readonly(True)

    def test_reordered_anchors_on_same_url_are_not_treated_as_equal(self):
        pairs = [{'anchor': 'Тарифы', 'url': 'https://example.test/product'},
                 {'anchor': 'Отмена подписки', 'url': 'https://example.test/product'}]
        self.docx_package(pairs, pairs[::-1])
        self.assert_anchor_resolution_needed()

    def test_unicode_composition_is_not_silently_normalized(self):
        expected = [{'anchor': 'Café', 'url': 'https://example.test/product'}]
        actual = [{'anchor': 'Cafe\u0301', 'url': expected[0]['url']}]
        self.docx_package(expected, actual)
        self.assert_anchor_resolution_needed()

    def test_empty_docx_anchor_needs_explicit_resolution(self):
        expected = [{'anchor': 'Тарифы', 'url': 'https://example.test/product'}]
        report = self.docx_package(expected, [{'anchor': '', 'url': expected[0]['url']}])
        self.assertEqual(json.loads(report.read_text())['links'][0]['anchor'], '')
        self.assert_anchor_resolution_needed()
        self.accept_resolution(report)
        self.assert_audit_readonly(True)

    def test_legacy_urls_without_link_map_need_resolution_even_for_visible_url(self):
        url = 'https://example.test/product'
        pairs = [{'anchor': url, 'url': url}]
        report = self.docx_package(pairs, pairs, legacy=True)
        self.assertNotIn('link_map', self.data()['creative'])
        self.assert_anchor_resolution_needed()
        self.accept_resolution(report)
        self.assert_audit_readonly(True)

    def test_pdf_exact_anchor_hint_still_requires_explicit_resolution(self):
        report = self.pdf_package(visible_anchor=True)
        self.assertEqual(json.loads(report.read_text())['links'][0]['anchor_hint'], 'Product')
        self.assert_anchor_resolution_needed()
        self.accept_resolution(report)
        self.assert_audit_readonly(True)

    def test_pdf_missing_anchor_hint_needs_explicit_resolution(self):
        report = self.pdf_package(visible_anchor=False)
        self.assertIsNone(json.loads(report.read_text())['links'][0]['anchor_hint'])
        self.assert_anchor_resolution_needed()
        self.accept_resolution(report)
        self.assert_audit_readonly(True)


class StateFileDeliveryTests(unittest.TestCase):
    setUp = HandoffTests.setUp
    file = HandoffTests.file
    data = HandoffTests.data
    scheduled_state = HandoffTests.scheduled_state
    plan_package = HandoffTests.plan_package
    register_plan = HandoffTests.register_plan

    def delivery_package(self):
        self.scheduled_state()
        plan = self.plan_package()
        self.register_plan(plan)
        document = self.file('placement-summary.txt', 'Согласованные условия размещения.')
        handoff.register(self.state, [document], 'prepared', 'v1', self.at, group='summary')
        response = self.file('reply.txt', f'[План](<{plan[1]}>) и [условия](<{document}>).')
        return plan, response

    def snapshot(self):
        return {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def test_state_link_blocks_audit_and_recording_without_changing_files(self):
        _, response = self.delivery_package()
        response.write_text(response.read_text() + f' [Состояние](<{self.state}>)')
        before = self.snapshot()
        for record_issue in (False, True):
            with self.subTest(record_issue=record_issue):
                result = handoff.audit(self.state, response, record_issue=record_issue)
                self.assertFalse(result['valid'], result['issues'])
                self.assertEqual(len(result['issues']), 1, result['issues'])
                self.assertIn('Внутренний файл состояния сделки не входит в выдачу', result['issues'][0])
                self.assertIn(str(self.state.resolve()), result['issues'][0])
                if record_issue:
                    self.assertFalse(result['plan_issue_recorded'])
                    self.assertEqual(result['plan_issue_count'], 0)
                self.assertEqual(before, self.snapshot())

    def test_registered_plan_and_document_pass_and_record_plan_issue(self):
        plan, response = self.delivery_package()
        before = self.snapshot()
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(before, self.snapshot())
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        self.assertTrue(result['plan_issue_recorded'])
        self.assertEqual(result['plan_issue_count'], 1)
        entry = self.data()['plan_issues'][0]
        self.assertEqual(entry['plan'], {'path': str(plan[0].relative_to(self.root)), 'sha256': handoff.sha(plan[0])})
        self.assertEqual(entry['response'], {'path': response.name, 'sha256': handoff.sha(response)})
        self.assertNotEqual(before[self.state], self.state.read_bytes())
        self.assertEqual({p: raw for p, raw in before.items() if p != self.state},
                         {p: raw for p, raw in self.snapshot().items() if p != self.state})

    def test_check_links_cli_accepts_existing_state_file_without_reading_it(self):
        self.state.write_text('Не JSON: проверка путей не должна читать состояние.')
        response = self.file('reply.txt', f'[Состояние](<{self.state}>)')
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/handoff.py'), 'check-links',
                                 '--response', str(response)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data['valid'], data['issues'])
        self.assertEqual(data['local_links'], [str(self.state.resolve())])
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
