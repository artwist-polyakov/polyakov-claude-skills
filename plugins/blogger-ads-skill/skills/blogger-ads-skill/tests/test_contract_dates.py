import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import handoff


class ContractDateTests(unittest.TestCase):
    def setUp(self):
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
        return path

    def reply(self, path, name=None):
        return self.file(name or (path.stem + '-reply.txt'), f'Документ: [проект](<{path}>)')

    def issue(self, path):
        result = handoff.audit(self.state, self.reply(path), record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        return result

    def capture(self):
        return {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def test_project_with_deferred_date_passes(self):
        for index, value in enumerate(('«___» __________ 2026 г.', '____')):
            with self.subTest(value=value):
                self.issue(self.document(f'draft-{index}', value))

    def test_date_in_russian_or_iso_format_passes(self):
        for value in ('14.10.2026', '«14» октября 2026 г.', '2026-10-14', '14/10/2026'):
            with self.subTest(value=value):
                self.assertEqual(handoff.contract_date(value), '2026-10-14')

    def test_received_original_preserves_source_files(self):
        self.issue(self.document('v1'))
        source = self.file('received.docx', 'Договор контрагента с иной датой')
        sidecar = self.file('received.docx.fields.json', json.dumps({'fields': {'CONTRACT_NUMBER': 'A-1', 'CONTRACT_DATE': '20.10.2026'}}))
        handoff.register(self.state, [source, sidecar], 'received', 'v1', self.at, group='received', document_kind='contract')
        before = self.capture()
        handoff.check_contract_fields(self.records())
        self.assertEqual(before, self.capture())

    def test_amendment_date_and_actual_signature_dates_are_independent(self):
        self.issue(self.document('v1'))
        new = self.document('appendix-v1', kind='amendment', group='amendment',
                            AMENDMENT_DATE='20.10.2026', SIGNATURE_DATE='18.10.2026')
        self.issue(new)

    def test_docx_without_pdf_passes_audit_without_changing_state(self):
        path = self.document('v1')
        response = self.reply(path)
        before = self.state.read_bytes()
        self.assertTrue(handoff.audit(self.state, response)['valid'])
        self.assertEqual(before, self.state.read_bytes())
        self.issue(path)
        before = self.state.read_bytes()
        self.assertTrue(handoff.audit(self.state, response, record_issue=True)['valid'])
        self.assertEqual(before, self.state.read_bytes())

    def test_current_map_must_match_exact_document_bytes(self):
        old = self.document('v1')
        self.issue(old)
        new = self.document('v2')
        sidecar = new.with_suffix('.docx.fields.json')
        data = json.loads(sidecar.read_text())
        data['output_sha256'] = handoff.sha(old)
        sidecar.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'точную карту'):
            handoff.check_contract_fields(self.records())
        records = self.records()
        records[sidecar]['sha256'] = handoff.sha(sidecar)
        with self.assertRaisesRegex(ValueError, 'output_sha256'):
            handoff.check_contract_fields(records)

    def test_current_map_requires_fields_and_string_values(self):
        path = self.document('v1')
        sidecar = path.with_suffix('.docx.fields.json')
        for fields, error in (
            ([], 'нужна карта fields'),
            ({'CONTRACT_DATE': '14.10.2026'}, 'нужны CONTRACT_NUMBER и CONTRACT_DATE'),
            ({'CONTRACT_NUMBER': 'A-1'}, 'нужны CONTRACT_NUMBER и CONTRACT_DATE'),
            ({'CONTRACT_NUMBER': 1, 'CONTRACT_DATE': '14.10.2026'}, 'CONTRACT_NUMBER: нужна строка'),
            ({'CONTRACT_NUMBER': 'A-1', 'CONTRACT_DATE': 2026}, 'CONTRACT_DATE: нужна строка'),
            ({'CONTRACT_NUMBER': 'A-1', 'CONTRACT_DATE': 'tomorrow'}, 'CONTRACT_DATE: неподдерживаемый формат даты'),
            ({'CONTRACT_NUMBER': 'A-1', 'CONTRACT_DATE': ''}, 'CONTRACT_DATE: неподдерживаемый формат даты'),
        ):
            with self.subTest(fields=fields):
                sidecar.write_text(json.dumps({'output_sha256': handoff.sha(path), 'fields': fields}))
                records = self.records()
                records[sidecar]['sha256'] = handoff.sha(sidecar)
                with self.assertRaisesRegex(ValueError, error):
                    handoff.check_contract_fields(records)

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

    def test_plan_issue_is_recorded_only_after_successful_document_checks(self):
        old = self.document('v1')
        plan1 = self.plan_package('plan-v1')
        reply1 = self.file('issued1.txt', f'[Документ]({old}) [План]({plan1[1]})')
        result = handoff.audit(self.state, reply1, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
        self.assertTrue(result['plan_issue_recorded'])
        new = self.document('v2', '31.02.2026')
        plan2 = self.plan_package('plan-v2', plan1[0])
        reply2 = self.file('issued2.txt', f'[Документ]({new}) [План]({plan2[1]})')
        before = self.capture()
        result = handoff.audit(self.state, reply2, record_issue=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('недопустимая дата' in issue for issue in result['issues']), result['issues'])
        self.assertEqual(before, self.capture())
        self.assertEqual(len(self.data()['plan_issues']), 1)

    def test_real_patch_without_card_preserves_source_files(self):
        import render_contract
        schema = json.loads((ROOT / 'assets/template-fields.json').read_text())
        fields = {key: item['example'] for key, item in schema['fields'].items() if '<N>' not in key}
        fields.update(CONTRACT_NUMBER='AD_2026_001', CONTRACT_DATE='14.10.2026')
        source = self.root / 'source.docx'
        render_contract.render(ROOT / 'assets/templates/contract.docx', fields, source)
        sidecar = source.with_suffix('.docx.fields.json')
        handoff.register(self.state, [source, sidecar], 'draft', 'v1', self.at, group='contract', document_kind='contract')
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
        response = self.reply(patched)
        before = self.capture()
        result = handoff.audit(self.state, response, record_issue=True)
        self.assertTrue(result['valid'], result['issues'])
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
        self.assertTrue(any('.fields.json' in issue and 'утрачен' in issue for issue in result['issues']))
        self.assertEqual(before, self.capture())

    def test_invalid_calendar_date_is_not_a_placeholder(self):
        with self.assertRaisesRegex(ValueError, 'недопустимая дата'):
            handoff.contract_date('31.02.2026')


if __name__ == '__main__':
    unittest.main()
