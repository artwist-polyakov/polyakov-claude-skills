import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_deal import ROOT, deal, planned_actions

sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('freshness_handoff', ROOT / 'scripts/handoff.py')
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class ActionFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads((ROOT / 'assets/deal.example.json').read_text())
        self.state['schedule'].update(publication_at='2026-11-09T09:00:00+03:00', reporting_deadlines=planned_actions())
        self.now = deal.stamp('2026-11-01T12:00:00+03:00')
        self.action = self.state['schedule']['reporting_deadlines'][1]
        self.issued = deal.plan(self.state, self.now)

    def change_context(self):
        self.state['checks']['contract'] = {'status': 'pending', 'source': 'Реквизиты получены, подписи ещё нет'}

    def review(self):
        self.action['context_review'] = deal.action_review(
            self.state, self.action, 'Действие получить подписанный пакет всё ещё нужно: подписи нет', self.now)

    def event(self, plan):
        return next(event for event in plan['events'] if event['id'] == 'report-signing')

    def test_changed_check_blocks_old_action_even_with_new_duplicate_and_moved_time(self):
        self.change_context()
        self.action['at'] = '2026-11-02T10:00:00+03:00'
        self.action['title'] = 'Уточнённое название действия по получению подписей'
        duplicate = dict(self.action, id='another-signing', source='Уточнённое действие получить подписи')
        self.state['schedule']['reporting_deadlines'].append(duplicate)
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            deal.plan(self.state, self.now, previous=self.issued)
        self.assertEqual(self.state, before)

    def test_corrected_source_is_used_under_original_id_and_time(self):
        self.change_context()
        self.action['source'] = 'Реквизиты уже получены; получить подписи на подготовленном пакете'
        self.state['schedule']['revision'] += 1
        current = deal.plan(self.state, self.now, previous=self.issued)
        old, new = self.event(self.issued), self.event(current)
        self.assertEqual((new['id'], new['uid'], new['at']), (old['id'], old['uid'], old['at']))
        self.assertEqual(new['source'], self.action['source'])
        self.assertEqual(deal.changes(self.issued, current)['update'], [old['uid']])

    def test_explicit_review_preserves_calendar_bytes_sequence_and_action_text(self):
        self.change_context()
        self.review()
        current = deal.plan(self.state, self.now, previous=self.issued)
        self.assertEqual(deal.changes(self.issued, current), {'create': [], 'update': [], 'cancel': []})
        self.assertEqual(deal.ics(current, self.now), deal.ics(self.issued, self.now))
        old, new = self.event(self.issued), self.event(current)
        for key in ('title', 'source', 'at', 'uid', 'sequence'):
            self.assertEqual(new[key], old[key])
        self.assertNotEqual(new['context_sha256'], old['context_sha256'])
        self.assertIn('context_review', new)
        deal.validate_plan(self.state, current)

    def test_review_does_not_transfer_to_new_context_text_or_action_id(self):
        self.change_context()
        self.review()
        saved = copy.deepcopy(self.state)
        self.state['checks']['contract']['source'] = 'Изменилось условие подписания'
        with self.assertRaises(ValueError):
            deal.plan(self.state, self.now, previous=self.issued)
        self.state = copy.deepcopy(saved)
        action = self.state['schedule']['reporting_deadlines'][1]
        action['title'] = 'Получить подписи в новом согласованном порядке'
        self.assertNotIn('context_review', self.event(deal.plan(self.state, self.now)))
        with self.assertRaises(ValueError):
            deal.plan(self.state, self.now, previous=self.issued)
        self.state = copy.deepcopy(saved)
        action = self.state['schedule']['reporting_deadlines'][1]
        action['id'] = 'another-signing'
        new = next(e for e in deal.plan(self.state, self.now)['events'] if e['id'] == 'report-another-signing')
        self.assertNotIn('context_review', new)

    def test_unrelated_checks_and_recheck_time_do_not_invalidate_context(self):
        self.state['checks']['contract']['checked_at'] = self.now.isoformat()
        self.state['checks']['unrelated'] = {'source': 'Иной факт'}
        self.state['documents'] = [{'path': 'new-document.docx'}]
        current = deal.plan(self.state, self.now, previous=self.issued)
        self.assertEqual(self.event(current)['context_sha256'], self.event(self.issued)['context_sha256'])
        self.assertEqual(deal.changes(self.issued, current), {'create': [], 'update': [], 'cancel': []})

    def test_legacy_plan_needs_review_and_completed_action_needs_no_review(self):
        old = copy.deepcopy(self.issued)
        self.event(old).pop('context_sha256')
        with self.assertRaises(ValueError):
            deal.plan(self.state, self.now, previous=old)
        self.review()
        current = deal.plan(self.state, self.now, previous=old)
        self.assertEqual(deal.changes(old, current)['update'], [])
        self.change_context()
        self.state['completed_events']['report-signing'] = {
            'status': 'confirmed', 'source': 'Подписанный пакет получен', 'checked_at': self.now.isoformat()}
        self.state['checks']['contract'] = dict(self.state['completed_events']['report-signing'])
        completed = deal.plan(self.state, self.now, previous=self.issued)
        self.assertIn('report-signing', {event['id'] for event in completed['completed']})

    def test_cli_records_review_without_rewriting_action_or_checks(self):
        self.change_context()
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            state_path.write_text(json.dumps(self.state))
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/deal.py'), 'review-action',
                                     '--state', str(state_path), '--id', self.action['id'],
                                     '--reason', 'Подписанный пакет по-прежнему нужен', '--now', self.now.isoformat()],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            updated = json.loads(state_path.read_text())
            self.assertEqual(updated['checks'], self.state['checks'])
            item = updated['schedule']['reporting_deadlines'][1]
            self.assertEqual({k: v for k, v in item.items() if k != 'context_review'}, self.action)
            deal.plan(updated, self.now, previous=self.issued)

    def test_handoff_rechecks_issued_baseline_after_internal_rebuild(self):
        self.change_context()
        internal = deal.plan(self.state, self.now)
        deal.validate_plan(self.state, internal)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            state_path, old_path, current_path = (folder / name for name in ('state.json', 'issued.json', 'internal.json'))
            old_path.write_text(json.dumps(self.issued))
            current_path.write_text(json.dumps(internal))
            history = [({'plan': {'path': old_path.name, 'sha256': handoff.sha(old_path)}}, old_path, self.issued)]
            with self.assertRaises(ValueError):
                handoff.check_plan_issue_basis(self.state, state_path, current_path, internal, history)

    def test_declared_edo_requires_explicit_date_or_null(self):
        self.state['checks']['edo_exchange'] = {'status': 'pending', 'source': 'Дата пока не сообщена'}
        with self.assertRaises(ValueError):
            deal.validate(self.state)
        self.state['checks']['edo_exchange']['recovery_expected_on'] = None
        deal.validate(self.state)
        self.state['checks']['edo_exchange'].update(recovery_expected_on='2026-11-05',
                                                  recovery_source='Сообщение оператора', recovery_check_time='09:00')
        self.state['schedule']['reporting_deadlines'].append(dict(self.action, id='exchange', checks=['edo_exchange']))
        current = deal.plan(self.state, self.now)
        recovery = next(e for e in current['events'] if e['id'] == 'edo-recovery')
        self.assertEqual(recovery['at'], '2026-11-05T09:00:00+03:00')
        self.assertEqual(current['action_coverage']['edo_exchange'], ['report-exchange'])


if __name__ == '__main__':
    unittest.main()
