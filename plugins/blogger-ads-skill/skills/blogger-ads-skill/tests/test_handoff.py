import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('handoff', ROOT / 'scripts/handoff.py')
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class HandoffTests(unittest.TestCase):
    def setUp(self):
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

    def link_issues(self):
        return handoff.link_report_issues(self.registered(), self.data()['creative']['links'])

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
        own = self.received_pdf('own-creative.pdf')
        handoff.register(self.state, [received], 'received', 'v1', self.at,
                         group='received', document_kind='creative')
        handoff.register(self.state, [own], 'draft', 'v1', self.at, group='creative')
        own_report = self.link_report(own)
        handoff.register(self.state, [own_report], 'checked', 'v1', self.at, group='own-evidence')
        self.assertTrue(any(str(received) in issue for issue in self.link_issues()))
        received_report = self.link_report(received, 'received-links.json')
        handoff.register(self.state, [received_report], 'checked', 'v1', self.at, group='received-evidence')
        self.assertEqual(self.link_issues(), [])

    def test_received_link_report_with_matching_nonempty_map_passes_readonly(self):
        urls = ['https://example.test/?b=2&a=1', 'https://example.test/?a=1&b=2'] * 2
        source, report = self.received_links(urls, urls)
        self.register_link_report(report)
        before = {path: path.read_bytes() for path in (self.state, source, report)}
        self.assertEqual(self.link_issues(), [])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_link_report_without_expected_blocks_otherwise_valid_handoff(self):
        import inspect_links
        source, report = self.received_links([], [])
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
        data = self.data()
        data['creative']['links'] = [url]
        handoff.save(self.state, data)
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
        self.register_link_report(docx_report)
        result = handoff.audit(self.state, response)
        self.assertFalse(result['valid'])
        self.assertTrue(any(str(pdf) in issue and 'нет актуального отчёта' in issue for issue in result['issues']))
        self.assertFalse(any(str(docx) in issue and 'нет актуального отчёта' in issue for issue in result['issues']))

        pdf_report = self.link_report(pdf, 'pdf-links.json', [url])
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
        _, report = self.received_links([], [])
        data = json.loads(report.read_text())
        data['warnings'] = ['Страница 1: аннотация не прочитана']
        report.write_text(json.dumps(data))
        self.register_link_report(report)
        self.assertTrue(any('не разобраны' in issue for issue in self.link_issues()))

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
        handoff.register(self.state, [old], 'draft', 'v1', self.at, group='creative')
        report = self.link_report(old)
        handoff.register(self.state, [report], 'checked', 'v1', self.at, group='evidence')
        contents = {p: p.read_bytes() for p in (old, report)}
        new = self.received_pdf('new.pdf', 'A revised creative.')
        handoff.register(self.state, [new], 'draft', 'v2', self.at, group='creative')
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
        handoff.register(self.state, [source], 'draft', 'v1', self.at, group='creative')
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
        before = self.state.read_bytes()
        self.assertTrue(handoff.audit(self.state, response)['valid'])
        self.assertNotIn('plan_issues', self.data())
        self.assertEqual(before, self.state.read_bytes())
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

    def test_scene_time_advance_preserves_issued_plan_as_historical_baseline(self):
        self.scheduled_state()
        first_at = self.at
        handoff.deal.set_scene_time(self.state, first_at, 'Пользователь: сейчас 1 ноября, 12:00 МСК')
        first = self.plan_package()
        self.register_plan(first)
        response = self.issue_plan(first)
        original_files = {path: path.read_bytes() for path in [*first, response]}
        first_issue = self.data()['plan_issues'][0]

        self.at = '2026-11-01T15:00:00+03:00'
        handoff.deal.set_scene_time(self.state, self.at, 'Пользователь: теперь 1 ноября, 15:00 МСК')
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

    def test_scene_time_rejects_current_plan_with_old_or_unconfirmed_generation_time(self):
        self.scheduled_state()
        handoff.deal.set_scene_time(self.state, self.at, 'Пользователь: сейчас 1 ноября, 12:00 МСК')
        first = self.plan_package()
        self.register_plan(first)
        self.issue_plan(first)
        self.at = '2026-11-01T15:00:00+03:00'
        handoff.deal.set_scene_time(self.state, self.at, 'Пользователь: теперь 1 ноября, 15:00 МСК')

        # Повторная регистрация не превращает старое время выдачи в текущее.
        self.register_plan(first)
        response = self.file('reply-stale.txt', f'[План](<{first[1]}>)')
        before = self.state.read_bytes()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('не совпадает с текущим временем сцены' in issue for issue in result['issues']))
        self.assertEqual(before, self.state.read_bytes())

        current = self.plan_package('plan-v2', first[0])
        self.rewrite_plan(current, lambda plan: plan.update(generated_at='2026-11-01T17:00:00+03:00'))
        self.register_plan(current)
        response = self.file('reply-unconfirmed.txt', f'[План](<{current[1]}>)')
        before = self.state.read_bytes()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('не совпадает с текущим временем сцены' in issue for issue in result['issues']))
        self.assertEqual(before, self.state.read_bytes())
        self.assertEqual(len(self.data()['plan_issues']), 1)

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
        # Все регистрации и выдачи имели одинаковое время сцены; порядок задан журналом.
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
        files = self.plan_package()
        self.rewrite_plan(files, lambda data: data['events'][0].update(sequence=99))
        self.register_plan(files)
        for stage in ('closed', 'paused', 'cancelled'):
            with self.subTest(stage=stage):
                data = self.data()
                data['stage'] = stage
                handoff.save(self.state, data)
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('SEQUENCE' in item for item in result['issues']))

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
        data['responsibilities']['contract_author'] = 'Изменённое условие'
        handoff.save(self.state, data)
        before = self.state.read_bytes()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('Сверка договора' in issue for issue in result['issues']))
        self.assertEqual(before, self.state.read_bytes())
        data['responsibilities']['contract_author'] = None
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
            'documents': [{'id': 'contract', 'label': 'Полученный договор', 'path': source.name}],
            'signature_matrix': [{'label': 'Учебный договор', 'source': {'document': 'contract', 'page': 1},
                                  'not_applicable': 'Учебный текст без подписной части; просмотрена вся страница.'}],
            'decisions': [{
                'id': category, 'category': category, 'topic': topic,
                'source': {'document': 'contract', 'clause': '1', 'page': 1, 'quote': 'The parties agree on advertising services.'},
                'previous': {'source': {'kind': 'none', 'group': 'contract',
                             'reason': 'Первое полученное предложение, собственного проекта ещё не было.'}},
                'status': 'pending',
                'decision': {'action': 'defer', 'reason': 'Недостаточно условий для принятия',
                             'selected': 'Уточнить до принятия', 'plan': 'Запросить уточнение при согласовании договора'},
                'implementation': {'kind': 'unresolved', 'reason': 'В учебной редакции конкретный порядок не определён',
                                   'state': [{'path': '/responsibilities/contract_author', 'value': None}],
                                   'plan': [{'id': 'report-contract', 'at': self.at}]}
            } for category, topic in review.CATEGORIES.items()]
        }
        state = self.data()
        for item in data['decisions']:
            if item['category'] in review.TIMING_CATEGORIES:
                item['implementation']['timing'] = {
                    'subject': 'Учебное общее условие без срока', 'target': None, 'mode': 'not_applicable',
                    'received_value': None, 'selected_value': None,
                    'reason': 'В учебной фразе нет отдельных сроков этой категории.'}
                state['schedule'].setdefault('reviewed_terms', {})[item['id']] = review.reviewed_timing_record(item)
        handoff.save(self.state, state)
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
        handoff.register(self.state, [own], 'draft_for_review', 'v1', self.at, group='own')
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
        pdf = self.received_pdf('export-with-another-name.pdf')
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
        pdf = self.received_pdf('export.pdf')
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
        source, folder, _ = self.review_package(with_changes=True)
        self.amendment_package(folder)
        state = self.data()
        state['stage'] = 'closed'
        next(item for item in state['documents'] if item['path'] == source.name)['current'] = False
        handoff.save(self.state, state)
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        state = self.data()
        state['responsibilities']['contract_author'] = 'Другая сторона'
        handoff.save(self.state, state)
        self.assertFalse(handoff.audit(self.state)['valid'])

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

    def test_inactive_deal_does_not_require_new_plan(self):
        self.scheduled_state()
        for stage in ('paused', 'cancelled', 'closed'):
            with self.subTest(stage=stage):
                data = self.data()
                data['stage'] = stage
                handoff.save(self.state, data)
                self.assertTrue(handoff.audit(self.state)['valid'])

    def test_literal_instruction_and_advice_are_distinguished_by_source(self):
        source = self.file('user.txt', 'Ничего не отправляй. У меня пока нет новых данных по НДС.')
        handoff.instruction(self.state, source, 'Ничего не отправляй.', self.at)
        self.assertTrue(handoff.audit(self.state)['valid'])
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'дословным'):
            handoff.instruction(self.state, source, 'Проверить освобождение от НДС по статье 145.', self.at)
        self.assertEqual(before, self.state.read_bytes())

    def test_audit_rejects_manually_inserted_paraphrase_and_changed_source(self):
        source = self.file('user.txt', 'Не публикуй пост.')
        handoff.instruction(self.state, source, 'Не публикуй пост.', self.at)
        data = self.data()
        data['user_instructions'][0]['text'] = 'Не публиковать до проверки ОРД.'
        handoff.save(self.state, data)
        self.assertFalse(handoff.audit(self.state)['valid'])
        data['user_instructions'][0]['text'] = 'Не публикуй пост.'
        handoff.save(self.state, data)
        source.write_text('Не публикуй пост. Дополненный источник.')
        self.assertFalse(handoff.audit(self.state)['valid'])

    def test_legacy_instruction_is_not_silently_treated_as_verified(self):
        data = self.data()
        data['user_instructions'] = ['Собственное правило агента; источник: сообщение пользователя']
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertIn('дословным', result['issues'][0])

    def test_superseding_preserves_prior_delivery_and_registers_pdf(self):
        old = self.file('old.docx')
        new = self.file('new.docx')
        pdf = self.file('new.pdf')
        handoff.register(self.state, [old], 'draft', 'v1', self.at)
        handoff.register(self.state, [new, pdf], 'draft', 'v2', '2026-11-02T12:00:00+03:00', [old])
        by_name = {item['path']: item for item in self.data()['documents']}
        self.assertFalse(by_name['old.docx']['current'])
        self.assertEqual(by_name['old.docx']['delivered_at'], self.at)
        self.assertTrue(by_name['new.pdf']['current'])
        self.assertEqual(by_name['new.pdf']['sha256'], handoff.sha(pdf))

    def test_registration_refuses_changed_original_without_mutating_state(self):
        doc = self.file('document.docx')
        handoff.register(self.state, [doc], 'draft', 'v1', self.at)
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
        handoff.register(self.state, [pdf], 'unsigned', 'v1', self.at)
        self.assertTrue(handoff.audit(self.state, response)['valid'])
        response.write_text(f'[Счёт]({self.root / "missing.pdf"})')
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        response.write_text('[Счёт](invoice.pdf)')
        self.assertFalse(handoff.audit(self.state, response)['valid'])

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

    def test_directory_link_cannot_deliver_unregistered_or_superseded_files(self):
        folder = self.root / 'documents'
        folder.mkdir()
        old = folder / 'invoice-v1.pdf'
        old.write_text('Счёт v1')
        response = self.file('reply.txt', f'[Документы]({folder})')
        result = handoff.audit(self.state, response)
        self.assertFalse(result['valid'], result['issues'])
        self.assertTrue(any('конкретный файл' in issue for issue in result['issues']))
        handoff.register(self.state, [old], 'unsigned', 'v1', self.at)
        new = folder / 'invoice-v2.pdf'
        new.write_text('Счёт v2')
        handoff.register(self.state, [new], 'unsigned', 'v2', self.at, [old])
        self.assertFalse(handoff.audit(self.state, response)['valid'])
        response.write_text(f'[Счёт]({new})')
        result = handoff.audit(self.state, response)
        self.assertTrue(result['valid'], result['issues'])

    def test_missing_input_does_not_partially_register_other_files(self):
        doc = self.file('first.docx')
        before = self.state.read_bytes()
        with self.assertRaises(ValueError):
            handoff.register(self.state, [doc, self.root / 'missing.pdf'], 'draft', 'v1', self.at)
        self.assertEqual(before, self.state.read_bytes())

    def test_duplicate_files_are_rejected_before_registration(self):
        doc = self.file('first.docx')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'повторно'):
            handoff.register(self.state, [doc, doc], 'draft', 'v1', self.at)
        self.assertEqual(before, self.state.read_bytes())

    def test_parentheses_spaces_and_encoded_symbols_in_local_links(self):
        pdf = self.file('Счёт (1) #2.pdf')
        handoff.register(self.state, [pdf], 'unsigned', 'v1', self.at)
        response = self.file('reply.txt', f'[Счёт](<{str(pdf).replace("#", "%23")}>)')
        self.assertTrue(handoff.audit(self.state, response)['valid'])

    def test_superseded_link_requires_explicit_historical_use(self):
        old = self.file('old.docx')
        new = self.file('new.docx')
        handoff.register(self.state, [old], 'draft', 'v1', self.at)
        handoff.register(self.state, [new], 'draft', 'v2', self.at, [old])
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
        handoff.register(self.state, [contract], 'draft', 'v1', self.at, group='contract')
        self.layout(contract)
        handoff.register(self.state, [old, old_calendar], 'draft', 'v1', self.at, group='plan')
        handoff.register(self.state, [new], 'draft', 'v2', '2026-11-02T12:00:00+03:00', group='plan')
        docs = {item['path']: item for item in self.data()['documents']}
        self.assertFalse(docs[old.name]['current'])
        self.assertFalse(docs[old_calendar.name]['current'])
        self.assertEqual(docs[old.name]['delivered_at'], self.at)
        self.assertTrue(docs[contract.name]['current'])
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_authored_source_note_expires_but_received_markdown_stays_original(self):
        note = self.file('ord-ids.md', 'Назначение двух кодов ещё неизвестно.')
        original = self.file('client-message.md', 'Полученное сообщение заказчика о кодах.')
        handoff.register(self.state, [note], 'draft_for_review', 'v1', self.at, group='source')
        handoff.register(self.state, [original], 'received', 'v1', self.at, group='evidence')
        before = {p: p.read_bytes() for p in (note, original)}
        data = self.data()
        data['checks']['erid']['source'] = 'Новое изображение: токен отдельно от ID.'
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertTrue(any('прежних фактах' in issue for issue in result['issues']))
        handoff.refresh(self.state, '2026-11-02T12:00:00+03:00')
        records = self.registered()
        self.assertFalse(records[note.resolve()]['current'])
        self.assertTrue(records[original.resolve()]['current'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        with self.assertRaisesRegex(ValueError, 'Основание прежнего'):
            handoff.register(self.state, [note], 'draft_for_review', 'v2', self.at, group='source')

    def test_changed_facts_archive_letters_and_advice_without_closing_checks(self):
        letter = self.file('request.txt', 'Запросить подтверждение оплаты')
        data = self.data()
        data['recommendations'] = [{'at': self.at, 'text': 'Проверить оплату'}]
        handoff.save(self.state, data)
        handoff.register(self.state, [letter], 'draft', 'v1', self.at, group='correspondence')
        self.assertTrue(handoff.audit(self.state)['valid'])
        data = self.data()
        later = '2026-11-02T12:00:00+03:00'
        data['checks']['payment'] = {'status': 'confirmed', 'source': 'Сообщение получателя', 'checked_at': later}
        data['recommendations'].append({'at': later, 'text': 'Оплата получена, проверить подписание'})
        handoff.save(self.state, data)
        self.assertFalse(handoff.audit(self.state)['valid'])
        handoff.refresh(self.state, later)
        after = self.data()
        self.assertFalse(after['recommendations'][0]['current'])
        self.assertTrue(after['recommendations'][1]['current'])
        self.assertFalse(after['documents'][0]['current'])
        self.assertEqual(after['recommendations'][0]['text'], 'Проверить оплату')
        self.assertEqual(after['checks'], data['checks'])
        self.assertTrue(handoff.audit(self.state)['valid'])
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Основание прежнего письма'):
            handoff.register(self.state, [letter], 'draft', 'v2', later, group='correspondence')
        self.assertEqual(before, self.state.read_bytes())

    def test_cancellation_and_payment_policy_changes_invalidate_current_letters(self):
        original = self.data()
        for field in ('stage', 'policy'):
            with self.subTest(field=field):
                handoff.save(self.state, json.loads(json.dumps(original)))
                letter = self.file('letter.txt', 'Оплата требуется до выхода')
                handoff.register(self.state, [letter], 'draft', 'v1', self.at, group='correspondence')
                data = self.data()
                if field == 'stage':
                    data['stage'] = 'cancelled'
                else:
                    data['policy']['require_prepayment'] = False
                handoff.save(self.state, data)
                self.assertFalse(handoff.audit(self.state)['valid'])
                handoff.refresh(self.state, self.at)
                self.assertFalse(self.data()['documents'][0]['current'])
                self.assertTrue(handoff.audit(self.state)['valid'])

    def test_changed_facts_archive_analytical_notes_but_preserve_raw_evidence(self):
        original = self.data()
        for group in ('evidence', 'analysis'):
            with self.subTest(group=group):
                handoff.save(self.state, json.loads(json.dumps(original)))
                note = self.file('creative-review.md', 'Токен и поступление оплаты ещё не подтверждены')
                raw = self.file('received.txt', 'Исходное сообщение заказчика')
                handoff.register(self.state, [raw], 'received', 'v1', self.at, group='source')
                handoff.register(self.state, [note], 'reviewed', 'v1', self.at, group=group)
                data = self.data()
                later = '2026-11-02T12:00:00+03:00'
                data['checks']['payment'] = {'status': 'confirmed', 'source': 'Сообщение получателя', 'checked_at': later}
                handoff.save(self.state, data)
                self.assertFalse(handoff.audit(self.state)['valid'])
                handoff.refresh(self.state, later)
                docs = {item['path']: item for item in self.data()['documents']}
                self.assertFalse(docs[note.name]['current'])
                self.assertTrue(docs[raw.name]['current'])
                self.assertEqual(docs[note.name]['delivered_at'], self.at)
                before = self.state.read_bytes()
                with self.assertRaisesRegex(ValueError, 'Основание прежнего письма или заметки'):
                    handoff.register(self.state, [note], 'reviewed', 'v2', later, group=group)
                self.assertEqual(before, self.state.read_bytes())
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

    def test_received_pdf_docx_require_classification_before_mutating_state(self):
        for suffix in ('.pdf', '.docx'):
            path = self.file('source' + suffix)
            before = self.state.read_bytes()
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, 'document_kind'):
                handoff.register(self.state, [path], 'received', 'v1', self.at, group='source')
            self.assertEqual(self.state.read_bytes(), before)
        path = self.file('own-draft.docx')
        handoff.register(self.state, [path], 'draft_for_review', 'v1', self.at, group='contract')
        self.layout(path)
        self.assertTrue(handoff.audit(self.state)['valid'])

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
        source, _, _ = self.review_package()
        copied = self.root / 'renamed.pdf'
        copied.write_bytes(source.read_bytes())
        handoff.register(self.state, [copied], 'received', 'v1', self.at, group='copy', document_kind='creative')
        report = self.link_report(copied)
        handoff.register(self.state, [report], 'checked', 'v1', self.at, group='links')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        data = self.data()
        data['documents'] = [entry for entry in data['documents'] if entry['path'] != source.name]
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('исходник не зарегистрирован' in issue for issue in result['issues']))

    def test_review_state_bindings_and_plan_are_rechecked_on_each_handoff(self):
        _, _, plans = self.review_package()
        data = self.data()
        data['responsibilities']['contract_author'] = 'customer'
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('/responsibilities/contract_author' in issue for issue in result['issues']))
        data['responsibilities']['contract_author'] = None
        handoff.save(self.state, data)
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

    def test_docx_received_source_is_covered_only_by_explicit_hashed_pdf_original_link(self):
        import review
        _, folder, _ = self.review_package()
        original = self.root / 'received.docx'
        with ZipFile(original, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body><w:p><w:r><w:t>The parties agree on advertising services.</w:t></w:r></w:p></w:body></w:document>')
        handoff.register(self.state, [original], 'received', 'v1', self.at, group='docx-source', document_kind='contract')
        self.assertTrue(any('не охватывает' in issue for issue in handoff.audit(self.state)['issues']))
        decisions = json.loads((folder / 'review-input.json').read_text())
        decisions['documents'][0]['original_path'] = original.name
        source = self.file('decisions-v2.json', json.dumps(decisions))
        second = self.root / 'review-v2'
        review.build(source, second)
        handoff.register(self.state, [second / name for name in handoff.REVIEW_FILES], 'reviewed', 'v2', self.at, group='review')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        original.write_bytes(original.read_bytes() + b'changed')
        self.assertFalse(handoff.audit(self.state)['valid'])

    def test_unknown_publication_with_explicit_pending_plan_can_pass_review_guard(self):
        import review
        _, folder, _ = self.review_package()
        state = self.data()
        state['schedule']['publication_at'] = None
        for entry in state['documents']:
            if entry['group'] == 'plan':
                entry['current'] = False
        handoff.save(self.state, state)
        # Отсутствие даты не позволяет проигнорировать уже заявленные связи плана.
        self.assertFalse(handoff.audit(self.state)['valid'])
        decisions = json.loads((folder / 'review-input.json').read_text())
        for item in decisions['decisions']:
            item['implementation'].update(plan=[], plan_pending_reason='Дата публикации ещё не определена; расчёт после согласования.')
            if 'timing' in item['implementation']:
                state['schedule']['reviewed_terms'][item['id']] = review.reviewed_timing_record(item)
        handoff.save(self.state, state)
        source = self.file('decisions-v2.json', json.dumps(decisions))
        second = self.root / 'review-v2'
        review.build(source, second)
        handoff.register(self.state, [second / name for name in handoff.REVIEW_FILES], 'reviewed', 'v2', self.at, group='review')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertIsNone(self.data()['schedule']['publication_at'])
        state = self.data()
        state['schedule']['publication_at'] = '2026-11-09T09:00:00+03:00'
        handoff.save(self.state, state)
        self.assertFalse(handoff.audit(self.state)['valid'])


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


if __name__ == '__main__':
    unittest.main()
