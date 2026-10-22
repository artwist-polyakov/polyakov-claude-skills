import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_deal import ROOT, deal, planned_actions


class EdoRecoveryDatesTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads((ROOT / 'assets/deal.example.json').read_text())
        self.state['schedule'].update(publication_at='2026-11-09T09:00:00+03:00', reporting_deadlines=planned_actions())
        self.now = deal.stamp('2026-11-01T12:00:00+03:00')
        self.recovery = {
            'status': 'pending', 'source': 'Обмен документами ещё не завершён',
            'recovery_expected_on': '2026-11-05',
            'recovery_source': 'messages/operator.txt: До 5 ноября недоступны приглашения',
            'recovery_check_time': '09:00'}
        self.state['checks']['edo_exchange'] = self.recovery
        self.exchange = {
            'id': 'finish-exchange', 'title': 'Проверить завершение обмена',
            'at': '2026-11-08T10:00:00+03:00', 'source': 'Сохранить квитанции обмена документами',
            'kind': 'organizational', 'checks': ['edo_exchange']}
        self.state['schedule']['reporting_deadlines'].append(self.exchange)

    def event(self, plan):
        return next(event for event in plan['events'] if event['id'] == 'edo-recovery')

    def test_legacy_format_and_default_day_preserve_time_without_mutating_state(self):
        before = copy.deepcopy(self.state)
        legacy = self.event(deal.plan(self.state, self.now))
        self.assertEqual(self.state, before)
        self.assertEqual(legacy['at'], '2026-11-05T09:00:00+03:00')
        self.assertEqual(legacy['recovery_expected_on'], '2026-11-05')
        self.assertEqual(legacy['recovery_check_on'], '2026-11-05')
        for value in (None, '2026-11-05'):
            self.recovery['recovery_check_on'] = value
            self.assertEqual(self.event(deal.plan(self.state, self.now)), legacy)

    def test_calendar_uses_chosen_day_and_keeps_reported_day_and_exact_source(self):
        self.recovery['recovery_check_on'] = '2026-11-06'
        before = copy.deepcopy(self.state)
        current = deal.plan(self.state, self.now)
        event = self.event(current)
        self.assertEqual(event['at'], '2026-11-06T09:00:00+03:00')
        self.assertEqual(event['recovery_expected_on'], '2026-11-05')
        self.assertEqual(event['recovery_check_on'], '2026-11-06')
        self.assertEqual(event['source'], self.recovery['recovery_source'])
        self.assertIn(self.recovery['recovery_source'], event['note'])
        calendar_event = next(chunk for chunk in deal.ics(current, self.now).split('BEGIN:VEVENT')
                              if event['uid'] in chunk)
        self.assertIn('DTSTART:20261106T060000Z', calendar_event)
        self.assertEqual(self.state, before)
        later = self.event(deal.plan(self.state, deal.hours(self.now, 240)))
        self.assertEqual(later['at'], event['at'])

    def test_valid_local_times_around_clock_changes_match_calendar(self):
        self.state['schedule'].update(timezone='Europe/Berlin', publication_at='2026-11-09T09:00:00+01:00')
        for day, hour, offset, utc in (
                ('2026-03-29', '01:30', '+01:00', '20260329T003000Z'),
                ('2026-03-29', '03:30', '+02:00', '20260329T013000Z'),
                ('2026-10-25', '01:30', '+02:00', '20261024T233000Z'),
                ('2026-10-25', '03:30', '+01:00', '20261025T023000Z')):
            with self.subTest(day=day, hour=hour):
                self.recovery.update(recovery_check_on=day, recovery_check_time=hour)
                before = copy.deepcopy(self.state)
                current = deal.plan(self.state, self.now)
                event = self.event(current)
                self.assertEqual(event['at'], f'{day}T{hour}:00{offset}')
                calendar_event = next(chunk for chunk in deal.ics(current, self.now).split('BEGIN:VEVENT')
                                      if event['uid'] in chunk)
                self.assertIn('DTSTART:' + utc, calendar_event)
                self.assertEqual(self.state, before)

    def test_invalid_local_time_cli_creates_no_plan_or_calendar(self):
        self.state['schedule'].update(timezone='Europe/Berlin', publication_at='2026-11-09T09:00:00+01:00')
        for field in ('recovery_expected_on', 'recovery_check_on'):
            for day, reason in (('2026-03-29', 'не существует'), ('2026-10-25', 'встречается дважды')):
                with self.subTest(field=field, day=day), tempfile.TemporaryDirectory() as directory:
                    self.state['checks']['edo_exchange'] = dict(self.recovery, **{field: day}, recovery_check_time='02:30')
                    state_path, output = Path(directory) / 'state.json', Path(directory) / 'plan'
                    state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
                    before = state_path.read_bytes()
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'scripts/deal.py'), 'plan', '--state', str(state_path),
                         '--now', self.now.isoformat(), '--output-dir', str(output)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn('edo_exchange.recovery_check_time', result.stderr)
                    self.assertIn(reason, result.stderr)
                    self.assertIn('Europe/Berlin', result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertFalse(output.exists())
                    self.assertEqual(state_path.read_bytes(), before)

    def test_changing_only_check_day_moves_same_event_without_changing_source(self):
        issued = deal.plan(self.state, self.now)
        old = self.event(issued)
        self.recovery['recovery_check_on'] = '2026-11-06'
        self.state['schedule']['revision'] += 1
        current = deal.plan(self.state, self.now, previous=issued)
        new = self.event(current)
        self.assertEqual(deal.changes(issued, current), {'create': [], 'update': [old['uid']], 'cancel': []})
        self.assertEqual(new['sequence'], old['sequence'] + 1)
        self.assertEqual(new['uid'], old['uid'])
        self.assertNotEqual(new['at'], old['at'])
        self.assertEqual((new['recovery_expected_on'], new['source']), (old['recovery_expected_on'], old['source']))

    def test_changing_reported_day_does_not_move_explicit_check_day(self):
        self.recovery['recovery_check_on'] = '2026-11-06'
        issued = deal.plan(self.state, self.now)
        self.recovery['recovery_expected_on'] = '2026-11-04'
        self.recovery['recovery_source'] = 'messages/update.txt: Приглашения восстановят 4 ноября'
        self.state['schedule']['revision'] += 1
        current = deal.plan(self.state, self.now, previous=issued)
        old, new = self.event(issued), self.event(current)
        self.assertEqual(new['at'], old['at'])
        self.assertEqual(new['recovery_expected_on'], '2026-11-04')
        self.assertEqual(new['source'], self.recovery['recovery_source'])
        self.assertEqual(deal.changes(issued, current)['update'], [old['uid']])
        deal.validate_plan(self.state, current)
        new['recovery_expected_on'] = new['recovery_check_on']
        with self.assertRaises(ValueError):
            deal.validate_plan(self.state, current)

    def test_unknown_reported_day_can_have_explicit_check_without_inventing_recovery(self):
        self.recovery.update(recovery_expected_on=None, recovery_check_on='2026-11-06',
                             recovery_source='messages/operator.txt: Срок восстановления пока неизвестен')
        event = self.event(deal.plan(self.state, self.now))
        self.assertIsNone(event['recovery_expected_on'])
        self.assertEqual(event['at'], '2026-11-06T09:00:00+03:00')
        for key in ('recovery_check_on', 'recovery_source', 'recovery_check_time'):
            self.recovery.pop(key)
        self.assertNotIn('edo-recovery', {event['id'] for event in deal.plan(self.state, self.now)['events']})

    def test_each_date_is_validated_even_when_the_other_is_valid(self):
        valid = dict(self.recovery, recovery_check_on='2026-11-06')
        for field in ('recovery_expected_on', 'recovery_check_on'):
            for value in ('2026-02-30', '2026-11-05T09:00:00+03:00', '', False, 20261105):
                with self.subTest(field=field, value=value):
                    self.state['checks']['edo_exchange'] = dict(valid, **{field: value})
                    with self.assertRaises(ValueError):
                        deal.plan(self.state, self.now)
        for field, value in (('recovery_source', ''), ('recovery_check_time', None)):
            self.state['checks']['edo_exchange'] = dict(valid, **{field: value})
            with self.assertRaises(ValueError):
                deal.plan(self.state, self.now)


if __name__ == '__main__':
    unittest.main()
