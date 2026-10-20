import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import handoff
import render_docx
from test_docx_layout import fake_convert_to_pdf, make_layout_fixture


class ContractDateTests(unittest.TestCase):
    def setUp(self):
        self.converter = patch('render_docx.convert_to_pdf', side_effect=fake_convert_to_pdf)
        self.converter.start()
        self.addCleanup(self.converter.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'deal-state.json'
        self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
        self.at = '2026-11-01T12:00:00+03:00'

    def data(self):
        return json.loads(self.state.read_text())

    def records(self):
        return {handoff.resolve(self.root, item['path']): item for item in self.data()['documents']}

    def file(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    def document(self, version, date='14.10.2026', number='A-1', kind='contract', group='contract', **fields):
        path = self.file(version + '.docx', f'Синтетический документ {version}: {date}')
        values = {'CONTRACT_NUMBER': number, 'CONTRACT_DATE': date} | fields
        sidecar = self.file(version + '.docx.fields.json', json.dumps({'output_sha256': handoff.sha(path), 'fields': values}))
        handoff.register(self.state, [path, sidecar], 'draft', version, self.at, group=group, document_kind=kind)
        manifest = make_layout_fixture(path, self.root / ('layout-' + version))
        render_docx.confirm(manifest, [1, 2], 'passed', 'Синтетическая проверка для теста даты')
        handoff.register(self.state, [manifest], 'checked', version, self.at, group='layout-' + group)
        return path

    def reply(self, path, name=None):
        return self.file(name or (path.stem + '-reply.txt'), f'Документ: [проект](<{path}>)')

    def issue(self, path):
        result = handoff.audit(self.state, self.reply(path), record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        return result

    def capture(self):
        return {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def assert_date_error(self, path):
        response = self.reply(path)
        before = self.capture()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('CONTRACT_DATE' in issue for issue in result['issues']), result['issues'])
        self.assertEqual(before, self.capture())
        return result

    def consent(self, number='A-1', before='14.10.2026', after='16.10.2026'):
        quote = f'Измени дату договора {number} с {before} на {after}.'
        source = self.file('user-consent.txt', quote)
        return handoff.contract_date_change(self.state, number, before, after, source, quote, self.at)

    def test_known_to_placeholder_fails_after_archive_and_refresh_without_writes(self):
        old = self.document('v1')
        self.issue(old)
        old_bytes = {path: path.read_bytes() for path in (old, old.with_suffix('.docx.fields.json'))}
        new = self.document('v2', '«___» __________ 2026 г. (заполнить при подписании)')
        handoff.refresh(self.state, self.at)
        self.assertFalse(self.records()[old]['current'])
        self.assert_date_error(new)
        self.assertEqual(old_bytes, {path: path.read_bytes() for path in old_bytes})
        self.assertEqual(len(self.data()['contract_issues']), 1)

    def test_other_date_without_instruction_fails(self):
        self.issue(self.document('v1'))
        self.assert_date_error(self.document('v2', '16.10.2026'))

    def test_same_date_in_russian_or_iso_format_passes(self):
        self.issue(self.document('v1'))
        for index, date in enumerate(('«14» октября 2026 г.', '2026-10-14', '14/10/2026'), 2):
            self.issue(self.document('v' + str(index), date))
        self.assertEqual(len(self.data()['contract_issues']), 4)

    def test_explicit_change_with_quote_preserves_files_and_advances_known_date(self):
        self.issue(self.document('v1'))
        self.consent()
        new = self.document('v2', '16.10.2026')
        response = self.reply(new)
        before = self.capture()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual({p: b for p, b in before.items() if p != self.state},
                         {p: b for p, b in self.capture().items() if p != self.state})
        self.assert_date_error(self.document('v3', '14.10.2026'))
        with self.assertRaisesRegex(ValueError, 'from-date'):
            self.consent(before='14.10.2026', after='17.10.2026')

    def test_unknown_then_known_and_another_number_are_allowed(self):
        self.issue(self.document('v1', 'Дата пока неизвестна'))
        self.issue(self.document('v2'))
        self.issue(self.document('v3', '18.10.2026', number='B-1'))

    def test_registered_but_unissued_draft_is_not_baseline(self):
        self.document('v1')
        new = self.document('v2', '16.10.2026')
        self.issue(new)
        self.assertEqual(len(self.data()['contract_issues']), 1)

    def test_confirmation_does_not_authorize_another_contract(self):
        self.issue(self.document('a-v1', group='a'))
        self.issue(self.document('b-v1', number='B-1', group='b'))
        self.consent()
        self.assert_date_error(self.document('b-v2', '16.10.2026', number='B-1', group='b'))
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'from-date'):
            self.consent(number='C-1')
        self.assertEqual(before, self.state.read_bytes())

    def test_confirmed_change_cannot_authorize_blank_date(self):
        self.issue(self.document('v1'))
        self.consent()
        self.assert_date_error(self.document('v2', 'Неизвестна'))
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'очистка даты запрещена'):
            self.consent(after='Неизвестна')
        self.assertEqual(before, self.state.read_bytes())

    def test_source_quote_and_hash_checked(self):
        self.issue(self.document('v1'))
        source = self.file('bad-quote.txt', 'Дату не меняй.')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'точная цитата'):
            handoff.contract_date_change(self.state, 'A-1', '14.10.2026', '16.10.2026', source,
                                         'Измени дату.', self.at)
        self.assertEqual(before, self.state.read_bytes())
        self.consent()
        new = self.document('v2', '16.10.2026')
        (self.root / 'user-consent.txt').write_text('Изменённый источник')
        result = handoff.audit(self.state, self.reply(new), record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('изменён источник' in issue for issue in result['issues']))
        self.assertEqual(len(self.data()['contract_issues']), 1)

    def test_received_document_is_not_compared_or_recorded(self):
        self.issue(self.document('v1'))
        source = self.file('received.docx', 'Договор контрагента с иной датой')
        sidecar = self.file('received.docx.fields.json', json.dumps({'fields': {'CONTRACT_NUMBER': 'A-1', 'CONTRACT_DATE': '20.10.2026'}}))
        handoff.register(self.state, [source, sidecar], 'received', 'v1', self.at, group='received', document_kind='contract')
        before = self.capture()
        _, current = handoff.check_contract_dates(self.data(), self.records(), self.root, [])
        self.assertNotIn(source, current)
        self.assertEqual(before, self.capture())

    def test_amendment_date_and_actual_signature_dates_are_independent(self):
        self.issue(self.document('v1'))
        new = self.document('appendix-v1', kind='amendment', group='amendment',
                            AMENDMENT_DATE='20.10.2026', SIGNATURE_DATE='18.10.2026')
        self.issue(new)

    def test_legacy_plan_response_recovers_archived_date(self):
        old = self.document('v1')
        response = self.reply(old)
        legacy = [({'response': {'path': response.name, 'sha256': handoff.sha(response)}}, None, None)]
        self.document('v2', 'Дата подписания неизвестна')
        before = self.capture()
        with self.assertRaisesRegex(ValueError, 'была выдана как 2026-10-14'):
            handoff.check_contract_dates(self.data(), self.records(), self.root, legacy)
        self.assertEqual(before, self.capture())

    def test_issue_is_idempotent_and_readonly_check_does_not_record(self):
        path = self.document('v1')
        response = self.reply(path)
        before = self.state.read_bytes()
        self.assertTrue(handoff.audit(self.state, response)['valid'])
        self.assertEqual(before, self.state.read_bytes())
        self.issue(path)
        before = self.state.read_bytes()
        self.assertTrue(handoff.audit(self.state, response, record_issue=True)['valid'])
        self.assertEqual(before, self.state.read_bytes())

    def test_first_packet_cannot_mix_known_and_unknown_dates(self):
        old = self.document('v1')
        self.document('terms-v1', 'Неизвестна', kind='placement_terms', group='terms')
        self.assert_date_error(old)
        self.assertNotIn('contract_issues', self.data())

    def test_current_map_must_match_exact_document_bytes(self):
        old = self.document('v1')
        self.issue(old)
        new = self.document('v2')
        sidecar = new.with_suffix('.docx.fields.json')
        data = json.loads(sidecar.read_text())
        data['output_sha256'] = handoff.sha(old)
        sidecar.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'точную карту'):
            handoff.check_contract_dates(self.data(), self.records(), self.root, [])

    def plan_package(self, version, previous=None):
        state = self.data()
        state['schedule']['publication_at'] = '2026-11-09T09:00:00+03:00'
        state['schedule']['reporting_deadlines'] = [
            {'id': key, 'title': title, 'at': self.at, 'source': 'Тестовая организационная проверка',
             'kind': 'organizational', 'checks': [key]} for key, title in handoff.deal.CHECK_LABELS.items()]
        handoff.save(self.state, state)
        folder = self.root / version
        command = [sys.executable, str(ROOT / 'scripts/deal.py'), 'plan', '--state', str(self.state),
                   '--now', self.at, '--output-dir', str(folder)]
        if previous:
            command += ['--previous', str(previous)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = [folder / name for name in ('plan.json', 'plan.md', 'reminders.ics')]
        handoff.register(self.state, files, 'prepared', version, self.at, group='plan')
        return files

    def test_success_records_both_histories_and_failure_records_neither(self):
        old = self.document('v1')
        plan1 = self.plan_package('plan-v1')
        reply1 = self.file('issued1.txt', f'[Документ]({old}) [План]({plan1[1]})')
        result = handoff.audit(self.state, reply1, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        self.assertTrue(result['contract_issue_recorded'])
        self.assertTrue(result['plan_issue_recorded'])
        new = self.document('v2', 'Подпись пока не получена')
        plan2 = self.plan_package('plan-v2', plan1[0])
        reply2 = self.file('issued2.txt', f'[Документ]({new}) [План]({plan2[1]})')
        before = self.capture()
        result = handoff.audit(self.state, reply2, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertEqual(before, self.capture())
        self.assertEqual(len(self.data()['contract_issues']), 1)
        self.assertEqual(len(self.data()['plan_issues']), 1)

    def test_full_audit_recovers_legacy_issue_and_detects_all_three_dates(self):
        old = self.document('v1')
        plan1 = self.plan_package('plan-v1')
        reply1 = self.file('issued1.txt', f'[Документ]({old}) [План]({plan1[1]})')
        self.assertTrue(handoff.audit(self.state, reply1, record_issue=True)['valid'])
        state = self.data()
        state.pop('contract_issues')
        # Старые версии навыка не требовали регистрации сохранённых карт.
        state['documents'] = [item for item in state['documents'] if not item['path'].endswith('.fields.json')]
        handoff.save(self.state, state)
        new = self.document('v2', 'Неизвестна')
        self.document('terms-v2', 'Неизвестна', kind='placement_terms', group='terms')
        self.document('creative-v2', 'Неизвестна', kind='creative', group='creative')
        plan2 = self.plan_package('plan-v2', plan1[0])
        reply2 = self.file('issued2.txt', f'[Документ]({new}) [План]({plan2[1]})')
        before = self.capture()
        result = handoff.audit(self.state, reply2, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertEqual(sum(issue.count('была выдана как 2026-10-14') for issue in result['issues']), 3)
        self.assertEqual(before, self.capture())
        self.assertNotIn('contract_issues', self.data())
        self.assertEqual(len(self.data()['plan_issues']), 1)

    def test_unregistered_legacy_map_gets_sha_protection_on_issue(self):
        path = self.document('v1')
        state = self.data()
        state['documents'] = [item for item in state['documents'] if not item['path'].endswith('.fields.json')]
        handoff.save(self.state, state)
        self.issue(path)
        sidecar = path.with_suffix('.docx.fields.json')
        fields = json.loads(sidecar.read_text())
        fields['fields']['CONTRACT_DATE'] = '16.10.2026'
        sidecar.write_text(json.dumps(fields))
        before = self.capture()
        result = handoff.audit(self.state, self.reply(path), record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('изменён выданный fields' in issue for issue in result['issues']))
        self.assertEqual(before, self.capture())

    def test_real_patch_without_card_reports_unchecked_and_preserves_history(self):
        import render_contract
        schema = json.loads((ROOT / 'assets/template-fields.json').read_text())
        fields = {key: item['example'] for key, item in schema['fields'].items() if '<N>' not in key}
        fields.update(CONTRACT_NUMBER='AD_2026_001', CONTRACT_DATE='14.10.2026')
        source = self.root / 'source.docx'
        render_contract.render(ROOT / 'assets/templates/contract.docx', fields, source)
        sidecar = source.with_suffix('.docx.fields.json')
        handoff.register(self.state, [source, sidecar], 'draft', 'v1', self.at, group='contract', document_kind='contract')
        manifest = make_layout_fixture(source, self.root / 'layout-original')
        render_docx.confirm(manifest, [1, 2], 'passed', 'Синтетический просмотр для теста проверки выдачи')
        handoff.register(self.state, [manifest], 'checked', 'v1', self.at, group='layout-contract')
        self.issue(source)
        preserved = {path: path.read_bytes() for path in (source, sidecar)}
        changes = self.file('patch.json', json.dumps({'source_sha256': handoff.sha(source), 'replacements': [
            {'find': fields['PAYMENT_CLAUSE'], 'replace': fields['PAYMENT_CLAUSE'] + ' Оплата производится в рублях.'}]}))
        patched = self.root / 'patched.docx'
        command = [sys.executable, str(ROOT / 'scripts/render_contract.py'), '--input', str(source),
                   '--patch', str(changes), '--output', str(patched)]
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(patched.with_suffix('.docx.fields.json').exists())
        for path in (source, patched):
            with ZipFile(path) as archive:
                xml = ET.fromstring(archive.read('word/document.xml'))
            text = ''.join(node.text or '' for node in xml.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
            self.assertIn('14.10.2026', text)
        handoff.register(self.state, [patched], 'draft', 'v2', self.at, group='contract', document_kind='contract')
        manifest = make_layout_fixture(patched, self.root / 'layout-patched')
        render_docx.confirm(manifest, [1, 2], 'passed', 'Синтетический просмотр для теста проверки выдачи')
        handoff.register(self.state, [manifest], 'checked', 'v2', self.at, group='layout-contract')
        response = self.reply(patched)
        before = self.capture()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(len(result['contract_date_unchecked']), 1)
        self.assertEqual(result['contract_date_unchecked'][0]['path'], str(patched))
        self.assertIn('автоматически не проверена', result['contract_date_unchecked'][0]['reason'])
        self.assertFalse(result['contract_issue_recorded'])
        self.assertEqual(len(self.data()['contract_issues']), 1)
        self.assertEqual(before, self.capture())
        self.assertEqual(preserved, {path: path.read_bytes() for path in preserved})

    def test_deleted_map_of_issued_document_is_still_an_error(self):
        source = self.document('v1')
        self.issue(source)
        source.with_suffix('.docx.fields.json').unlink()
        response = self.reply(source)
        before = self.capture()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('Дата договора:' in issue and '.fields.json' in issue for issue in result['issues']))
        self.assertEqual(before, self.capture())
        self.assertEqual(len(self.data()['contract_issues']), 1)

    def test_number_with_embedded_underscore_keeps_date_protection(self):
        self.issue(self.document('v1', number='AD_2026_001'))
        self.assert_date_error(self.document('v2', 'Неизвестна', number='AD_2026_001'))
        self.assertIsNone(handoff.contract_number('_____'))
        self.assertIsNone(handoff.contract_number('{{CONTRACT_NUMBER}}'))

    def test_legacy_historical_link_does_not_roll_back_known_date(self):
        old = self.document('v1')
        reply1 = self.reply(old)
        current = self.document('v2', '16.10.2026')
        reply2 = self.file('reply-v2.txt', f'[Текущий договор]({current}) Для истории: [первый проект]({old})')
        legacy = [({'response': {'path': response.name, 'sha256': handoff.sha(response)}}, None, None)
                  for response in (reply1, reply2)]
        before = self.capture()
        entries, known = handoff.contract_issue_history(self.data(), self.records(), self.root, legacy)
        self.assertEqual(len(entries), 2)
        self.assertEqual(known['A-1'], '2026-10-16')
        handoff.check_contract_dates(self.data(), self.records(), self.root, legacy)
        self.assertEqual(before, self.capture())

    def test_invalid_calendar_date_is_not_a_placeholder(self):
        with self.assertRaisesRegex(ValueError, 'недопустимая дата'):
            handoff.contract_date('31.02.2026')

    def test_cli_confirms_change(self):
        self.issue(self.document('v1'))
        source = self.file('user.txt', 'Измени дату договора A-1 на 16 октября 2026 года.')
        command = [sys.executable, str(ROOT / 'scripts/handoff.py'), 'contract-date-change',
                   '--state', str(self.state), '--number', 'A-1', '--from-date', '14.10.2026',
                   '--to-date', '16 октября 2026 года', '--source', str(source), '--quote', source.read_text(), '--at', self.at]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['to_date'], '2026-10-16')


if __name__ == '__main__':
    unittest.main()
