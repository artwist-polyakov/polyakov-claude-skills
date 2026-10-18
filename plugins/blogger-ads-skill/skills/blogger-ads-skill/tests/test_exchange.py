"""Один выбранный канал применяется к договору и закрывающим документам."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('exchange_render', ROOT / 'scripts/render_contract.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        schema = json.loads((ROOT / 'assets/template-fields.json').read_text())
        self.fields = {key: item['example'] for key, item in schema['fields'].items()
                       if '<N>' not in key and key not in module.EXCHANGE_FIELDS}
        self.fields.update(EXCHANGE_METHOD='scans')
        self.data = json.loads((ROOT / 'assets/exchange.example.json').read_text())
        self.data.update(channel='telegram', customer_contact='@example_customer', agreement='proposed',
                         closing_document='УПД', closing_sender='contractor',
                         closing_deadline='в течение 24 часов после окончания размещения',
                         followup_method='edo', followup_deadline='после восстановления ЭДО')
        self.exchange = self.directory / 'exchange-v1.json'
        self.output = self.directory / 'contract-v1.docx'
        self.save(self.exchange, self.data)

    def save(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def render(self, fields=None, data=None, output=None):
        if data is not None:
            self.save(self.exchange, data)
        return module.render(module.TEMPLATES / 'contract.docx', fields or self.fields,
                             output or self.output, exchange_file=self.exchange)

    def clauses(self, path=None):
        paragraphs = module.inspect_document(path or self.output)['paragraphs']
        return [next(p['text'] for p in paragraphs if p['text'].startswith(number))
                for number in ('3.6.', '7.3.')]

    def assert_not_written(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.docx.fields.json').exists())

    def test_same_telegram_channel_in_both_clauses_with_unknown_contractor_and_proposed_agreement(self):
        fields = self.fields | {'CONTRACTOR_CLAIMS_CONTACT': 'office@example.org'}
        self.render(fields)
        closing, exchange = self.clauses()
        for clause in (closing, exchange):
            self.assertIn('обеими Сторонами через Telegram', clause)
            self.assertIn('[УТОЧНИТЬ АККАУНТ TELEGRAM ИСПОЛНИТЕЛЯ]', clause)
            self.assertIn('Заказчик: @example_customer', clause)
            self.assertIn('согласие контрагента пока не подтверждено', clause)
            self.assertIn('после восстановления ЭДО', clause)
            self.assertNotIn('office@example.org', clause)
            self.assertNotIn('бумаж', clause)
        self.assertEqual(closing.split('Подписанные сканы', 1)[1], exchange.split('Подписанные сканы', 1)[1])
        self.assertIn('Исполнитель направляет Заказчику УПД', closing)
        self.assertIn(self.data['closing_deadline'], closing)
        saved = json.loads(self.output.with_suffix('.docx.fields.json').read_text())
        self.assertEqual(saved['exchange_source'], {'path': str(self.exchange.resolve()),
                         'sha256': hashlib.sha256(self.exchange.read_bytes()).hexdigest()})
        self.assertEqual(saved['fields']['EXCHANGE_METHOD'], 'scans')
        self.assertNotIn('EXCHANGE_METHOD', '\n'.join(self.clauses()))

    def test_unknown_channel_contacts_deadline_and_edo_are_explicit_without_defaults(self):
        data = json.loads((ROOT / 'assets/exchange.example.json').read_text())
        data['channel'] = 'unknown'
        self.render(data=data)
        closing, exchange = self.clauses()
        for clause in (closing, exchange):
            self.assertIn('[УТОЧНИТЬ ОБЩИЙ КАНАЛ ОБМЕНА]', clause)
            self.assertIn('Статус согласования порядка обмена: [УТОЧНИТЬ]', clause)
            self.assertIn('[СОГЛАСОВАТЬ НЕОБХОДИМОСТЬ, СПОСОБ И СРОК]', clause)
            self.assertNotIn('Telegram', clause)
            self.assertNotIn('48', clause)
            self.assertNotIn('Стороны обмениваются бумажными оригиналами', clause)
        self.assertIn('[СОГЛАСОВАТЬ ДЛИТЕЛЬНОСТЬ СРОКА И НАЧАЛО ОТСЧЁТА]', closing)

    def test_confirmed_email_and_no_followup_method_are_supported(self):
        data = self.data | {'channel': 'email', 'contractor_contact': 'seller@example.org',
                           'customer_contact': 'buyer@example.org', 'agreement': 'agreed',
                           'followup_method': 'none', 'followup_deadline': None}
        self.render(data=data)
        for clause in self.clauses():
            self.assertIn('обеими Сторонами через электронную почту', clause)
            self.assertIn('Исполнитель: seller@example.org; Заказчик: buyer@example.org', clause)
            self.assertIn('Порядок обмена согласован Сторонами', clause)
            self.assertIn('Последующий обмен через ЭДО или бумажными оригиналами не предусмотрен', clause)
            self.assertNotIn('согласие контрагента пока', clause)

    def test_paper_followup_is_explicit_in_both_clauses_without_invented_deadline(self):
        data = self.data | {'followup_method': 'paper', 'followup_deadline': None}
        self.render(data=data)
        for clause in self.clauses():
            self.assertIn('обеими Сторонами через Telegram', clause)
            self.assertIn('согласие контрагента пока не подтверждено', clause)
            self.assertIn('Стороны обмениваются бумажными оригиналами тех же документов', clause)
            self.assertIn('Срок обмена оригиналами: [СОГЛАСОВАТЬ СРОК]', clause)
            self.assertNotIn('оформляют те же документы через ЭДО', clause)
        source = self.directory / 'exchange-v2.json'
        self.save(source, data | {'followup_deadline': 'в течение 7 рабочих дней после обмена сканами',
                                 'agreement': 'agreed'})
        output = self.directory / 'contract-v2.docx'
        module.revise_document(self.output, {}, output, exchange_file=source)
        for clause in self.clauses(output):
            self.assertIn('в течение 7 рабочих дней после обмена сканами', clause)
            self.assertIn('Порядок обмена согласован Сторонами', clause)
            self.assertNotIn('Срок обмена оригиналами: [СОГЛАСОВАТЬ СРОК]', clause)

    def test_invalid_contacts_and_inconsistent_unknown_inputs_are_rejected_before_write(self):
        variants = [
            self.data | {'contractor_contact': 'office@example.org'},
            self.data | {'customer_contact': '@other; email@example.org'},
            self.data | {'channel': 'email'},
            self.data | {'channel': 'unknown'},
            self.data | {'channel_name': 'Telegram'},
            self.data | {'channel': 'other', 'channel_name': None},
            self.data | {'followup_method': 'none'},
            self.data | {'agreement': True},
            self.data | {'closing_deadline': ''},
            self.data | {'closing_deadline': 'срок\nновая строка'},
            self.data | {'additional_contact': 'office@example.org'},
            {key: value for key, value in self.data.items() if key != 'agreement'},
        ]
        for data in variants:
            with self.subTest(data=data):
                before = copy.deepcopy(data)
                with self.assertRaises(ValueError):
                    self.render(data=data)
                self.assertEqual(data, before)
                self.assert_not_written()

    def test_common_custom_channel_is_supported_without_replacing_it(self):
        data = self.data | {'channel': 'other', 'channel_name': 'согласованный кабинет документообмена',
                           'contractor_contact': 'профиль 123', 'customer_contact': 'профиль 456'}
        self.render(data=data)
        for clause in self.clauses():
            self.assertIn('через согласованный кабинет документообмена', clause)
            self.assertIn('Исполнитель: профиль 123; Заказчик: профиль 456', clause)

    def test_own_contract_requires_explicit_method_and_scans_require_source(self):
        for method in (None, '', 'edo_and_scans', 'scans'):
            fields = self.fields.copy()
            if method is None:
                fields.pop('EXCHANGE_METHOD')
            else:
                fields['EXCHANGE_METHOD'] = method
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, 'EXCHANGE_METHOD'):
                    module.render(module.TEMPLATES / 'contract.docx', fields, self.output)
                self.assert_not_written()

    def test_exchange_file_requires_scans_method_and_own_contract(self):
        for method in ('edo', 'paper', 'unknown'):
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, 'EXCHANGE_METHOD=scans'):
                    self.render(self.fields | {'EXCHANGE_METHOD': method})
                self.assert_not_written()
        with self.assertRaisesRegex(ValueError, 'только к собственному шаблону contract'):
            module.render(module.TEMPLATES / 'placement-terms.docx', self.fields,
                          self.output, exchange_file=self.exchange)
        self.assert_not_written()

    def test_both_manual_conflicts_rejected_even_when_labeled_as_proposal(self):
        for key in module.EXCHANGE_FIELDS:
            with self.subTest(key=key):
                fields = self.fields | {key: 'Предложение: Исполнитель по email, Заказчик через Telegram.'}
                before = fields.copy()
                with self.assertRaisesRegex(ValueError, key + ' противоречит --exchange-file'):
                    self.render(fields)
                self.assertEqual(fields, before)
                self.assert_not_written()
        exact = module.scan_exchange_fields(self.data)
        self.render(self.fields | exact)
        self.assertTrue(self.output.exists())

    def test_revise_reuses_source_and_rejects_manual_override_or_changed_source(self):
        self.render()
        before = self.output.read_bytes()
        second = self.directory / 'contract-v2.docx'
        module.revise_document(self.output, {'PRICE_TOTAL': '123 456 рублей'}, second)
        self.assertEqual(self.clauses(), self.clauses(second))
        with self.assertRaisesRegex(ValueError, 'EXCHANGE_CLAUSE противоречит --exchange-file'):
            module.revise_document(self.output, {'EXCHANGE_CLAUSE': 'Другая переписка.'}, self.directory / 'bad.docx')
        self.save(self.exchange, self.data | {'contractor_contact': '@example_seller'})
        with self.assertRaisesRegex(ValueError, 'Исходный порядок обмена изменился'):
            module.revise_document(self.output, {}, self.directory / 'changed.docx')
        self.assertEqual(self.output.read_bytes(), before)
        self.assertFalse((self.directory / 'bad.docx').exists())
        self.assertFalse((self.directory / 'changed.docx').exists())

    def test_revise_new_source_changes_both_clauses_and_keeps_old_files(self):
        self.render()
        before = {p: p.read_bytes() for p in (self.output, self.exchange, self.output.with_suffix('.docx.fields.json'))}
        new_source = self.directory / 'exchange-v2.json'
        self.save(new_source, self.data | {'contractor_contact': '@example_seller', 'agreement': 'agreed'})
        output = self.directory / 'contract-v2.docx'
        module.revise_document(self.output, {}, output, exchange_file=new_source)
        for clause in self.clauses(output):
            self.assertIn('Исполнитель: @example_seller', clause)
            self.assertIn('Порядок обмена согласован Сторонами', clause)
            self.assertNotIn('[УТОЧНИТЬ АККАУНТ', clause)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(json.loads(output.with_suffix('.docx.fields.json').read_text())['exchange_source']['path'], str(new_source.resolve()))

    def test_switching_from_scans_requires_both_new_clauses_and_clears_source(self):
        self.render()
        output = self.directory / 'contract-v2.docx'
        with self.assertRaisesRegex(ValueError, 'оба новых пункта'):
            module.revise_document(self.output, {'EXCHANGE_METHOD': 'edo'}, output)
        changes = {'EXCHANGE_METHOD': 'edo', 'EXCHANGE_CLAUSE': 'Документы подписываются через ЭДО.',
                   'CLOSING_DOCUMENTS_CLAUSE': 'Исполнитель направляет УПД через ЭДО после оказания услуг.'}
        module.revise_document(self.output, changes, output)
        saved = json.loads(output.with_suffix('.docx.fields.json').read_text())
        self.assertIsNone(saved['exchange_source'])
        self.assertEqual(saved['fields']['EXCHANGE_METHOD'], 'edo')
        self.assertNotIn('Telegram', '\n'.join(self.clauses(output)))

    def test_human_changed_docx_cannot_be_revised_with_new_exchange_source(self):
        self.render()
        with ZipFile(self.output, 'a') as package:
            package.writestr('manual-change', b'human bytes')
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'DOCX изменён после сборки'):
            module.revise_document(self.output, {}, self.directory / 'new.docx', exchange_file=self.exchange)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertFalse((self.directory / 'new.docx').exists())

    def test_cli_can_generate_scans_and_rejects_exchange_file_for_patch(self):
        fields = self.directory / 'fields.json'
        self.save(fields, self.fields)
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/render_contract.py'), '--template', 'contract',
                                 '--fields', str(fields), '--exchange-file', str(self.exchange),
                                 '--output', str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/render_contract.py'), '--input', str(self.output),
                                 '--inspect', '--exchange-file', str(self.exchange)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--exchange-file', result.stderr)


if __name__ == '__main__':
    unittest.main()
