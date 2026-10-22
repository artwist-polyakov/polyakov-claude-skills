import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import handoff


class DocumentBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'deal-state.json'
        self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
        self.source = self.root / 'research-v1.md'
        self.original = 'Полный первоначальный документ\nСтрока 2\nСтрока 3\n'.encode()
        self.source.write_bytes(self.original)
        self.at = '2026-11-01T12:00:00+03:00'

    def register(self, files=None, version='v1', status='prepared'):
        return handoff.register(self.state, files or [self.source], status, version, self.at, group='documents')

    def data(self):
        return json.loads(self.state.read_text())

    def archive(self, index=0):
        return self.root / self.data()['documents'][index]['archive_path']

    def test_register_saves_exact_bytes_deduplicates_and_preserves_old_versions(self):
        duplicate = self.root / 'same-content.bin'
        duplicate.write_bytes(self.original)
        self.register([self.source, duplicate])
        records = self.data()['documents']
        self.assertEqual(records[0]['archive_path'], records[1]['archive_path'])
        self.assertEqual(self.archive().read_bytes(), self.original)
        self.assertEqual(len(list((self.root / '.versions').iterdir())), 1)
        newer = self.root / 'research-v2.md'
        newer.write_bytes(b'\x00\xffnew exact bytes\r\n')
        self.register([newer], version='v2')
        self.assertEqual(self.archive().read_bytes(), self.original)
        self.assertEqual(self.archive(2).read_bytes(), newer.read_bytes())
        self.assertFalse(self.data()['documents'][0]['current'])
        self.assertTrue(handoff.audit(self.state)['valid'])

    def test_audit_reports_exact_restore_command_without_mutating_any_bytes(self):
        self.register()
        self.source.write_bytes(b'changed current text')
        files = [self.state, self.source, self.archive()]
        before = {p: p.read_bytes() for p in files}
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('restore --state' in issue and str(self.source) in issue and '--save-current' in issue
                            for issue in result['issues']))
        self.assertEqual(before, {p: p.read_bytes() for p in files})

    def test_restore_preserves_current_edits_and_recovers_original_without_registry_changes(self):
        self.register()
        changed = 'Новая редакция, которую нельзя потерять\n'.encode()
        self.source.write_bytes(changed)
        state_before, archive_before = self.state.read_bytes(), self.archive().read_bytes()
        saved = self.root / 'saved-current.md'
        result = handoff.restore(self.state, self.source, saved)
        self.assertTrue(result['restored'])
        self.assertEqual(saved.read_bytes(), changed)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(self.archive().read_bytes(), archive_before)
        self.assertTrue(handoff.audit(self.state)['valid'])
        # Продолжение правок оформляется отдельной версией, старый архив остаётся точным.
        self.register([saved], version='v2')
        self.assertEqual(self.archive().read_bytes(), self.original)
        self.assertEqual(self.archive(1).read_bytes(), changed)

    def test_missing_save_current_and_existing_target_refuse_without_loss(self):
        self.register()
        self.source.write_bytes(b'current changes')
        occupied = self.root / 'occupied.md'
        occupied.write_bytes(b'other existing file')
        before = {p: p.read_bytes() for p in [self.state, self.source, occupied, self.archive()]}
        for target in (None, occupied, self.source, self.state, self.archive()):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handoff.restore(self.state, self.source, target)
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_invalid_or_missing_archive_refuses_before_saving_or_restoring(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.source.write_bytes(self.original)
                self.register()
                archive = self.archive()
                self.source.write_bytes(b'changed')
                if missing:
                    archive.unlink()
                else:
                    archive.write_bytes(b'corrupt archive')
                saved = self.root / 'do-not-create.md'
                before = self.state.read_bytes(), self.source.read_bytes()
                with self.assertRaisesRegex(ValueError, 'Архив отсутствует или повреждён'):
                    handoff.restore(self.state, self.source, saved)
                self.assertFalse(saved.exists())
                self.assertEqual(before, (self.state.read_bytes(), self.source.read_bytes()))
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('Архив отсутствует или повреждён' in issue for issue in result['issues']))
                archive.write_bytes(self.original)

    def test_missing_working_file_is_restored_and_unchanged_file_is_noop(self):
        self.register()
        before = self.state.read_bytes()
        self.source.unlink()
        result = handoff.restore(self.state, self.source)
        self.assertTrue(result['restored'])
        self.assertIsNone(result['saved_current'])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.state.read_bytes(), before)
        result = handoff.restore(self.state, self.source)
        self.assertFalse(result['restored'])
        self.assertEqual(self.state.read_bytes(), before)

    def test_received_original_is_archived_but_restore_refuses_to_change_it(self):
        self.register(status='received')
        self.assertEqual(self.archive().read_bytes(), self.original)
        self.source.write_bytes(b'current received bytes')
        before = self.state.read_bytes(), self.source.read_bytes(), self.archive().read_bytes()
        saved = self.root / 'received-copy.md'
        with self.assertRaisesRegex(ValueError, 'полученные оригиналы'):
            handoff.restore(self.state, self.source, saved)
        self.assertFalse(saved.exists())
        self.assertEqual(before, (self.state.read_bytes(), self.source.read_bytes(), self.archive().read_bytes()))

    def test_registration_refuses_to_overwrite_corrupted_archive(self):
        self.register()
        self.archive().write_bytes(b'corrupt archive')
        before = self.state.read_bytes(), self.source.read_bytes(), self.archive().read_bytes()
        with self.assertRaisesRegex(ValueError, 'Архив повреждён'):
            self.register()
        self.assertEqual(before, (self.state.read_bytes(), self.source.read_bytes(), self.archive().read_bytes()))

    def test_legacy_record_can_gain_archive_only_from_unchanged_original(self):
        self.register()
        data = self.data()
        data['documents'][0].pop('archive_path')
        handoff.save(self.state, data)
        self.assertFalse(handoff.audit(self.state)['valid'])
        self.source.write_bytes(b'changed')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Ранее зарегистрированный файл изменён'):
            self.register()
        self.assertEqual(self.state.read_bytes(), before)
        self.source.write_bytes(self.original)
        self.register()
        self.assertTrue(handoff.audit(self.state)['valid'])
        self.assertEqual(self.archive().read_bytes(), self.original)

    def test_cli_restore_keeps_changed_copy(self):
        self.register()
        self.source.write_bytes(b'changed for CLI')
        saved = self.root / 'changes.md'
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/handoff.py'), 'restore',
                                 '--state', str(self.state), '--file', str(self.source), '--save-current', str(saved)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['restored'])
        self.assertEqual(saved.read_bytes(), b'changed for CLI')
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
