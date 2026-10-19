import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deal", ROOT / "scripts/deal.py")
deal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deal)


def planned_actions():
    # Явные организационные действия; время сохраняется при всех пересчётах.
    return [{"id": key, "title": title, "at": "2026-01-01T10:00:00+03:00",
             "source": "Учебное организационное действие, не договорный срок",
             "kind": "organizational", "checks": checks}
            for key, title, checks in (
                ("legality", "Проверить тематику и площадку", ["legality"]),
                ("signing", "Получить подписанный пакет", ["contract"]),
                ("marking", "Сверить пометку и рекламодателя в посте", ["marking"]),
                ("ord-check", "Получить токен и сверить полный материал ОРД", ["erid", "ord_creative"]),
                ("payment-check", "Проверить поступление предоплаты", ["payment"]),
                ("queue", "Проверить текущую очередь публикаций", ["scheduled_message"]),
                ("fee-period", "Выяснить применимость отчислений и квартал дохода", ["fee_period"]))]


class DealTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads((ROOT / "assets/deal.example.json").read_text())
        self.state["schedule"]["publication_at"] = "2026-11-09T09:00:00+03:00"
        # В основных сценариях срок согласован; незаполненная анкета проверяется отдельно.
        self.state["schedule"]["closing_after_end_hours"] = 48
        self.state["schedule"]["link_after_publication_hours"] = 1
        self.state["schedule"]["reporting_deadlines"] = planned_actions()
        self.now = deal.stamp("2026-11-01T12:00:00+03:00")

    def confirm_readiness(self):
        self.state["creative"]["text"] = "Реклама. Тестовый рекламодатель."
        proof = {"status": "confirmed", "source": "evidence/confirmation.txt", "checked_at": "2026-11-01T10:00:00+03:00"}
        for key in ("legality", "contract", "creative_approval", "marking", "erid", "ord_creative", "payment"):
            self.state["checks"][key] = dict(proof)
        for key in ("creative_approval", "marking", "erid", "ord_creative"):
            self.state["checks"][key]["creative_sha256"] = deal.digest(self.state["creative"])
        self.state["checks"]["erid"]["token"] = "test-not-a-real-erid"
        self.state["checks"]["marking"]["token"] = "test-not-a-real-erid"
        self.state["checks"]["ord_creative"].update(
            token="test-not-a-real-erid", verified={"text": True, "links": True, "media": True},
            source="evidence/ord-card.txt: полный текст, все ссылки, отсутствие медиа")
        self.state["checks"]["scheduled_message"] = {
            "status": "confirmed", "source": "evidence/current-scheduled-queue.png: отложенных записей нет",
            "checked_at": "2026-11-01T11:50:00+03:00",
            "publication_at": self.state["schedule"]["publication_at"],
            "scheduled_message_id": self.state["publication"]["scheduled_message_id"]}

    def review_current_actions(self):
        # Эти сценарии проверяют доказательства и завершение; общие учебные формулировки сохраняют смысл.
        for item in self.state["schedule"]["reporting_deadlines"]:
            if item.get("kind") == "organizational":
                item["context_review"] = deal.action_review(
                    self.state, item, "Общее учебное действие остаётся применимым после изменения доказательства", self.now)

    def test_documented_stages_are_valid_and_preserve_inactive_readiness_block(self):
        self.confirm_readiness()
        for stage in ("inquiry", "terms", "creative", "ready", "published", "closing", "closed", "paused", "cancelled"):
            with self.subTest(stage=stage):
                self.state["stage"] = stage
                before = copy.deepcopy(self.state)
                deal.validate(self.state)
                blockers = deal.readiness_issues(self.state, self.now)
                self.assertEqual("stage" in blockers, stage in ("closed", "paused", "cancelled"))
                self.assertEqual(self.state, before)

    def test_missing_unsupported_or_nonstring_stage_is_rejected_without_mutation(self):
        for stage in ("cancelld", "unknown", "", " ", None, True, 1, [], {}):
            with self.subTest(stage=stage):
                self.state["stage"] = stage
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "stage:"):
                    deal.validate(self.state)
                self.assertEqual(self.state, before)
        del self.state["stage"]
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "stage:"):
            deal.validate(self.state)
        self.assertEqual(self.state, before)

    def test_check_cli_rejects_stage_typo_in_otherwise_ready_deal_without_writing(self):
        self.confirm_readiness()
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        self.state["stage"] = "cancelld"
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "deal-state.json"
            state_path.write_text(json.dumps(self.state, ensure_ascii=False))
            before = state_path.read_bytes()
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/deal.py"), "check", "--state", str(state_path),
                 "--now", self.now.isoformat()], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("stage:", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(list(Path(folder).iterdir()), [state_path])

    def test_business_deadline_and_holiday(self):
        self.state["schedule"]["holidays"] = ["2026-11-06"]
        events = {e["id"]: e for e in deal.plan(self.state, self.now)["events"]}
        self.assertEqual(events["creative"]["at"], "2026-11-04T09:00:00+03:00")
        self.assertEqual(events["closing"]["at"], "2026-11-13T09:00:00+03:00")

    def test_unknown_claims_period_does_not_invent_a_deadline(self):
        self.state["schedule"]["claims_after_end_hours"] = None
        result = deal.plan(self.state, self.now)
        self.assertNotIn("claims", {e["id"] for e in result["events"]})
        self.assertTrue(any("Срок замечаний" in warning and "начало отсчёта" in warning
                            for warning in result["warnings"]))
        for count, expected in ((0, "2026-11-11T09:00:00+03:00"),
                                (24, "2026-11-12T09:00:00+03:00")):
            with self.subTest(hours=count):
                self.state["schedule"]["claims_after_end_hours"] = count
                result = deal.plan(self.state, self.now)
                event = next(e for e in result["events"] if e["id"] == "claims")
                self.assertEqual(event["at"], expected)
                self.assertFalse(any("Срок замечаний" in warning for warning in result["warnings"]))

    def test_link_deadline_is_explicit_and_unknown_does_not_create_event(self):
        for hours, expected in ((1, "2026-11-09T10:00:00+03:00"), (24, "2026-11-10T09:00:00+03:00")):
            with self.subTest(hours=hours):
                self.state["schedule"]["link_after_publication_hours"] = hours
                event = next(e for e in deal.plan(self.state, self.now)["events"] if e["id"] == "link")
                self.assertEqual(event["at"], expected)
        self.state["schedule"]["link_after_publication_hours"] = None
        unknown = deal.plan(self.state, self.now)
        self.assertNotIn("link", {e["id"] for e in unknown["events"]})
        self.assertTrue(any("Срок отправки ссылки" in warning for warning in unknown["warnings"]))
        del self.state["schedule"]["link_after_publication_hours"]
        legacy = next(e for e in deal.plan(self.state, self.now)["events"] if e["id"] == "link")
        self.assertEqual(legacy["at"], "2026-11-09T10:00:00+03:00")
        self.assertIn("организационный ориентир старого плана", legacy["note"])
        self.assertNotIn("по выбранному условию", legacy["note"])

    def test_unknown_closing_period_has_no_event_or_calendar_deadline(self):
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["revision"] += 1
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.state["schedule"].pop("closing_after_end_hours", None)
                else:
                    self.state["schedule"]["closing_after_end_hours"] = None
                result = deal.plan(self.state, self.now)
                self.assertNotIn("closing", {e["id"] for e in result["events"]})
                self.assertTrue(any("Срок выдачи закрывающих" in warning and "начало отсчёта" in warning
                                    for warning in result["warnings"]))
                self.assertNotIn("new-deal-closing@blogger-ads-skill", deal.ics(result, self.now))
                self.assertEqual(deal.changes(issued, result)["cancel"], ["new-deal-closing@blogger-ads-skill"])

    def test_init_does_not_assume_closing_period(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), "init", "--output", str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(path.read_text())
            self.assertIsNone(state["schedule"]["closing_after_end_hours"])
            self.assertIsNone(state["schedule"]["link_after_publication_hours"])
            state["schedule"]["publication_at"] = self.state["schedule"]["publication_at"]
            state["schedule"]["reporting_deadlines"] = planned_actions()
            self.assertNotIn("closing", {e["id"] for e in deal.plan(state, self.now)["events"]})

    def test_known_closing_period_keeps_zero_and_48_hours_after_placement_end(self):
        for actual, start in ((None, "2026-11-09T09:00:00+03:00"),
                              ("2026-11-10T10:00:00+03:00", "2026-11-10T10:00:00+03:00")):
            self.state["publication"]["actual_at"] = actual
            for count in (0, 48):
                with self.subTest(actual=actual, count=count):
                    self.state["schedule"]["closing_after_end_hours"] = count
                    result = deal.plan(self.state, self.now)
                    event = next(e for e in result["events"] if e["id"] == "closing")
                    self.assertEqual(event["at"], deal.hours(deal.stamp(start), 48 + count).isoformat())
                    self.assertFalse(any("Срок выдачи закрывающих" in warning for warning in result["warnings"]))

    def test_unspecified_deadline_cannot_hide_a_known_condition_or_source(self):
        for key in ("payment", "erid"):
            for condition, source in (("За один рабочий день", "Договор п.4"),
                                      ("За один рабочий день", None), (None, "Договор п.4"), ("", None)):
                with self.subTest(key=key, condition=condition, source=source):
                    self.state["schedule"]["deadline_terms"][key] = {
                        "precision": "unspecified", "condition": condition, "source": source}
                    with self.assertRaisesRegex(ValueError, "для известного условия выберите"):
                        deal.plan(self.state, self.now)
            for legacy in ({}, {"precision": "unspecified"},
                           {"precision": "unspecified", "condition": None, "source": None}):
                self.state["schedule"]["deadline_terms"][key] = legacy
                deal.validate(self.state)

    def test_known_deadline_precision_requires_nonempty_text_fields(self):
        for key in ("payment", "erid"):
            for precision in ("relative", "date", "datetime"):
                term = {"precision": precision, "condition": "Согласованное условие", "source": "Договор"}
                self.state["schedule"][key + "_due_at"] = "2026-11-06T18:00:00+03:00" if precision == "datetime" else None
                for field in ("condition", "source"):
                    for value in (None, "", "   ", True, 1, ["Договор"]):
                        with self.subTest(key=key, precision=precision, field=field, value=value):
                            self.state["schedule"]["deadline_terms"][key] = dict(term, **{field: value})
                            with self.assertRaisesRegex(ValueError, "непустыми строками"):
                                deal.validate(self.state)
                self.state["schedule"]["deadline_terms"][key] = term
                deal.validate(self.state)

    def test_relative_payment_condition_does_not_create_exact_deadline(self):
        self.state["schedule"]["deadline_terms"]["payment"] = {
            "precision": "relative", "condition": "За один рабочий день, час не согласован",
            "source": "Сообщение пользователя",
            "calculation": {"kind": "business_days_before_publication", "days": 1, "requires": []}}
        result = deal.plan(self.state, self.now)
        self.assertNotIn("payment", {e["id"] for e in result["events"]})
        self.state["schedule"]["payment_due_at"] = "2026-11-06T09:00:00+03:00"
        with self.assertRaisesRegex(ValueError, "точный час должен быть согласован"):
            deal.plan(self.state, self.now)

    def relative_payment(self, days=1, requires=None):
        self.state["schedule"]["publication_at"] = "2026-10-19T09:25:00+03:00"
        self.state["schedule"]["deadline_terms"]["payment"] = {
            "precision": "relative", "condition": "После подписания, за один рабочий день до публикации, без часа.",
            "source": "Сообщение пользователя.",
            "calculation": {"kind": "business_days_before_publication", "days": days,
                            "requires": ["contract"] if requires is None else requires}}
        self.now = deal.stamp("2026-10-16T20:20:00+03:00")

    def test_relative_deadline_requires_payment_and_explicit_prerequisite_on_friday(self):
        self.relative_payment()
        signing, payment = self.state["schedule"]["reporting_deadlines"][1], self.state["schedule"]["reporting_deadlines"][4]
        signing["at"], payment["at"] = "2026-10-17T09:00:00+03:00", "2026-10-17T10:00:00+03:00"
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "План опаздывает.*2026-10-16.*contract, payment"):
            deal.plan(self.state, self.now)
        self.assertEqual(self.state, before)
        signing["at"] = "2026-10-16T20:30:00+03:00"
        with self.assertRaisesRegex(ValueError, "План опаздывает.*payment"):
            deal.plan(self.state, self.now)
        payment["at"] = "2026-10-16T21:00:00+03:00"
        result = deal.plan(self.state, self.now)
        self.assertEqual(result["deadline_controls"][0]["date"], "2026-10-16")
        event = next(event for event in result["events"] if event["id"] == "report-payment-check")
        self.assertIn("немедленно проверить выполнимость", event["note"])
        self.assertIn("час обязательства им не устанавливается", event["note"])
        self.assertNotIn("..", event["note"])
        self.assertEqual(self.state["schedule"]["deadline_terms"]["payment"]["source"], "Сообщение пользователя.")
        self.assertIsNone(self.state["schedule"]["payment_due_at"])
        self.assertFalse(deal.check(self.state, self.now)["ready"])

    def test_relative_deadline_future_day_and_work_calendar_are_explicit(self):
        self.relative_payment()
        self.now = deal.stamp("2026-10-12T09:00:00+03:00")
        payment = self.state["schedule"]["reporting_deadlines"][4]
        payment["at"] = "2026-10-17T10:00:00+03:00"
        with self.assertRaisesRegex(ValueError, "не позже 2026-10-16"):
            deal.plan(self.state, self.now)
        payment["at"] = "2026-10-16T23:30:00+03:00"
        self.assertEqual(deal.plan(self.state, self.now)["deadline_controls"][0]["date"], "2026-10-16")
        payment["at"] = "2026-10-12T10:00:00+03:00"
        calculation = self.state["schedule"]["deadline_terms"]["payment"]["calculation"]
        for days, holidays, working, expected in (
                (2, [], [], "2026-10-15"), (2, ["2026-10-15"], [], "2026-10-14"),
                (2, [], ["2026-10-17"], "2026-10-16"),
                (3, ["2026-10-16"], ["2026-10-17"], "2026-10-14")):
            with self.subTest(days=days, holidays=holidays, working=working):
                calculation["days"] = days
                self.state["schedule"].update(holidays=holidays, working_days=working)
                self.assertEqual(deal.plan(self.state, self.now)["deadline_controls"][0]["date"], expected)

    def test_relative_deadline_only_uses_declared_prerequisites_and_does_not_reopen_paid(self):
        self.relative_payment(requires=[])
        self.state["schedule"]["reporting_deadlines"][1]["at"] = "2026-10-17T10:00:00+03:00"
        deal.plan(self.state, self.now)
        self.state["schedule"]["deadline_terms"]["payment"]["calculation"]["requires"] = ["contract"]
        with self.assertRaisesRegex(ValueError, "План опаздывает.*contract"):
            deal.plan(self.state, self.now)
        self.state["checks"]["payment"] = {"status": "confirmed", "source": "Выписка банка",
                                             "checked_at": self.now.isoformat()}
        deal.plan(self.state, self.now)
        issues = deal.readiness_issues(self.state, self.now)
        self.assertNotIn("payment", issues)
        self.assertIn("contract", issues)

    def test_relative_controls_remain_static_as_time_passes_and_overdue_actions_do_not_move(self):
        self.relative_payment()
        for action in self.state["schedule"]["reporting_deadlines"]:
            action["at"] = "2026-10-16T20:30:00+03:00"
        before = copy.deepcopy(self.state)
        issued = deal.plan(self.state, self.now)
        for at in ("2026-10-16T22:00:00+03:00", "2026-10-17T10:00:00+03:00"):
            with self.subTest(at=at):
                current = deal.plan(self.state, deal.stamp(at), previous=issued)
                self.assertEqual(deal.changes(issued, current), {"create": [], "update": [], "cancel": []})
                self.assertEqual(issued["deadline_controls"], current["deadline_controls"])
                for old, new in zip(issued["events"], current["events"]):
                    self.assertEqual({key: value for key, value in old.items() if key != "overdue"},
                                     {key: value for key, value in new.items() if key != "overdue"})
        self.assertEqual(self.state, before)

    def test_relative_without_calculation_keeps_fact_check_but_plan_requires_migration(self):
        self.confirm_readiness()
        self.state["schedule"]["deadline_terms"]["payment"] = {
            "precision": "relative", "condition": "За три дня после согласования", "source": "Договор"}
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        with self.assertRaisesRegex(ValueError, "deadline_terms.payment.calculation.*текст условия автоматически не разбирается"):
            deal.plan(self.state, self.now)

    def test_uncomputed_condition_uses_only_explicit_organizational_review_day(self):
        self.relative_payment()
        calculation = {"kind": "uncomputed", "reason": "Неизвестна дата получения счёта.",
                       "review_on": "2026-10-16", "requires": ["contract"]}
        self.state["schedule"]["deadline_terms"]["payment"].update(
            condition="После получения счёта", calculation=calculation)
        result = deal.plan(self.state, self.now)
        control = result["deadline_controls"][0]
        self.assertEqual(control["kind"], "uncomputed")
        self.assertIn("договорная дата не вычислена", control["note"])
        self.assertEqual(control["date"], calculation["review_on"])
        self.assertNotIn("payment", {event["id"] for event in result["events"]})
        self.state["schedule"]["reporting_deadlines"][4]["at"] = "2026-10-17T09:00:00+03:00"
        with self.assertRaisesRegex(ValueError, "План опаздывает.*payment"):
            deal.plan(self.state, self.now)

    def test_relative_calculation_rejects_ambiguous_structure(self):
        self.relative_payment()
        term = self.state["schedule"]["deadline_terms"]["payment"]
        for calculation in ([], {}, {"kind": "calendar_days_before_publication", "days": 1},
                            {"kind": "business_days_before_publication"},
                            *({"kind": "business_days_before_publication", "days": value} for value in (True, -1, 1.5, "1")),
                            *({"kind": "business_days_before_publication", "days": 1, "requires": value}
                              for value in ("contract", ["contract", "contract"], ["unknown"], ["payment"], [None])),
                            {"kind": "uncomputed", "reason": "", "review_on": "2026-10-16"},
                            {"kind": "uncomputed", "reason": "Нет даты", "review_on": "2026-10-16T09:00:00+03:00"}):
            with self.subTest(calculation=calculation):
                term["calculation"] = calculation
                with self.assertRaises(ValueError):
                    deal.validate(self.state)

    def test_exact_deadline_requires_original_condition_and_source(self):
        self.state["schedule"]["payment_due_at"] = "2026-11-06T18:00:00+03:00"
        self.state["schedule"]["deadline_terms"]["payment"] = {"precision": "datetime"}
        with self.assertRaisesRegex(ValueError, "исходное условие"):
            deal.plan(self.state, self.now)
        self.state["schedule"]["deadline_terms"]["payment"].update(
            condition="Предоплата до 6 ноября 18:00 МСК", source="Договор п.5.2")
        event = next(e for e in deal.plan(self.state, self.now)["events"] if e["id"] == "payment")
        self.assertEqual(event["at"], "2026-11-06T18:00:00+03:00")

    def test_generated_separator_does_not_duplicate_source_punctuation(self):
        self.state["schedule"]["payment_due_at"] = "2026-11-06T18:00:00+03:00"
        self.state["schedule"]["deadline_terms"]["payment"] = {
            "precision": "datetime", "condition": "До 6 ноября 18:00 МСК.", "source": "Договор, п.5.2."}
        before = copy.deepcopy(self.state)
        event = next(event for event in deal.plan(self.state, self.now)["events"] if event["id"] == "payment")
        self.assertIn("Согласованный срок: До 6 ноября 18:00 МСК. Основание: Договор, п.5.2.", event["note"])
        self.assertNotIn("..", event["note"])
        self.assertEqual(self.state, before)

    def test_completed_creative_no_longer_overdue_and_change_reopens_it(self):
        old = deal.plan(self.state, self.now)
        self.state["creative"]["text"] = "Утверждённый текст"
        self.state["checks"]["creative_approval"] = {
            "status": "confirmed", "source": "Согласование обеих сторон",
            "checked_at": "2026-11-01T10:00:00+03:00",
            "creative_sha256": deal.digest(self.state["creative"])}
        self.state["schedule"]["revision"] = 2
        result = deal.plan(self.state, deal.stamp("2026-11-08T12:00:00+03:00"))
        self.assertNotIn("creative", {e["id"] for e in result["events"]})
        completed = next(e for e in result["completed"] if e["id"] == "creative")
        self.assertFalse(completed["overdue"])
        self.assertNotIn(completed["uid"], deal.ics(result, self.now))
        self.assertIn(completed["uid"], deal.changes(old, result)["cancel"])
        self.state["creative"]["links"] = ["https://example.org/changed"]
        reopened = deal.plan(self.state, self.now)
        self.assertIn("creative", {e["id"] for e in reopened["events"]})

    def test_completion_requires_evidence_and_cannot_be_in_future(self):
        for proof in ({"status": "confirmed"},
                      {"status": "confirmed", "source": "Согласование",
                       "checked_at": "2026-11-03T10:00:00+03:00"}):
            self.state["completed_events"]["link"] = proof
            self.assertIn("link", {e["id"] for e in deal.plan(self.state, self.now)["events"]})
        self.state["completed_events"]["link"] = {
            "status": "confirmed", "source": "Пользователь отправил ссылку",
            "checked_at": "2026-11-01T10:00:00+03:00"}
        self.assertNotIn("link", {e["id"] for e in deal.plan(self.state, self.now)["events"]})

    def test_pending_erid_with_token_does_not_complete_registration(self):
        self.state["schedule"].update(erid_due_at="2026-11-06T18:00:00+03:00")
        self.state["schedule"]["deadline_terms"]["erid"] = {
            "precision": "datetime", "condition": "До 6 ноября 18:00 МСК", "source": "Договор"}
        self.state["checks"]["erid"] = {
            "status": "pending", "source": "Снимок поля Токен без раскрытых ссылок",
            "token": "example-token", "checked_at": "2026-11-01T10:00:00+03:00",
            "creative_sha256": deal.digest(self.state["creative"])}
        self.assertIn("erid", {e["id"] for e in deal.plan(self.state, self.now)["events"]})
        self.state["checks"]["erid"]["status"] = "confirmed"
        del self.state["checks"]["erid"]["token"]
        self.assertIn("erid", {e["id"] for e in deal.plan(self.state, self.now)["events"]})

    def test_issued_erid_requires_separate_ord_verification_to_remove_reminder(self):
        self.confirm_readiness()
        self.state["schedule"]["erid_due_at"] = "2026-11-06T18:00:00+03:00"
        self.state["schedule"]["deadline_terms"]["erid"] = {
            "precision": "datetime", "condition": "До 6 ноября 18:00 МСК", "source": "Договор"}
        registered = self.state["checks"].pop("ord_creative")
        before = copy.deepcopy(self.state)
        result = deal.check(self.state, self.now)
        self.assertFalse(result["ready"])
        self.assertIn("Сверка зарегистрированной версии креатива в ОРД", result["missing"])
        pending = deal.plan(self.state, self.now)
        event = next(e for e in pending["events"] if e["id"] == "erid")
        self.assertIn(event["uid"], deal.ics(pending, self.now))
        self.assertEqual(self.state, before)

        self.state["checks"]["ord_creative"] = registered
        self.state["schedule"]["revision"] += 1
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        ready = deal.plan(self.state, self.now)
        completed = next(e for e in ready["completed"] if e["id"] == "erid")
        self.assertEqual(completed["completion_source"], registered["source"])
        self.assertNotIn(event["uid"], deal.ics(ready, self.now))
        self.assertIn(event["uid"], deal.changes(pending, ready)["cancel"])

    def test_ord_verification_requires_all_three_explicit_booleans(self):
        self.confirm_readiness()
        registered = copy.deepcopy(self.state["checks"]["ord_creative"])
        for part in ("text", "links", "media"):
            for value in (False, None, 1, "true"):
                with self.subTest(part=part, value=value):
                    proof = copy.deepcopy(registered)
                    if value is None:
                        del proof["verified"][part]
                    else:
                        proof["verified"][part] = value
                    self.state["checks"]["ord_creative"] = proof
                    self.assertFalse(deal.check(self.state, self.now)["ready"])
                    self.assertIsNone(deal.completion(self.state, "erid", self.now))
        for verified in (None, {}, True):
            with self.subTest(verified=verified):
                self.state["checks"]["ord_creative"] = dict(registered, verified=verified)
                self.assertFalse(deal.check(self.state, self.now)["ready"])
                self.assertIsNone(deal.completion(self.state, "erid", self.now))

    def test_ord_verification_requires_matching_token_version_and_evidence(self):
        self.confirm_readiness()
        registered = self.state["checks"]["ord_creative"]
        for key, value in (("token", "another-token"), ("creative_sha256", "another-version"),
                           ("status", "pending"), ("source", None), ("checked_at", None),
                           ("checked_at", "2026-11-02T10:00:00+03:00")):
            with self.subTest(key=key, value=value):
                self.state["checks"]["ord_creative"] = dict(registered, **{key: value})
                self.assertFalse(deal.check(self.state, self.now)["ready"])
                self.assertIsNone(deal.completion(self.state, "erid", self.now))

    def test_reschedule_stable_ids_and_revision(self):
        old = deal.plan(self.state, self.now)
        self.state["schedule"]["publication_at"] = "2026-11-10T09:00:00+03:00"
        with self.assertRaises(ValueError):
            deal.changes(old, deal.plan(self.state, self.now))
        self.state["schedule"]["revision"] = 2
        new = deal.plan(self.state, self.now)
        diff = deal.changes(old, new)
        moving = [event for event in new["events"]
                  if event.get("kind") != "organizational" and event["id"] != "ord-2026-11"]
        self.assertEqual(set(diff["update"]), {event["uid"] for event in moving})
        self.assertNotIn("new-deal-ord-2026-11@blogger-ads-skill", diff["update"])
        self.assertEqual(diff["create"], [])
        self.assertEqual(diff["cancel"], [])

    def test_sequences_use_last_issued_baseline_for_each_internal_rebuild(self):
        issued = deal.plan(self.state, self.now)
        original = copy.deepcopy(issued)
        self.state["schedule"]["revision"] = 2
        self.state["schedule"]["closing_after_end_hours"] = 24
        internal = deal.plan(self.state, self.now)
        first_changes = deal.changes(issued, internal)
        self.assertEqual(first_changes["update"], ["new-deal-closing@blogger-ads-skill"])
        self.state["schedule"]["revision"] = 3
        final = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(issued, final), first_changes)
        self.assertEqual(issued, original)
        for event in final["events"]:
            self.assertEqual(event["sequence"], 2 if event["id"] == "closing" else 1)
        calendar = deal.ics(final, self.now)
        self.assertIn("UID:new-deal-closing@blogger-ads-skill\r\nSEQUENCE:2", calendar)
        self.assertIn("UID:new-deal-publication@blogger-ads-skill\r\nSEQUENCE:1", calendar)
        # Отказ от внутренней правки тоже сравнивается с выданной базой.
        self.state["schedule"]["closing_after_end_hours"] = 48
        restored = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(issued, restored), {"create": [], "update": [], "cancel": []})
        self.assertTrue(all(event["sequence"] == 1 for event in restored["events"]))

    def test_sequence_changes_for_date_meaning_title_description_and_status(self):
        issued = deal.plan(self.state, self.now)
        event = next(event for event in issued["events"] if event["id"] == "ord-2026-11")
        for field, value in (("at", "2026-12-29T09:00:00+03:00"), ("title", "Уточнённая задача"),
                             ("note", "Новое основание"), ("legal_date", "2026-12-30"),
                             ("applicability", "applicable"), ("status", "needs_attention")):
            with self.subTest(field=field):
                current = copy.deepcopy(issued)
                current["revision"] += 1
                changed = next(item for item in current["events"] if item["id"] == event["id"])
                changed[field] = value
                self.assertEqual(deal.changes(issued, current)["update"], [event["uid"]])
                self.assertEqual(changed["sequence"], event["sequence"] + 1)
                self.assertTrue(all(item["sequence"] == 1 for item in current["events"] if item["id"] != event["id"]))

    def test_sequence_completion_reopening_and_clock_only_changes(self):
        issued = deal.plan(self.state, self.now)
        later = deal.plan(self.state, deal.stamp("2026-12-31T12:00:00+03:00"))
        self.assertNotEqual(next(event["overdue"] for event in issued["events"] if event["id"] == "creative"),
                            next(event["overdue"] for event in later["events"] if event["id"] == "creative"))
        self.assertEqual(deal.changes(issued, later), {"create": [], "update": [], "cancel": []})
        self.assertEqual([event["sequence"] for event in issued["events"]], [event["sequence"] for event in later["events"]])
        self.state["completed_events"]["link"] = {"status": "confirmed", "source": "Ссылка отправлена",
                                                 "checked_at": "2026-11-10T10:00:00+03:00"}
        self.state["schedule"]["revision"] = 2
        closed = deal.plan(self.state, deal.stamp("2026-11-11T12:00:00+03:00"))
        self.assertEqual(deal.changes(issued, closed)["cancel"], ["new-deal-link@blogger-ads-skill"])
        self.assertEqual(next(event for event in closed["completed"] if event["id"] == "link")["sequence"], 2)
        self.state["completed_events"] = {}
        self.state["schedule"]["revision"] = 3
        reopened = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(closed, reopened)["create"], ["new-deal-link@blogger-ads-skill"])
        self.assertEqual(next(event for event in reopened["events"] if event["id"] == "link")["sequence"], 3)

    def test_legacy_plan_without_sequences_uses_its_revision(self):
        issued = deal.plan(self.state, self.now)
        issued.pop("completed")
        issued["revision"] = 7
        for event in issued["events"]:
            event.pop("sequence")
        self.state["schedule"]["revision"] = 9
        self.state["schedule"]["closing_after_end_hours"] = 24
        current = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(issued, current)["update"], ["new-deal-closing@blogger-ads-skill"])
        self.assertTrue(all(event["sequence"] == (8 if event["id"] == "closing" else 7) for event in current["events"]))

    def reporting_deadline(self, event_id, at="2026-11-30T18:00:00+03:00"):
        return {"id": event_id, "title": "Проверить отчётность", "at": at,
                "source": "Согласованный срок проверки"}

    def test_reporting_insertion_preserves_existing_ids(self):
        deadlines = [*planned_actions(), self.reporting_deadline("custom-2026-10"),
                     self.reporting_deadline("fee-q4", "2027-03-05T18:00:00+03:00")]
        self.state["schedule"]["reporting_deadlines"] = deadlines
        old = deal.plan(self.state, self.now)
        deadlines.insert(0, self.reporting_deadline("edo", "2026-11-02T12:00:00+03:00"))
        self.state["schedule"]["revision"] = 2
        new = deal.plan(self.state, self.now)
        old_ids = {e["id"]: e["uid"] for e in old["events"]}
        new_ids = {e["id"]: e["uid"] for e in new["events"]}
        self.assertEqual(old_ids, {key: new_ids[key] for key in old_ids})
        self.assertEqual(deal.changes(old, new),
                         {"create": [new_ids["report-edo"]], "update": [], "cancel": []})

    def test_reporting_reordering_does_not_change_events(self):
        deadlines = [*planned_actions(), self.reporting_deadline("custom-2026-10"), self.reporting_deadline("fee-q4")]
        self.state["schedule"]["reporting_deadlines"] = deadlines
        old = deal.plan(self.state, self.now)
        deadlines.reverse()
        new = deal.plan(self.state, self.now)
        self.assertEqual({e["id"]: e["uid"] for e in old["events"]},
                         {e["id"]: e["uid"] for e in new["events"]})
        self.assertEqual(deal.changes(old, new), {"create": [], "update": [], "cancel": []})

    def test_reporting_date_and_title_changes_keep_identity(self):
        deadline = self.reporting_deadline("ORD_2026-10")
        self.state["schedule"]["reporting_deadlines"] = [*planned_actions(), deadline]
        old = deal.plan(self.state, self.now)
        old_event = next(e for e in old["events"] if e["id"] == "report-ORD_2026-10")
        deadline.update(title="Проверить исправленный отчёт", at="2026-11-29T17:00:00+03:00")
        self.state["schedule"]["revision"] = 2
        new = deal.plan(self.state, self.now)
        new_event = next(e for e in new["events"] if e["id"] == old_event["id"])
        self.assertEqual(old_event["uid"], new_event["uid"])
        self.assertEqual(deal.changes(old, new),
                         {"create": [], "update": [old_event["uid"]], "cancel": []})

    def test_reporting_rejects_missing_empty_and_invalid_ids(self):
        for event_id in (None, "", " ", 1, True, [], {}, "ord/10", "ord@10", "ord:10", "отчёт", "ord\n"):
            with self.subTest(event_id=event_id):
                self.state["schedule"]["reporting_deadlines"] = [self.reporting_deadline(event_id)]
                with self.assertRaisesRegex(ValueError, "reporting_deadlines.id"):
                    deal.plan(self.state, self.now)
        deadline = self.reporting_deadline("ord")
        del deadline["id"]
        self.state["schedule"]["reporting_deadlines"] = [deadline]
        with self.assertRaisesRegex(ValueError, "reporting_deadlines.id"):
            deal.plan(self.state, self.now)

    def test_reporting_rejects_duplicate_ids(self):
        self.state["schedule"]["reporting_deadlines"] = [
            self.reporting_deadline("ord"), self.reporting_deadline("ord", "2026-12-01T18:00:00+03:00")]
        with self.assertRaisesRegex(ValueError, "повторяется id ord"):
            deal.plan(self.state, self.now)

    def test_pending_facts_allow_plan_with_explicit_actions_without_confirming_them(self):
        before = copy.deepcopy(self.state)
        result = deal.plan(self.state, self.now)
        self.assertEqual(result["action_coverage"]["contract"], ["report-signing"])
        self.assertEqual(result["action_coverage"]["ord_creative"], ["report-ord-check"])
        self.assertEqual(result["action_coverage"]["creative_approval"], ["creative"])
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.assertEqual(before, self.state)

    def test_plan_rejects_missing_actions_but_fact_check_still_returns_its_result(self):
        for removed, key in (("signing", "contract"), ("ord-check", "ord_creative"),
                             ("queue", "scheduled_message")):
            with self.subTest(check=key):
                self.state["schedule"]["reporting_deadlines"] = [
                    item for item in planned_actions() if item["id"] != removed]
                with self.assertRaisesRegex(ValueError, f"План неполон:.*{key}"):
                    deal.plan(self.state, self.now)
                self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.state["schedule"]["reporting_deadlines"] = []
        with self.assertRaisesRegex(ValueError, "Общая readiness"):
            deal.plan(self.state, self.now)

    def test_action_after_publication_does_not_cover_a_prepublication_check(self):
        action = next(item for item in self.state["schedule"]["reporting_deadlines"] if item["id"] == "signing")
        action["at"] = "2026-11-09T09:01:00+03:00"
        with self.assertRaisesRegex(ValueError, "План неполон:.*contract"):
            deal.plan(self.state, self.now)
        action["at"] = self.state["schedule"]["publication_at"]
        self.assertEqual(deal.plan(self.state, self.now)["action_coverage"]["contract"], ["report-signing"])

    def test_optional_edo_action_does_not_change_publication_readiness(self):
        self.confirm_readiness()
        self.state["schedule"]["fee_obligation"] = self.fee_settings("not_applicable", [])
        self.state["schedule"]["reporting_deadlines"] = []
        self.state["checks"]["edo_exchange"] = {"status": "pending", "source": "Сбой оператора, обмен ещё не завершён", "recovery_expected_on": None}
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        with self.assertRaisesRegex(ValueError, "План неполон:.*edo_exchange"):
            deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"] = [{
            "id": "restore-edo", "title": "Проверить восстановление ЭДО и отправку оригиналов",
            "at": "2026-11-12T15:00:00+03:00", "source": "Сбой оператора; проверить канал и статус обмена",
            "kind": "organizational", "checks": ["edo_exchange"]}]
        self.assertEqual(deal.plan(self.state, self.now)["action_coverage"], {"edo_exchange": ["report-restore-edo"]})
        self.state["publication"]["actual_at"] = "2026-11-09T09:00:00+03:00"
        self.state["stage"] = "closing"
        self.assertEqual(deal.plan(self.state, self.now)["action_coverage"], {"edo_exchange": ["report-restore-edo"]})

    def edo_recovery(self):
        self.confirm_readiness()
        self.state["checks"]["edo_exchange"] = {
            "status": "pending", "source": "Обмен ещё не завершён из-за сбоя оператора",
            "recovery_expected_on": "2026-11-05", "recovery_source": "Оператор ожидает восстановление 5 ноября.",
            "recovery_check_time": "09:00"}
        action = {"id": "finish-edo-exchange", "title": "Проверить отправку и получение оригиналов",
                  "at": "2026-11-06T10:00:00+03:00", "source": "Контроль завершения обмена после восстановления сервиса",
                  "kind": "organizational", "checks": ["edo_exchange"]}
        self.state["schedule"]["reporting_deadlines"].append(action)
        return action

    def test_edo_recovery_is_separate_from_exchange_signatures_and_ord(self):
        action = self.edo_recovery()
        before = copy.deepcopy(self.state)
        result = deal.plan(self.state, self.now)
        recovery = next(event for event in result["events"] if event["id"] == "edo-recovery")
        self.assertEqual(recovery["at"], "2026-11-05T09:00:00+03:00")
        self.assertEqual(recovery["service"], "edo")
        self.assertEqual(result["action_coverage"]["edo_exchange"], ["report-finish-edo-exchange"])
        self.assertIn("не подтверждает подписи", recovery["note"])
        self.assertIn("не относится к ОРД", recovery["note"])
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        self.assertEqual(self.state, before)
        self.state["schedule"]["reporting_deadlines"].remove(action)
        with self.assertRaisesRegex(ValueError, "План неполон:.*edo_exchange"):
            deal.plan(self.state, self.now)

    def test_edo_recovery_completion_is_independent_of_exchange_completion(self):
        self.edo_recovery()
        self.state["checks"]["contract"] = {"status": "pending", "source": "Подпись ещё не получена"}
        issued = deal.plan(self.state, self.now)
        later = deal.stamp("2026-11-06T12:00:00+03:00")
        proof = {"status": "confirmed", "source": "Сервис доступен, документы ещё ожидают отправки",
                 "checked_at": "2026-11-05T09:05:00+03:00"}
        self.state["completed_events"]["edo-recovery"] = proof
        self.state["schedule"]["revision"] += 1
        self.review_current_actions()
        current = deal.plan(self.state, later, previous=issued)
        self.assertIn("edo-recovery", {event["id"] for event in current["completed"]})
        self.assertIn("report-finish-edo-exchange", {event["id"] for event in current["events"]})
        self.assertEqual(self.state["checks"]["edo_exchange"]["status"], "pending")
        self.assertEqual(self.state["checks"]["contract"]["status"], "pending")
        self.assertEqual(deal.changes(issued, current)["cancel"], ["new-deal-edo-recovery@blogger-ads-skill"])
        self.state["completed_events"]["edo-recovery"] = {"status": "pending"}
        self.state["checks"]["edo_exchange"].update(proof)
        self.review_current_actions()
        reopened = deal.plan(self.state, later, previous=current)
        self.assertIn("edo-recovery", {event["id"] for event in reopened["events"]})
        self.assertNotIn("edo_exchange", reopened["action_coverage"])

    def test_edo_recovery_rejects_reserved_manual_duplicate_but_unknown_date_stays_optional(self):
        self.edo_recovery()
        self.state["schedule"]["reporting_deadlines"].append({
            "id": "edo-recovery", "title": "Произвольное название без поиска по тексту",
            "at": "2026-11-05T09:00:00+03:00", "source": "Ручной дубль"})
        with self.assertRaisesRegex(ValueError, "edo-recovery зарезервирован"):
            deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"].pop()
        self.state["checks"]["edo_exchange"] = {"status": "pending", "source": "Дата восстановления ещё неизвестна", "recovery_expected_on": None}
        result = deal.plan(self.state, self.now)
        self.assertNotIn("edo-recovery", {event["id"] for event in result["events"]})
        self.assertEqual(result["action_coverage"]["edo_exchange"], ["report-finish-edo-exchange"])

    def test_edo_recovery_time_and_sequence_stay_fixed_until_explicit_change(self):
        self.edo_recovery()
        issued = deal.plan(self.state, self.now)
        later = deal.stamp("2026-11-06T12:00:00+03:00")
        self.review_current_actions()
        current = deal.plan(self.state, later, previous=issued)
        self.assertEqual(deal.changes(issued, current), {"create": [], "update": [], "cancel": []})
        old = next(event for event in issued["events"] if event["id"] == "edo-recovery")
        new = next(event for event in current["events"] if event["id"] == old["id"])
        self.assertFalse(old["overdue"])
        self.assertTrue(new["overdue"])
        self.assertEqual({key: value for key, value in old.items() if key != "overdue"},
                         {key: value for key, value in new.items() if key != "overdue"})
        self.state["checks"]["edo_exchange"]["recovery_expected_on"] = "2026-11-07"
        self.state["checks"]["edo_exchange"]["recovery_source"] = "Оператор уточнил дату: 7 ноября"
        self.state["schedule"]["revision"] += 1
        self.review_current_actions()
        moved = deal.plan(self.state, later, previous=issued)
        self.assertEqual(deal.changes(issued, moved), {"create": [], "update": [old["uid"]], "cancel": []})

    def test_edo_recovery_requires_date_source_and_organizational_time(self):
        self.edo_recovery()
        valid = copy.deepcopy(self.state["checks"]["edo_exchange"])
        for key, value in (("recovery_expected_on", "2026-02-30"), ("recovery_expected_on", "2026-11-05T09:00:00+03:00"),
                           ("recovery_expected_on", None), ("recovery_source", ""),
                           ("recovery_check_time", None), ("recovery_check_time", "24:00"), ("recovery_check_time", "09:00:00")):
            with self.subTest(key=key, value=value):
                self.state["checks"]["edo_exchange"] = dict(valid, **{key: value})
                with self.assertRaises(ValueError):
                    deal.validate(self.state)

    def test_existing_erid_and_payment_events_cover_actions_without_new_duplicates(self):
        self.confirm_readiness()
        self.state["schedule"]["reporting_deadlines"] = []
        for key in ("payment", "erid"):
            self.state["checks"][key]["status"] = "pending"
            self.state["schedule"][key + "_due_at"] = "2026-11-06T18:00:00+03:00"
            self.state["schedule"]["deadline_terms"][key] = {
                "precision": "datetime", "condition": "До 6 ноября 18:00 МСК", "source": "Учебный договор"}
        self.state["checks"]["ord_creative"]["status"] = "pending"
        self.state["schedule"]["fee_obligation"] = self.fee_settings("not_applicable", [])
        result = deal.plan(self.state, self.now)
        self.assertEqual(result["action_coverage"], {"erid": ["erid"], "ord_creative": ["erid"], "payment": ["payment"]})
        self.assertFalse(any(event["id"].startswith("report-") for event in result["events"]))
        for key in ("payment", "erid"):
            self.assertEqual(sum(event["id"] == key for event in result["events"]), 1)

    def test_stale_ord_confirmation_still_needs_an_action(self):
        self.confirm_readiness()
        self.state["schedule"]["reporting_deadlines"] = []
        registered = copy.deepcopy(self.state["checks"]["ord_creative"])
        for change in ({"creative_sha256": "old"}, {"token": "other-token"},
                       {"verified": {"text": True, "links": False, "media": True}}):
            with self.subTest(change=change):
                self.state["checks"]["ord_creative"] = dict(registered, **change)
                with self.assertRaisesRegex(ValueError, "План неполон:.*ord_creative"):
                    deal.plan(self.state, self.now)
                self.assertFalse(deal.check(self.state, self.now)["ready"])

    def test_organizational_action_schema_rejects_missing_or_ambiguous_inputs(self):
        for change in ({"kind": None}, {"checks": []}, {"checks": "contract"},
                       {"checks": ["contract", "contract"]}, {"checks": ["typo"]},
                       {"checks": [None]}, {"source": " "}, {"title": " "},
                       {"at": "сейчас"}, {"at": "2026-11-02T10:00:00"}):
            with self.subTest(change=change):
                actions = planned_actions()
                actions[1].update(change)
                self.state["schedule"]["reporting_deadlines"] = actions
                with self.assertRaises(ValueError):
                    deal.plan(self.state, self.now)

    def test_organizational_action_time_and_sequence_do_not_follow_now(self):
        for item in self.state["schedule"]["reporting_deadlines"]:
            item["at"] = "2026-11-02T10:00:00+03:00"
        issued = deal.plan(self.state, self.now)
        later = deal.plan(self.state, deal.stamp("2026-11-03T12:00:00+03:00"))
        self.assertEqual(deal.changes(issued, later), {"create": [], "update": [], "cancel": []})
        old = next(event for event in issued["events"] if event["id"] == "report-signing")
        new = next(event for event in later["events"] if event["id"] == old["id"])
        self.assertFalse(old["overdue"])
        self.assertTrue(new["overdue"])
        self.assertEqual((old["at"], old["uid"], old["sequence"]), (new["at"], new["uid"], new["sequence"]))
        self.state["schedule"]["reporting_deadlines"][1]["at"] = "2026-11-03T15:00:00+03:00"
        self.state["schedule"]["revision"] += 1
        moved = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(issued, moved), {"create": [], "update": [old["uid"]], "cancel": []})
        self.assertEqual(next(event["sequence"] for event in moved["events"] if event["uid"] == old["uid"]), 2)

    def test_previous_plan_rejects_new_id_for_same_open_checks(self):
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["revision"] += 1
        for index in (0, 3, 5):
            with self.subTest(action=planned_actions()[index]["id"]):
                actions = planned_actions()
                action = actions[index]
                old_id = action["id"]
                action.update(id="renamed-" + old_id, title="Уточнённая формулировка действия",
                              at="2026-11-03T15:00:00+03:00", checks=list(reversed(action["checks"])))
                self.state["schedule"]["reporting_deadlines"] = actions
                with self.assertRaisesRegex(ValueError, f"report-{old_id} заменено новым report-renamed-{old_id}.*тем же набором checks"):
                    deal.plan(self.state, self.now, previous=issued)
                action["id"] = old_id
                current = deal.plan(self.state, self.now, previous=issued)
                delta = deal.changes(issued, current)
                self.assertEqual(delta, {"create": [], "update": [f"new-deal-report-{old_id}@blogger-ads-skill"], "cancel": []})

    def test_previous_plan_keeps_id_when_only_completed_checks_are_removed(self):
        action = self.state["schedule"]["reporting_deadlines"][3]
        action["checks"] = ["creative_approval", "erid", "marking", "ord_creative"]
        issued = deal.plan(self.state, self.now)
        self.confirm_readiness()
        self.state["checks"]["ord_creative"]["status"] = "pending"
        self.state["schedule"]["revision"] += 1
        action.update(id="renamed-ord-check", checks=["erid", "marking", "ord_creative"])
        with self.assertRaisesRegex(ValueError, "report-ord-check заменено новым report-renamed-ord-check.*незавершённых checks \\(ord_creative\\)"):
            deal.plan(self.state, self.now, previous=issued)
        action["id"] = "ord-check"
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=issued)
        delta = deal.changes(issued, current)
        old = next(event for event in issued["events"] if event["id"] == "report-ord-check")
        new = next(event for event in current["events"] if event["id"] == old["id"])
        self.assertEqual(new["uid"], old["uid"])
        self.assertEqual(new["sequence"], old["sequence"] + 1)
        self.assertIn(old["uid"], delta["update"])
        self.assertNotIn(old["uid"], delta["cancel"])
        self.assertEqual(delta["create"], [])

    def test_remaining_work_does_not_discard_stale_partial_or_future_confirmations(self):
        self.confirm_readiness()
        action = self.state["schedule"]["reporting_deadlines"][3]
        action["checks"] = ["creative_approval", "erid", "marking", "ord_creative"]
        issued = deal.plan(self.state, self.now)
        original_checks = copy.deepcopy(self.state["checks"])
        cases = (("creative_approval", {"creative_sha256": "another-version"}),
                 ("creative_approval", {"checked_at": "2026-11-02T12:00:00+03:00"}),
                 ("marking", {"token": "another-token"}),
                 ("ord_creative", {"verified": {"text": True, "links": False, "media": True}}))
        for omitted, change in cases:
            with self.subTest(omitted=omitted, change=change):
                self.state["checks"] = copy.deepcopy(original_checks)
                self.state["checks"][omitted].update(change)
                # Две открытые проверки действительно разделяются, а не переименовываются.
                other = "ord_creative" if omitted != "ord_creative" else "creative_approval"
                self.state["checks"][other]["status"] = "pending"
                action["id"] = "remaining-ord-check"
                action["checks"] = [key for key in ("creative_approval", "erid", "marking", "ord_creative") if key != omitted]
                separate = dict(action, id="separate-check", checks=[omitted])
                self.state["schedule"]["reporting_deadlines"].append(separate)
                self.review_current_actions()
                current = deal.plan(self.state, self.now, previous=issued)
                self.assertIn("report-remaining-ord-check", {event["id"] for event in current["events"]})
                self.assertIn("report-separate-check", {event["id"] for event in current["events"]})
                self.state["schedule"]["reporting_deadlines"].remove(separate)

    def test_remaining_work_includes_edo_and_unknown_fee_period(self):
        self.confirm_readiness()
        proof = dict(self.state["checks"]["contract"])
        for key in ("edo_exchange", "fee_period"):
            with self.subTest(check=key):
                action = dict(planned_actions()[1], id="combined", checks=["contract", key])
                self.state["checks"][key] = {"status": "pending", "recovery_expected_on": None} if key == "edo_exchange" else proof
                self.state["schedule"]["reporting_deadlines"].append(action)
                issued = deal.plan(self.state, self.now)
                action.update(id="renamed-combined", checks=[key])
                with self.assertRaisesRegex(ValueError, f"незавершённых checks \\({key}\\)"):
                    deal.plan(self.state, self.now, previous=issued)
                self.state["schedule"]["reporting_deadlines"].remove(action)
                self.state["checks"].pop(key)

    def test_previous_plan_allows_split_checks_and_deletion_without_replacement(self):
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["revision"] += 1
        actions = self.state["schedule"]["reporting_deadlines"]
        old = actions.pop(3)
        actions.extend([dict(old, id="token", checks=["erid"]),
                        dict(old, id="full-creative", checks=["ord_creative"])])
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertEqual(len(deal.changes(issued, current)["create"]), 2)
        self.assertEqual(deal.changes(issued, current)["cancel"], ["new-deal-report-ord-check@blogger-ads-skill"])
        # Уже существующее действие с теми же checks не является новой заменой удалённого.
        actions = planned_actions() + [dict(planned_actions()[1], id="second-signing")]
        self.state["schedule"]["reporting_deadlines"] = actions
        issued = deal.plan(self.state, self.now)
        actions.remove(actions[1])
        self.state["schedule"]["revision"] += 1
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertEqual(deal.changes(issued, current),
                         {"create": [], "update": [], "cancel": ["new-deal-report-signing@blogger-ads-skill"]})

    def test_previous_plan_allows_new_action_after_proven_completion(self):
        issued = deal.plan(self.state, self.now)
        self.state["checks"]["contract"] = {"status": "confirmed", "checked_at": self.now.isoformat(),
                                               "source": "Подписанная первая редакция"}
        action = self.state["schedule"]["reporting_deadlines"][1]
        action["id"] = "next-signing"
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertIn("report-signing", {event["id"] for event in current["completed"]})
        self.assertIn("report-next-signing", {event["id"] for event in current["events"]})
        action["id"] = "signing"
        self.state["completed_events"]["report-signing"] = dict(self.state["checks"]["contract"])
        completed = deal.plan(self.state, self.now)
        action["id"] = "new-version-signing"
        self.state["checks"]["contract"] = {"status": "pending", "source": "Подпись новой редакции"}
        current = deal.plan(self.state, self.now, previous=completed)
        self.assertIn("report-new-version-signing", {event["id"] for event in current["events"]})

    def test_plan_cli_rejects_renamed_open_action_before_export(self):
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"][5]["id"] = "another-queue-id"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            state_path, previous_path = folder / "state.json", folder / "issued-plan.json"
            state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            previous_path.write_text(json.dumps(issued, ensure_ascii=False), encoding="utf-8")
            output = folder / "new-plan"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), "plan", "--state", str(state_path),
                                     "--previous", str(previous_path), "--output-dir", str(output), "--now", self.now.isoformat()],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Сохраните прежний reporting_deadlines.id", result.stderr)
            self.assertFalse(output.exists())

    def test_duplicate_payment_flag_is_rejected_but_receipt_details_are_allowed(self):
        self.confirm_readiness()
        receipt = {"payment_amount": "50000.00", "payment_received_at": self.now.isoformat(),
                   "payment_source": "Выписка банка"}
        self.state["commercial"] = dict(receipt)
        deal.validate(self.state)
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        for status in ("pending", "confirmed"):
            for value in (False, True, None):
                with self.subTest(status=status, duplicate=value):
                    self.state["checks"]["payment"]["status"] = status
                    self.state["commercial"] = dict(receipt, payment_received=value)
                    before = copy.deepcopy(self.state)
                    with self.assertRaisesRegex(ValueError, "commercial.payment_received.*удалите этот флаг.*checks.payment"):
                        deal.validate(self.state)
                    self.assertEqual(self.state, before)

    def test_actions_complete_separately_and_completed_action_cannot_cover_reopened_check(self):
        self.confirm_readiness()
        proof = copy.deepcopy(self.state["checks"]["contract"])
        self.state["checks"]["contract"] = {"status": "pending"}
        self.state["schedule"]["reporting_deadlines"] = [planned_actions()[1], planned_actions()[-1]]
        issued = deal.plan(self.state, self.now)
        action_id = "report-signing"
        uid = next(event["uid"] for event in issued["events"] if event["id"] == action_id)
        self.state["checks"]["contract"] = proof
        self.assertIn(action_id, {event["id"] for event in deal.plan(self.state, self.now)["events"]})
        self.state["completed_events"][action_id] = dict(proof)
        self.state["schedule"]["revision"] += 1
        completed = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(issued, completed)["cancel"], [uid])
        self.assertNotIn(uid, deal.ics(completed, self.now))
        self.state["checks"]["contract"] = {"status": "pending", "source": "Новая редакция требует подписи"}
        with self.assertRaisesRegex(ValueError, "План неполон:.*contract"):
            deal.plan(self.state, self.now)
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.state["completed_events"][action_id] = {"status": "pending"}
        self.state["schedule"]["revision"] += 1
        reopened = deal.plan(self.state, self.now)
        self.assertEqual(deal.changes(completed, reopened)["create"], [uid])
        self.assertEqual(next(event["sequence"] for event in reopened["events"] if event["id"] == action_id), 3)

    def test_removed_paid_action_keeps_completion_and_stable_history(self):
        issued = deal.plan(self.state, self.now)
        self.state["checks"]["payment"] = {"status": "confirmed", "source": "Выписка: деньги получены",
                                             "checked_at": "2026-11-01T11:00:00+03:00"}
        self.state["schedule"]["reporting_deadlines"] = [item for item in planned_actions() if item["id"] != "payment-check"]
        self.state["schedule"]["revision"] += 1
        current = deal.plan(self.state, self.now, previous=issued)
        event = next(event for event in current["completed"] if event["id"] == "report-payment-check")
        self.assertEqual(event["completed_at"], self.state["checks"]["payment"]["checked_at"])
        self.assertIn("Выписка", event["completion_source"])
        self.assertIn(event["uid"], deal.changes(issued, current)["cancel"])
        self.assertEqual(event["sequence"], 2)
        self.assertNotIn(event["uid"], deal.ics(current, self.now))
        again = deal.plan(self.state, deal.hours(self.now, 1), previous=current)
        self.assertEqual(deal.changes(current, again), {"create": [], "update": [], "cancel": []})
        self.assertEqual(next(e for e in again["completed"] if e["id"] == event["id"]), event)
        self.assertEqual(self.state["completed_events"], {})

    def test_removed_joint_action_requires_all_current_proofs(self):
        self.confirm_readiness()
        joint = {"id": "joint", "title": "Подписи и оплата", "at": "2026-11-01T10:00:00+03:00",
                 "source": "Организационная проверка", "kind": "organizational", "checks": ["contract", "payment"]}
        self.state["schedule"]["reporting_deadlines"].append(joint)
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"].remove(joint)
        for key, proof in (("contract", {"status": "pending"}),
                           ("contract", {"status": "confirmed", "source": True, "checked_at": self.now.isoformat()}),
                           ("payment", {"status": "confirmed", "source": " \t\n", "checked_at": self.now.isoformat()}),
                           ("payment", {"status": "confirmed", "source": "Будущий факт", "checked_at": "2026-11-02T12:00:00+03:00"})):
            with self.subTest(key=key):
                original = self.state["checks"][key]
                self.state["checks"][key] = proof
                self.review_current_actions()
                current = deal.plan(self.state, self.now, previous=issued)
                self.assertNotIn("report-joint", {event["id"] for event in current["completed"]})
                self.state["checks"][key] = original
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertIn("report-joint", {event["id"] for event in current["completed"]})

    def test_removed_ord_action_does_not_complete_for_another_creative(self):
        self.confirm_readiness()
        issued = deal.plan(self.state, self.now)
        action = self.state["schedule"]["reporting_deadlines"][3]
        action.update(id="new-ord-check", checks=["ord_creative"])
        self.state["schedule"]["reporting_deadlines"].append(dict(action, id="token-check", checks=["erid"]))
        self.state["creative"]["text"] += " Изменённая версия."
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertNotIn("report-ord-check", {event["id"] for event in current["completed"]})
        self.assertIn("report-new-ord-check", {event["id"] for event in current["events"]})

    def test_removed_explicit_completion_requires_current_full_creative(self):
        self.confirm_readiness()
        issued = deal.plan(self.state, self.now)
        action_id = "report-ord-check"
        proof = {"status": "confirmed", "source": "Сверка зарегистрированной версии",
                 "checked_at": "2026-11-01T11:00:00+03:00", "creative_sha256": deal.digest(self.state["creative"])}
        self.state["completed_events"][action_id] = proof
        action = self.state["schedule"]["reporting_deadlines"][3]
        action.update(id="new-ord-check", checks=["ord_creative"])
        self.state["schedule"]["reporting_deadlines"].append(dict(action, id="token-check", checks=["erid"]))
        self.state["creative"]["text"] += " Новая версия."
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertNotIn(action_id, {event["id"] for event in current["completed"]})
        # Даже после полной сверки нового креатива явная запись старой версии не годится.
        current_digest = deal.digest(self.state["creative"])
        for key in ("creative_approval", "marking", "erid", "ord_creative"):
            self.state["checks"][key]["creative_sha256"] = current_digest
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertNotIn(action_id, {event["id"] for event in current["completed"]})
        proof["creative_sha256"] = current_digest
        full = copy.deepcopy(self.state["checks"]["ord_creative"])
        for change in ({"verified": {"text": True, "links": False, "media": True}},
                       {"checked_at": "2026-11-02T12:00:00+03:00"}):
            with self.subTest(change=change):
                self.state["checks"]["ord_creative"] = dict(full, **change)
                with self.assertRaisesRegex(ValueError, "незавершённых checks \\(ord_creative\\)"):
                    deal.plan(self.state, self.now, previous=issued)
                current = deal._plan(self.state, self.now)
                deal.retain_completed_actions(self.state, self.now, issued, current)
                self.assertNotIn(action_id, {event["id"] for event in current["completed"]})
        self.state["checks"]["ord_creative"] = full
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertIn(action_id, {event["id"] for event in current["completed"]})

    def test_explicit_reopening_or_revocation_is_not_overridden_by_linked_checks(self):
        self.confirm_readiness()
        issued = deal.plan(self.state, self.now)
        action = self.state["schedule"]["reporting_deadlines"].pop(4)
        action_id = "report-" + action["id"]
        self.state["schedule"]["revision"] += 1
        completed = deal.plan(self.state, self.now, previous=issued)
        deal.changes(issued, completed)
        old = next(event for event in completed["completed"] if event["id"] == action_id)
        for proof in ({"status": "pending"}, {"status": "revoked"},
                      {"status": "confirmed", "source": "Будущее выполнение", "checked_at": "2026-11-02T10:00:00+03:00"}):
            with self.subTest(proof=proof):
                self.state["completed_events"][action_id] = proof
                for previous in (issued, completed):
                    current = deal.plan(self.state, self.now, previous=previous)
                    self.assertNotIn(action_id, {event["id"] for event in current["completed"]})
        self.state["completed_events"][action_id] = {"status": "pending"}
        self.state["schedule"]["reporting_deadlines"].append(action)
        self.state["schedule"]["revision"] += 1
        reopened = deal.plan(self.state, self.now, previous=completed)
        self.assertEqual(deal.changes(completed, reopened)["create"], [old["uid"]])
        event = next(event for event in reopened["events"] if event["id"] == action_id)
        self.assertEqual((event["uid"], event["sequence"]), (old["uid"], old["sequence"] + 1))

    def test_removed_erir_action_requires_a_valid_matching_token(self):
        self.confirm_readiness()
        action = {"id": "erir-check", "title": "Проверить видимость идентификатора",
                  "at": "2026-11-01T10:00:00+03:00", "source": "Организационная проверка",
                  "kind": "organizational", "checks": ["erir"]}
        self.state["schedule"]["reporting_deadlines"].append(action)
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"].remove(action)
        proof = {"status": "confirmed", "source": "Результат проверки ЕРИР", "checked_at": "2026-11-01T11:00:00+03:00"}
        self.state["checks"]["erir"] = dict(proof)
        for token in (None, "", "not a token", "ERID", "test-not-a-real-erid"):
            for explicit in (False, True):
                with self.subTest(token=token, explicit=explicit):
                    self.state["checks"]["erir"]["token"] = token
                    self.state["checks"]["erid"]["token"] = token
                    self.state["completed_events"] = {"report-erir-check": dict(proof)} if explicit else {}
                    self.review_current_actions()
                    current = deal.plan(self.state, self.now, previous=issued)
                    self.assertEqual("report-erir-check" in {event["id"] for event in current["completed"]},
                                     token == "test-not-a-real-erid")
        self.state["checks"]["erir"]["token"] = "another-token"
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertNotIn("report-erir-check", {event["id"] for event in current["completed"]})

    def test_rechecking_same_fact_preserves_original_completion_time_and_source(self):
        self.confirm_readiness()
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"].pop(4)
        self.state["schedule"]["revision"] += 1
        completed = deal.plan(self.state, self.now, previous=issued)
        deal.changes(issued, completed)
        old = copy.deepcopy(next(event for event in completed["completed"] if event["id"] == "report-payment-check"))
        self.state["checks"]["payment"].update(checked_at="2026-11-01T11:30:00+03:00", source="Повторная сверка выписки")
        again = deal.plan(self.state, self.now, previous=completed)
        self.assertEqual(deal.changes(completed, again), {"create": [], "update": [], "cancel": []})
        event = next(event for event in again["completed"] if event["id"] == old["id"])
        self.assertEqual(event, old)
        # Явное исправление самой записи выполнения по-прежнему применяется.
        self.state["completed_events"][old["id"]] = {
            "status": "confirmed", "source": "Уточнённое доказательство выполнения", "checked_at": "2026-11-01T10:05:00+03:00"}
        self.state["schedule"]["revision"] += 1
        corrected = deal.plan(self.state, self.now, previous=again)
        deal.changes(again, corrected)
        event = next(event for event in corrected["completed"] if event["id"] == old["id"])
        self.assertEqual(event["completed_at"], "2026-11-01T10:05:00+03:00")
        self.assertEqual(event["sequence"], old["sequence"] + 1)

    def test_validate_plan_rejects_another_deal_or_state_revision(self):
        valid = deal.plan(self.state, self.now)
        for key, value in (("deal_id", "another-deal"), ("revision", valid["revision"] + 1)):
            with self.subTest(key=key):
                changed = copy.deepcopy(valid)
                changed[key] = value
                with self.assertRaisesRegex(ValueError, "другой сделке или редакции"):
                    deal.validate_plan(self.state, changed)

    def test_validate_plan_detects_removed_and_changed_events_and_completed_items(self):
        self.confirm_readiness()
        valid = deal.plan(self.state, self.now)
        for section in ("events", "completed"):
            for change in ("remove", "at", "note", "uid"):
                with self.subTest(section=section, change=change):
                    changed = copy.deepcopy(valid)
                    if change == "remove":
                        changed[section].pop(0)
                    else:
                        changed[section][0][change] = "2026-11-08T15:00:00+03:00" if change == "at" else "Подменённое значение"
                    with self.assertRaisesRegex(ValueError, f"План {section} не воспроизводится"):
                        deal.validate_plan(self.state, changed)
        self.state["schedule"]["duration_hours"] = 72
        with self.assertRaisesRegex(ValueError, "не воспроизводится"):
            deal.validate_plan(self.state, valid)

    def test_validate_plan_uses_saved_generation_time_and_ignores_sequence_and_overdue(self):
        saved = deal.plan(self.state, self.now)
        for section in ("events", "completed"):
            saved[section].reverse()
            for event in saved[section]:
                event["sequence"] = 42
                event["overdue"] = not event["overdue"]
        before_state, before_plan = copy.deepcopy(self.state), copy.deepcopy(saved)
        self.assertIs(deal.validate_plan(self.state, saved), saved)
        self.assertEqual(self.state, before_state)
        self.assertEqual(saved, before_plan)
        # Старые планы без sequence и пустого completed остаются допустимыми.
        saved.pop("completed")
        for event in saved["events"]:
            event.pop("sequence")
        self.assertIs(deal.validate_plan(self.state, saved), saved)
        self.confirm_readiness()
        confirmed = deal.plan(self.state, self.now)
        deal.validate_plan(self.state, confirmed)
        confirmed["generated_at"] = "2026-11-01T09:00:00+03:00"
        with self.assertRaisesRegex(ValueError, "не воспроизводится"):
            deal.validate_plan(self.state, confirmed)

    def test_validate_plan_preserves_removed_completed_action_from_issued_plan(self):
        self.confirm_readiness()
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"].pop(4)
        self.state["schedule"]["revision"] += 1
        current = deal.plan(self.state, self.now, previous=issued)
        deal.changes(issued, current)
        retained = next(event for event in current["completed"] if event["id"] == "report-payment-check")
        before = copy.deepcopy(current)
        self.assertIs(deal.validate_plan(self.state, current), current)
        self.assertEqual(current, before)
        self.assertNotIn(retained["uid"], deal.ics(current, self.now))

    def test_validate_plan_rejects_duplicate_events_and_missing_generation_time(self):
        valid = deal.plan(self.state, self.now)
        for section in ("events", "completed"):
            with self.subTest(section=section):
                changed = copy.deepcopy(valid)
                changed[section].append(copy.deepcopy(changed["events"][0]))
                with self.assertRaisesRegex(ValueError, "повтор события"):
                    deal.validate_plan(self.state, changed)
        del valid["generated_at"]
        with self.assertRaisesRegex(ValueError, "generated_at"):
            deal.validate_plan(self.state, valid)

    def test_cancelled_unconfirmed_action_is_not_completed(self):
        optional = {"id": "optional", "title": "Обсудить дополнительный формат", "at": "2026-11-02T10:00:00+03:00",
                    "source": "Предложение, которое можно отменить"}
        self.state["schedule"]["reporting_deadlines"].append(optional)
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"].remove(optional)
        self.state["schedule"]["revision"] += 1
        current = deal.plan(self.state, self.now, previous=issued)
        self.assertNotIn("report-optional", {event["id"] for event in current["completed"]})
        self.assertEqual(deal.changes(issued, current)["cancel"], ["new-deal-report-optional@blogger-ads-skill"])
        issued["deal_id"] = "other-deal"
        with self.assertRaisesRegex(ValueError, "другой сделке"):
            deal.plan(self.state, self.now, previous=issued)

    def test_exported_plan_contains_action_instructions_in_json_markdown_and_ics(self):
        self.relative_payment()
        self.state["checks"]["edo_exchange"] = {
            "status": "pending", "source": "Сбой ЭДО", "recovery_expected_on": "2026-10-18",
            "recovery_source": "Сообщение оператора", "recovery_check_time": "10:00"}
        self.state["schedule"]["reporting_deadlines"].append({
            "id": "edo-exchange", "title": "Проверить обмен оригиналами", "at": "2026-10-18T11:00:00+03:00",
            "source": "Отдельный контроль обмена", "kind": "organizational", "checks": ["edo_exchange"]})
        action = self.state["schedule"]["reporting_deadlines"][3]
        action["source"] = "Получить раскрытую карточку ОРД; сравнить текст и список URL с утверждённым постом"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            state_file = folder / "state.json"
            state_file.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            output = folder / "plan"
            command = [sys.executable, str(ROOT / "scripts/deal.py"), "plan", "--state", str(state_file),
                       "--output-dir", str(output), "--now", self.now.isoformat()]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["installed"])
            plan_data = json.loads((output / "plan.json").read_text())
            markdown = (output / "plan.md").read_text()
            calendar = (output / "reminders.ics").read_bytes().decode().replace("\r\n ", "")
            for event_id in ("report-ord-check", "readiness", "report-payment-check", "report-signing", "edo-recovery"):
                event = next(event for event in plan_data["events"] if event["id"] == event_id)
                self.assertIn(event["note"], markdown)
                self.assertIn("DESCRIPTION:" + deal.escape_ics(event["note"]), calendar)
            event = next(event for event in plan_data["events"] if event["id"] == "report-ord-check")
            self.assertIn(action["source"], event["note"])
            self.assertIn("выбранный час не является договорным сроком", event["note"])
            readiness = next(event for event in plan_data["events"] if event["id"] == "readiness")
            self.assertIn("не означает обязательного переноса", readiness["note"])

    def fee_settings(self, applicability="applicable", quarters=None):
        fee = {"applicability": applicability, "payer": "ИП Учебный" if applicability == "applicable" else None,
               "source": "Учебный прямой договор и подтверждённый доход за квартал; ст.18.2 и ПП №1224",
               "quarters": ["2026-Q4"] if quarters is None else quarters, "reminder_time": "09:00"}
        if self.state["checks"].get("payment", {}).get("status") == "confirmed":
            fee["payment_basis_sha256"] = deal.fee_payment_basis(self.state)["sha256"]
        return fee

    def record_fee_payment(self):
        self.state["checks"]["payment"] = {
            "status": "confirmed", "source": "Выписка и сообщение о зачислении", "amount": "50000.00",
            "checked_at": "2026-11-01T10:00:00+03:00"}
        self.state["completed_events"]["payment"] = {
            "completed_at": "2026-09-30T21:15:00+03:00", "source": "Сообщение о фактическом зачислении",
            "checked_at": "2026-11-01T10:00:00+03:00"}

    def test_fee_source_requires_review_when_payment_is_confirmed(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        previous = deal.plan(self.state, self.now)
        self.record_fee_payment()
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "payment_basis_sha256.*Пересмотрите прежнее основание"):
            deal.plan(self.state, self.now, previous=previous)
        self.assertEqual(self.state, before)
        fee = self.state["schedule"]["fee_obligation"]
        fee["source"] = "Зачисление подтверждено выпиской; квартал предварительный, правовой период и плательщик проверяются"
        fee["payment_basis_sha256"] = deal.fee_payment_basis(self.state)["sha256"]
        self.state["schedule"]["revision"] += 1
        self.review_current_actions()
        current = deal.plan(self.state, self.now, previous=previous)
        events = self.fee_events(current)
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event["obligation_source"] == fee["source"] for event in events.values()))
        self.assertTrue(all(event["payment_basis_sha256"] == fee["payment_basis_sha256"] for event in events.values()))
        self.assertTrue({event["uid"] for event in events.values()} <= set(deal.changes(previous, current)["update"]))

    def test_fee_basis_detects_changes_in_both_payment_sources_and_actual_date(self):
        self.record_fee_payment()
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        baseline = copy.deepcopy(self.state)
        for section, field, value in (("checks", "source", "Исправленная выписка"),
                                      ("checks", "amount", "45000.00"),
                                      ("completed_events", "source", "Уточнённое сообщение"),
                                      ("completed_events", "completed_at", "2026-10-01T00:15:00+03:00")):
            with self.subTest(section=section, field=field):
                self.state = copy.deepcopy(baseline)
                self.state[section]["payment"][field] = value
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "payment_basis_sha256.*прежнему свидетельству"):
                    deal.plan(self.state, self.now)
                self.assertEqual(self.state, before)

    def test_fee_basis_is_invalidated_by_payment_rollback_or_removed_evidence(self):
        self.record_fee_payment()
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        baseline = copy.deepcopy(self.state)
        for change in ("pending", "remove_check", "remove_completion"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(baseline)
                if change == "pending":
                    self.state["checks"]["payment"]["status"] = "pending"
                elif change == "remove_check":
                    del self.state["checks"]["payment"]
                else:
                    del self.state["completed_events"]["payment"]
                with self.assertRaisesRegex(ValueError, "payment_basis_sha256.*отзыва"):
                    deal.plan(self.state, self.now)

    def test_fee_basis_ignores_recheck_clock_and_preserves_event_sequence(self):
        self.record_fee_payment()
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        previous = deal.plan(self.state, self.now)
        basis = deal.fee_payment_basis(self.state)
        self.state["checks"]["payment"]["checked_at"] = "2026-11-01T11:00:00+03:00"
        self.state["completed_events"]["payment"]["checked_at"] = "2026-11-01T11:15:00+03:00"
        self.assertEqual(deal.fee_payment_basis(self.state), basis)
        current = deal.plan(self.state, deal.hours(self.now, 1), previous=previous)
        self.assertEqual(self.fee_events(current), self.fee_events(previous))
        self.assertEqual(deal.changes(previous, current), {"create": [], "update": [], "cancel": []})

    def test_fee_payment_basis_does_not_convert_payment_date_into_income_quarter(self):
        self.record_fee_payment()  # Зачисление в Q3, выбранный период Q4 остаётся предварительным.
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        before = copy.deepcopy(self.state)
        events = self.fee_events(deal.plan(self.state, self.now))
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event["quarter"] == "2026-Q4" and event["applicability"] == "unknown"
                            and event["payer"] is None for event in events.values()))
        self.assertEqual(self.state, before)
        self.state["schedule"]["fee_obligation"]["quarters"] = []
        result = deal.plan(self.state, self.now)
        self.assertEqual(self.fee_events(result), {})
        self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])

    def test_fee_payment_basis_cli_reads_stale_state_without_modifying_it(self):
        self.record_fee_payment()
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            for saved in (None, "0" * 64):
                with self.subTest(saved=saved):
                    self.state["schedule"]["fee_obligation"]["payment_basis_sha256"] = saved
                    path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
                    original = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, "payment_basis_sha256"):
                        deal.validate(self.state)
                    command = [sys.executable, "-B", str(ROOT / "scripts/deal.py"), "fee-payment-basis", "--state", str(path)]
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), deal.fee_payment_basis(self.state))
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(list(path.parent.iterdir()), [path])

    def test_fee_payment_basis_rejects_invalid_hash_but_allows_no_source_without_binding(self):
        self.record_fee_payment()
        fee = self.state["schedule"]["fee_obligation"]
        self.assertIsNone(fee["source"])
        deal.validate(self.state)
        for value in (False, 1, [], {}, "", "abc", "A" * 64):
            with self.subTest(value=value):
                fee["payment_basis_sha256"] = value
                with self.assertRaisesRegex(ValueError, "payment_basis_sha256.*SHA-256"):
                    deal.validate(self.state)

    def monthly_events(self, result):
        return {event["id"]: event for event in result["events"] if event["id"].startswith("ord-")}

    def test_ord_monthly_control_survives_unknown_and_legacy_settings(self):
        for role in ("blogger", "advertiser", "agency"):
            for legacy in (False, True):
                with self.subTest(role=role, legacy=legacy):
                    self.state["user_role"] = role
                    if legacy:
                        self.state["schedule"].pop("ord_reporting", None)
                    else:
                        self.state["schedule"]["ord_reporting"] = {"applicability": "unknown", "reminder_time": "09:00"}
                    before = copy.deepcopy(self.state)
                    result = deal.plan(self.state, self.now)
                    event = self.monthly_events(result)["ord-2026-11"]
                    self.assertIsNone(event["responsible"])
                    self.assertIsNone(event["legal_date"])
                    self.assertEqual(event["control_date"], "2026-12-30")
                    self.assertEqual(event["at"], "2026-12-30T09:00:00+03:00")
                    self.assertTrue(any("Применимость обычного месячного" in warning for warning in result["warnings"]))
                    self.assertEqual(self.state, before)

    def test_ord_thirty_days_is_not_thirtieth_of_next_month(self):
        self.state["schedule"]["ord_reporting"].update(
            applicability="applicable", responsible="Заказчик по договору",
            source="Подтверждена применимость обычного срока п.10 ПП974; договор п.6")
        self.state["schedule"]["publication_at"] = "2026-01-20T09:00:00+03:00"
        event = self.monthly_events(deal.plan(self.state, self.now))["ord-2026-01"]
        self.assertEqual(event["legal_date"], "2026-03-02")
        self.assertEqual(event["legal_source"], deal.ORD_RULE_SOURCE)
        self.assertIn("не установленный законом час", event["note"])
        self.state["schedule"]["publication_at"] = "2028-01-20T09:00:00+03:00"
        event = self.monthly_events(deal.plan(self.state, self.now))["ord-2028-01"]
        self.assertEqual(event["legal_date"], "2028-03-01")

    def test_ord_month_boundary_and_exact_midnight_end(self):
        self.state["schedule"].update(publication_at="2026-10-30T00:00:00+03:00", duration_hours=48)
        self.assertEqual(set(self.monthly_events(deal.plan(self.state, self.now))), {"ord-2026-10"})
        self.state["schedule"]["duration_hours"] = 48.5
        events = self.monthly_events(deal.plan(self.state, self.now))
        self.assertEqual(set(events), {"ord-2026-10", "ord-2026-11"})
        self.assertEqual(events["ord-2026-10"]["control_date"], "2026-11-30")
        self.assertEqual(events["ord-2026-11"]["control_date"], "2026-12-30")

    def test_ord_actual_start_and_explicit_extended_period_determine_months(self):
        self.state["schedule"]["publication_at"] = "2026-12-30T09:00:00+03:00"
        self.state["publication"]["actual_at"] = "2027-01-01T09:00:00+03:00"
        self.assertEqual(set(self.monthly_events(deal.plan(self.state, self.now))), {"ord-2027-01"})
        self.state["schedule"]["ord_reporting"].update(
            distribution_end_at="2027-03-01T00:00:00+03:00",
            distribution_end_source="Пост оставался доступен до указанного времени; свидетельство удаления")
        result = deal.plan(self.state, self.now)
        self.assertEqual(set(self.monthly_events(result)), {"ord-2027-01", "ord-2027-02"})
        self.state["documents"] = [{"type": "upd", "date": "2027-04-01"}]
        self.assertEqual(self.monthly_events(result), self.monthly_events(deal.plan(self.state, self.now)))
        self.assertFalse(any("Фактическое удаление" in warning for warning in result["warnings"]))

    def test_ord_completion_is_per_month_and_uses_stable_uid(self):
        self.state["schedule"].update(publication_at="2026-10-31T09:00:00+03:00", duration_hours=48)
        now = deal.stamp("2026-12-02T10:00:00+03:00")
        old = deal.plan(self.state, now)
        event_id = "ord-2026-10"
        self.state["completed_events"][event_id] = {"status": "confirmed", "source": "Подтверждение ОРД за октябрь",
                                                   "checked_at": "2026-11-28T12:00:00+03:00"}
        self.state["schedule"]["revision"] += 1
        new = deal.plan(self.state, now)
        completed = next(event for event in new["completed"] if event["id"] == event_id)
        self.assertEqual(set(self.monthly_events(new)), {"ord-2026-11"})
        self.assertNotIn(completed["uid"], deal.ics(new, now))
        self.assertEqual(deal.changes(old, new), {"create": [], "update": [], "cancel": [completed["uid"]]})

    def test_ord_reschedule_new_month_cancels_old_and_creates_new_uid(self):
        old = deal.plan(self.state, self.now)
        self.state["schedule"]["publication_at"] = "2026-12-09T09:00:00+03:00"
        self.state["schedule"]["revision"] += 1
        new = deal.plan(self.state, self.now)
        changes = deal.changes(old, new)
        self.assertIn("new-deal-ord-2026-11@blogger-ads-skill", changes["cancel"])
        self.assertIn("new-deal-ord-2026-12@blogger-ads-skill", changes["create"])

    def test_ord_rejects_duplicate_manual_month_but_preserves_contract_reminder(self):
        manual = self.reporting_deadline("ord-2026-11", "2026-12-05T11:00:00+03:00")
        self.state["schedule"]["reporting_deadlines"] = [*planned_actions(), manual]
        with self.assertRaisesRegex(ValueError, "особое договорное действие"):
            deal.plan(self.state, self.now)
        manual["id"] = "contract-statistics-nov"
        result = deal.plan(self.state, self.now)
        self.assertIn("ord-2026-11", self.monthly_events(result))
        event = next(event for event in result["events"] if event["id"] == "report-contract-statistics-nov")
        self.assertEqual(event["at"], manual["at"])

    def test_ord_explicit_non_applicability_and_invalid_inputs(self):
        original = copy.deepcopy(self.state["schedule"]["ord_reporting"])
        self.state["schedule"]["ord_reporting"].update(applicability="not_applicable", source="Документированное основание неприменимости")
        self.assertEqual(self.monthly_events(deal.plan(self.state, self.now)), {})
        cases = [{"applicability": "not_applicable"}, {"applicability": True}, {"responsible": []},
                 {"reminder_time": "25:00"}, {"distribution_end_at": "2026-11-12T09:00:00+03:00"},
                 {"distribution_end_at": "2026-11-12T09:00:00", "distribution_end_source": "Без часового пояса"},
                 {"distribution_end_at": "2026-11-08T09:00:00+03:00", "distribution_end_source": "До начала"}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.state["schedule"]["ord_reporting"] = dict(original, **changes)
                with self.assertRaises(ValueError):
                    deal.plan(self.state, self.now)

    def fee_events(self, result):
        return {event["id"]: event for event in result["events"] if event["id"].startswith("fee-")}

    def test_fee_fourth_quarter_has_all_three_dates_next_year(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings()
        result = deal.plan(self.state, self.now)
        events = self.fee_events(result)
        self.assertEqual({key: event["at"] for key, event in events.items()}, {
            "fee-2026-Q4-assessment": "2027-02-15T09:00:00+03:00",
            "fee-2026-Q4-review": "2027-02-25T09:00:00+03:00",
            "fee-2026-Q4-payment": "2027-03-05T09:00:00+03:00"})
        for event in events.values():
            self.assertEqual(event["quarter"], "2026-Q4")
            self.assertEqual(event["legal_date"], event["at"][:10])
            self.assertEqual(event["legal_source"], deal.FEE_RULE_SOURCE)
            self.assertIn("не установленный законом час", event["note"])
            self.assertIn("2026-Q4", event["note"])
            self.assertIn(event["legal_date"], event["note"])
            self.assertIn(event["uid"], deal.ics(result, self.now))

    def test_fee_quarters_are_explicit_and_do_not_follow_publication_or_upd(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings(quarters=["2026-Q4", "2026-Q3"])
        before = self.fee_events(deal.plan(self.state, self.now))
        self.assertEqual(before["fee-2026-Q3-assessment"]["at"], "2026-11-15T09:00:00+03:00")
        self.assertEqual(before["fee-2026-Q3-review"]["at"], "2026-11-25T09:00:00+03:00")
        self.assertEqual(before["fee-2026-Q3-payment"]["at"], "2026-12-05T09:00:00+03:00")
        self.state["schedule"]["fee_obligation"]["quarters"].reverse()
        self.state["schedule"]["publication_at"] = "2027-01-04T09:00:00+03:00"
        self.state["publication"]["actual_at"] = "2027-01-05T10:00:00+03:00"
        self.state["documents"] = [{"type": "upd", "date": "2027-01-10"}]
        self.assertEqual(before, self.fee_events(deal.plan(self.state, self.now)))

    def test_fee_completion_removes_only_one_step_and_preserves_other_uids(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings()
        now = deal.stamp("2027-02-16T10:00:00+03:00")
        old = deal.plan(self.state, now)
        event_id = "fee-2026-Q4-assessment"
        self.state["completed_events"][event_id] = {
            "status": "confirmed", "source": "Расчёт ЕРИР за 2026-Q4 сохранён",
            "checked_at": "2027-02-15T16:00:00+03:00"}
        self.state["schedule"]["revision"] += 1
        current = deal.plan(self.state, now)
        completed = next(event for event in current["completed"] if event["id"] == event_id)
        self.assertFalse(completed["overdue"])
        self.assertNotIn(event_id, self.fee_events(current))
        self.assertEqual(len(self.fee_events(current)), 2)
        self.assertNotIn(completed["uid"], deal.ics(current, now))
        self.assertEqual(deal.changes(old, current), {"create": [], "update": [], "cancel": [completed["uid"]]})

    def test_unknown_fee_with_quarter_keeps_three_preliminary_checks_without_payer(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown")
        self.state["schedule"]["fee_obligation"]["source"] = "Ожидаемая предоплата в Q4; поступление, юридический период и плательщик не подтверждены"
        self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
            deal.plan(self.state, self.now)
        self.assertEqual(self.state, before)
        self.state["schedule"]["reporting_deadlines"] = planned_actions()
        before = copy.deepcopy(self.state)
        result = deal.plan(self.state, self.now)
        events = self.fee_events(result)
        self.assertEqual(len(events), 3)
        self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])
        self.assertTrue(all(event["payer"] is None and event["applicability"] == "unknown" for event in events.values()))
        self.assertTrue(all("не считать этот план обязанностью" in event["note"] for event in events.values()))
        self.assertTrue(any("не установлены" in warning for warning in result["warnings"]))
        self.assertEqual(before, self.state)
        self.state["schedule"]["fee_obligation"] = self.fee_settings()
        self.state["schedule"]["revision"] += 1
        new = deal.plan(self.state, self.now)
        self.assertEqual({key: item["uid"] for key, item in events.items()},
                         {key: item["uid"] for key, item in self.fee_events(new).items()})
        self.assertEqual(len(deal.changes(result, new)["update"]), 3)

    def test_unknown_and_legacy_fee_settings_require_action_instead_of_assigning_period_or_payer(self):
        for role in ("blogger", "advertiser", "agency"):
            for legacy in (False, True):
                with self.subTest(role=role, legacy=legacy):
                    self.state["user_role"] = role
                    if legacy:
                        self.state["schedule"].pop("fee_obligation", None)
                    else:
                        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown", [])
                    self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
                    with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
                        deal.plan(self.state, self.now)
                    self.state["schedule"]["reporting_deadlines"] = planned_actions()
                    before = copy.deepcopy(self.state)
                    result = deal.plan(self.state, self.now)
                    self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])
                    self.assertEqual(self.fee_events(result), {})
                    self.assertTrue(any("три контрольные даты не рассчитаны" in warning for warning in result["warnings"]))
                    self.assertEqual(self.state, before)

    def test_computable_payment_keeps_three_preliminary_fee_dates_without_changing_state(self):
        self.relative_payment()
        for role in ("blogger", "advertiser", "agency"):
            for legacy in (False, True):
                with self.subTest(role=role, legacy=legacy):
                    self.state["user_role"] = role
                    if legacy:
                        self.state["schedule"].pop("fee_obligation", None)
                    else:
                        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown", [])
                    before = copy.deepcopy(self.state)
                    result = deal.plan(self.state, self.now)
                    events = self.fee_events(result)
                    self.assertEqual({key: event["at"] for key, event in events.items()}, {
                        "fee-2026-Q4-assessment": "2027-02-15T09:00:00+03:00",
                        "fee-2026-Q4-review": "2027-02-25T09:00:00+03:00",
                        "fee-2026-Q4-payment": "2027-03-05T09:00:00+03:00"})
                    for event in events.values():
                        self.assertEqual(event["period_basis"], "contractual_payment_deadline")
                        self.assertEqual(event["payment_due_on"], "2026-10-16")
                        self.assertEqual(event["applicability"], "unknown")
                        self.assertIsNone(event["payer"])
                        self.assertIn("Предварительный контроль", event["title"])
                        self.assertIn("не фактическая дата поступления", event["note"])
                        self.assertIn("не считать этот план обязанностью", event["note"])
                        self.assertIn(before["schedule"]["deadline_terms"]["payment"]["source"], event["obligation_source"])
                        self.assertIn(deal.escape_ics(event["note"]), deal.ics(result, self.now).replace("\r\n ", ""))
                    self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])
                    self.assertFalse(any("три контрольные даты не рассчитаны" in warning for warning in result["warnings"]))
                    self.assertTrue(any("договорной дате платежа 2026-10-16" in warning for warning in result["warnings"]))
                    self.assertNotIn("payment", {event["id"] for event in result["events"]})
                    self.assertEqual(before, self.state)

    def exact_payment(self, at):
        self.state["schedule"]["payment_due_at"] = at
        self.state["schedule"]["deadline_terms"]["payment"] = {
            "precision": "datetime", "condition": "Предоплата в согласованный день и час", "source": "Договор, пункт об оплате"}

    def test_preliminary_fee_uses_payment_timezone_not_confirmation_or_actual_publication(self):
        self.exact_payment("2026-09-30T22:00:00+00:00")
        first = deal.plan(self.state, self.now)
        events = self.fee_events(first)
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event["payment_due_on"] == "2026-10-01" and event["quarter"] == "2026-Q4"
                            for event in events.values()))
        self.state["checks"]["payment"] = {
            "status": "confirmed", "source": "Банковская выписка проверена; дата поступления отдельно не установлена",
            "checked_at": "2026-07-01T10:00:00+03:00"}
        self.state["publication"]["actual_at"] = "2027-01-01T10:00:00+03:00"
        self.state["stage"] = "closing"
        self.review_current_actions()
        current = deal.plan(self.state, deal.hours(self.now, 24), previous=first)
        self.assertEqual(events, self.fee_events(current))

    def test_preliminary_fee_does_not_replace_explicit_quarters_or_non_applicability(self):
        self.exact_payment("2026-10-16T18:00:00+03:00")
        for applicability in ("unknown", "applicable"):
            with self.subTest(applicability=applicability):
                self.state["schedule"]["fee_obligation"] = self.fee_settings(applicability, ["2026-Q3"])
                events = self.fee_events(deal.plan(self.state, self.now))
                self.assertEqual(len(events), 3)
                self.assertTrue(all(event["quarter"] == "2026-Q3" and "payment_due_on" not in event for event in events.values()))
        self.state["schedule"]["fee_obligation"] = self.fee_settings("not_applicable", [])
        self.assertEqual(self.fee_events(deal.plan(self.state, self.now)), {})

    def test_uncomputed_payment_and_confirmation_date_do_not_invent_fee_quarter(self):
        self.relative_payment()
        self.state["schedule"]["deadline_terms"]["payment"]["calculation"] = {
            "kind": "uncomputed", "reason": "Неизвестна дата получения счёта", "review_on": "2026-10-16"}
        self.state["checks"]["payment"] = {
            "status": "confirmed", "source": "Проверено поступление без записи фактической даты", "checked_at": self.now.isoformat()}
        for term in (self.state["schedule"]["deadline_terms"]["payment"],
                     {"precision": "unspecified", "condition": None, "source": None}):
            with self.subTest(term=term):
                self.state["schedule"]["deadline_terms"]["payment"] = term
                result = deal.plan(self.state, self.now)
                self.assertEqual(self.fee_events(result), {})
                self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])

    def test_preliminary_fee_keeps_past_dates_and_moves_only_after_payment_deadline_changes(self):
        self.relative_payment(requires=[])
        self.state["schedule"]["publication_at"] = "2026-10-01T09:25:00+03:00"
        self.now = deal.stamp("2027-01-01T12:00:00+03:00")
        previous = deal.plan(self.state, self.now)
        old_events = self.fee_events(previous)
        self.assertEqual(len(old_events), 3)
        self.assertTrue(all(event["quarter"] == "2026-Q3" and event["payment_due_on"] == "2026-09-30" and event["overdue"]
                            for event in old_events.values()))
        later = deal.plan(self.state, deal.hours(self.now, 24), previous=previous)
        self.assertEqual(old_events, self.fee_events(later))
        self.assertEqual(deal.changes(previous, later), {"create": [], "update": [], "cancel": []})
        self.state["schedule"]["publication_at"] = "2026-10-02T09:25:00+03:00"
        self.state["schedule"]["revision"] += 1
        current = deal.plan(self.state, self.now, previous=previous)
        new_events = self.fee_events(current)
        self.assertTrue(all(event["quarter"] == "2026-Q4" and event["payment_due_on"] == "2026-10-01" for event in new_events.values()))
        changes = deal.changes(previous, current)
        self.assertTrue({event["uid"] for event in old_events.values()} <= set(changes["cancel"]))
        self.assertTrue({event["uid"] for event in new_events.values()} <= set(changes["create"]))
        self.assertFalse(any(event["id"] in old_events for event in current["completed"]))

    def test_preliminary_fee_respects_work_calendar_and_supported_periods(self):
        self.relative_payment(requires=[])
        self.state["schedule"].update(publication_at="2026-10-02T09:25:00+03:00", holidays=["2026-10-01"])
        events = self.fee_events(deal.plan(self.state, self.now))
        self.assertTrue(all(event["payment_due_on"] == "2026-09-30" and event["quarter"] == "2026-Q3" for event in events.values()))
        for at in ("2025-03-31T23:00:00+03:00", "9999-01-01T00:00:00+03:00"):
            with self.subTest(at=at):
                self.exact_payment(at)
                result = deal.plan(self.state, self.now)
                self.assertEqual(self.fee_events(result), {})
                self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])

    def test_preliminary_fee_dates_do_not_replace_action_for_actual_income_period(self):
        self.relative_payment()
        self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
        with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
            deal.plan(self.state, self.now)

    def test_fee_period_action_is_independent_of_publication_readiness_and_keeps_its_time(self):
        self.confirm_readiness()
        for quarters in ([], ["2026-Q4"]):
            with self.subTest(quarters=quarters):
                self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown", quarters)
                self.state["publication"]["actual_at"] = None
                self.state["stage"] = "inquiry"
                self.state["schedule"]["reporting_deadlines"] = []
                self.assertTrue(deal.check(self.state, self.now)["ready"])
                with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
                    deal.plan(self.state, self.now)
                action = dict(planned_actions()[-1], at="2026-11-12T11:00:00+03:00")
                self.state["schedule"]["reporting_deadlines"] = [action]
                first = deal.plan(self.state, self.now)
                later = deal.plan(self.state, deal.hours(self.now, 24), previous=first)
                self.assertEqual(first["events"], later["events"])
                self.assertEqual(later["action_coverage"], {"fee_period": ["report-fee-period"]})
                self.state["publication"]["actual_at"] = "2026-11-09T09:00:00+03:00"
                self.state["stage"] = "closing"
                self.assertEqual(deal.plan(self.state, self.now)["action_coverage"], later["action_coverage"])

    def test_confirmed_fee_period_cannot_hide_unknown_applicability(self):
        proof = {"status": "confirmed", "source": "Участник ответил, но применимость и юридический период не установлены",
                 "checked_at": self.now.isoformat()}
        for quarters in ([], ["2026-Q4"]):
            with self.subTest(quarters=quarters):
                self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown", quarters)
                self.state["schedule"]["reporting_deadlines"] = planned_actions()
                self.state["checks"]["fee_period"] = proof
                self.state["completed_events"]["report-fee-period"] = proof
                with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
                    deal.plan(self.state, self.now)
                self.state["completed_events"]["report-fee-period"] = {"status": "pending"}
                result = deal.plan(self.state, self.now)
                self.assertEqual(result["action_coverage"]["fee_period"], ["report-fee-period"])
                self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
                with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
                    deal.plan(self.state, self.now, previous=result)

    def test_preliminary_quarter_does_not_complete_removed_fee_action_from_confirmed_check(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings("unknown", ["2026-Q4"])
        self.state["checks"]["fee_period"] = {
            "status": "confirmed", "source": "Плановый квартал выбран; применимость ещё проверяется",
            "checked_at": self.now.isoformat()}
        issued = deal.plan(self.state, self.now)
        self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
        current = deal._plan(self.state, self.now)
        deal.retain_completed_actions(self.state, self.now, issued, current)
        self.assertIn("fee_period", deal.unfinished_checks(self.state, self.now))
        self.assertNotIn("report-fee-period", {event["id"] for event in current["completed"]})
        with self.assertRaisesRegex(ValueError, "План неполон:.*fee_period"):
            deal.plan(self.state, self.now, previous=issued)

    def test_established_fee_applicability_does_not_require_open_period_action(self):
        for applicability, quarters in (("applicable", ["2026-Q4"]), ("not_applicable", [])):
            with self.subTest(applicability=applicability):
                self.state["schedule"]["fee_obligation"] = self.fee_settings(applicability, quarters)
                self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
                before = copy.deepcopy(self.state)
                result = deal.plan(self.state, self.now)
                self.assertNotIn("fee_period", result["action_coverage"])
                self.assertEqual(len(self.fee_events(result)), 3 if applicability == "applicable" else 0)
                self.assertEqual(self.state, before)

    def test_fee_not_applicable_requires_basis_and_creates_no_payment_task(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings("not_applicable", [])
        self.state["schedule"]["reporting_deadlines"] = planned_actions()[:-1]
        result = deal.plan(self.state, self.now)
        self.assertEqual(self.fee_events(result), {})
        self.assertNotIn("fee_period", result["action_coverage"])
        self.assertFalse(any("не установлены" in warning for warning in result["warnings"]))
        self.state["schedule"]["fee_obligation"]["source"] = None
        with self.assertRaisesRegex(ValueError, "основание применимости или неприменимости"):
            deal.plan(self.state, self.now)

    def test_fee_rejects_bad_periods_duplicate_periods_and_missing_basis(self):
        cases = [("quarters", ["2026-Q5"]), ("quarters", ["2026-10"]), ("quarters", ["2025-Q1"]),
                 ("quarters", [True]), ("quarters", ["2026-Q4", "2026-Q4"]), ("quarters", []),
                 ("quarters", "2026-Q4"), ("reminder_time", "25:00"), ("reminder_time", "09:60"),
                 ("reminder_time", "09:00:00"), ("source", None), ("source", {}), ("payer", None),
                 ("payer", 1), ("applicability", True)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.state["schedule"]["fee_obligation"] = dict(self.fee_settings(), **{key: value})
                with self.assertRaisesRegex(ValueError, "fee_obligation"):
                    deal.plan(self.state, self.now)

    def test_fee_time_change_updates_events_without_changing_legal_dates(self):
        self.state["schedule"]["fee_obligation"] = self.fee_settings()
        old = deal.plan(self.state, self.now)
        self.state["schedule"]["fee_obligation"]["reminder_time"] = "11:30"
        self.state["schedule"]["revision"] += 1
        new = deal.plan(self.state, self.now)
        self.assertEqual(len(deal.changes(old, new)["update"]), 3)
        for event_id, old_event in self.fee_events(old).items():
            event = self.fee_events(new)[event_id]
            self.assertEqual(old_event["legal_date"], event["legal_date"])
            self.assertEqual(old_event["uid"], event["uid"])
            self.assertEqual(event["at"][11:16], "11:30")

    def test_actual_time_drives_evidence_and_closing(self):
        self.state["publication"]["actual_at"] = "2026-11-09T10:30:00+03:00"
        events = {e["id"]: e for e in deal.plan(self.state, self.now)["events"]}
        self.assertEqual(events["placement_end"]["at"], "2026-11-11T10:30:00+03:00")
        self.assertEqual(events["readiness"]["at"], "2026-11-08T09:00:00+03:00")

    def test_empty_state_blocks_and_creative_change_invalidates(self):
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.confirm_readiness()
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        queue_proof = self.state["checks"]["scheduled_message"]
        self.state["checks"]["scheduled_message"] = {"status": "pending"}
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.state["checks"]["scheduled_message"] = queue_proof
        self.assertTrue(deal.check(self.state, self.now)["ready"])
        del self.state["checks"]["scheduled_message"]
        self.state["publication"]["scheduled_message_id"] = "test-message-id"
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.state["publication"]["scheduled_message_id"] = None
        self.state["checks"]["scheduled_message"] = queue_proof
        self.state["creative"]["links"] = ["https://example.org/new"]
        self.assertFalse(deal.check(self.state, self.now)["ready"])

    def test_missing_queue_check_blocks_even_without_scheduled_message_id(self):
        self.confirm_readiness()
        self.assertIsNone(self.state["publication"]["scheduled_message_id"])
        queue_proof = self.state["checks"].pop("scheduled_message")
        result = deal.check(self.state, self.now)
        self.assertFalse(result["ready"])
        self.assertEqual(result["missing"], ["Актуальная проверка очереди публикаций и отсутствия дублей"])
        self.state["checks"]["scheduled_message"] = queue_proof
        self.assertTrue(deal.check(self.state, self.now)["ready"])

    def test_queue_confirmation_is_bound_to_publication_time_and_message_id(self):
        for original_id, section, field, value in (
                (None, "schedule", "publication_at", "2026-11-10T09:00:00+03:00"),
                ("message-1", "schedule", "publication_at", "2026-11-09T10:00:00+03:00"),
                (None, "publication", "scheduled_message_id", "message-1"),
                ("message-1", "publication", "scheduled_message_id", "message-2"),
                ("message-1", "publication", "scheduled_message_id", None),
                (1, "publication", "scheduled_message_id", "1"),
                (1, "publication", "scheduled_message_id", True)):
            with self.subTest(original_id=original_id, field=field, value=value):
                self.state["publication"]["scheduled_message_id"] = original_id
                self.state["schedule"]["publication_at"] = "2026-11-09T09:00:00+03:00"
                self.confirm_readiness()
                self.assertTrue(deal.check(self.state, self.now)["ready"])
                self.state[section][field] = value
                before = copy.deepcopy(self.state)
                result = deal.check(self.state, self.now)
                self.assertFalse(result["ready"])
                self.assertEqual(set(result["blockers"]), {"scheduled_message"})
                self.assertIn("scheduled_message", deal.unfinished_checks(self.state, self.now))
                self.assertEqual(deal.plan(self.state, self.now)["action_coverage"]["scheduled_message"], ["report-queue"])
                self.assertEqual(self.state, before)
                self.state["checks"]["scheduled_message"].update(
                    publication_at=self.state["schedule"]["publication_at"],
                    scheduled_message_id=self.state["publication"]["scheduled_message_id"],
                    source="Новый просмотр очереди после изменения размещения", checked_at=self.now.isoformat())
                self.assertTrue(deal.check(self.state, self.now)["ready"])

    def test_queue_confirmation_accepts_unchanged_instant_with_another_offset(self):
        self.confirm_readiness()
        self.state["schedule"].update(publication_at="2026-11-09T06:00:00Z", timezone="UTC")
        before = copy.deepcopy(self.state)
        for now in (self.now, deal.hours(self.now, 1)):
            self.assertTrue(deal.check(self.state, now)["ready"])
        self.assertEqual(self.state, before)

    def test_queue_confirmation_without_complete_snapshot_requires_recheck(self):
        self.confirm_readiness()
        original = self.state["checks"]["scheduled_message"]
        for changes, removed in (({}, ("publication_at",)), ({}, ("scheduled_message_id",)),
                                 ({}, ("publication_at", "scheduled_message_id")),
                                 ({"publication_at": None}, ()), ({"publication_at": True}, ()),
                                 ({"publication_at": "2026-11-09T09:00:00"}, ())):
            with self.subTest(changes=changes, removed=removed):
                proof = dict(original, **changes)
                for field in removed:
                    proof.pop(field)
                self.state["checks"]["scheduled_message"] = proof
                result = deal.check(self.state, self.now)
                self.assertFalse(result["ready"])
                self.assertEqual(set(result["blockers"]), {"scheduled_message"})
                self.assertIn("publication_at", result["blockers"]["scheduled_message"][0])
                self.assertIn("scheduled_message_id", result["blockers"]["scheduled_message"][0])

    def test_old_queue_action_completion_does_not_cover_changed_placement(self):
        self.confirm_readiness()
        issued = deal.plan(self.state, self.now)
        action = next(item for item in self.state["schedule"]["reporting_deadlines"] if item["id"] == "queue")
        self.state["completed_events"]["report-queue"] = dict(self.state["checks"]["scheduled_message"])
        self.state["schedule"]["publication_at"] = "2026-11-10T09:00:00+03:00"
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.assertIn("scheduled_message", deal.unfinished_checks(self.state, self.now))
        for removed in (False, True):
            with self.subTest(removed=removed):
                if removed:
                    self.state["schedule"]["reporting_deadlines"].remove(action)
                with self.assertRaisesRegex(ValueError, "План неполон:.*scheduled_message"):
                    deal.plan(self.state, self.now, previous=issued)
                if removed:
                    current = deal._plan(self.state, self.now)
                    deal.retain_completed_actions(self.state, self.now, issued, current)
                    self.assertNotIn("report-queue", {event["id"] for event in current["completed"]})

    def test_readiness_requires_nonblank_text_source_for_each_confirmation(self):
        self.confirm_readiness()
        self.state["policy"]["require_erir_visibility"] = True
        self.state["checks"]["erir"] = dict(self.state["checks"]["erid"])
        self.state["checks"]["payment_terms"] = dict(self.state["checks"]["payment"])
        for key in self.state["checks"]:
            proof = self.state["checks"][key]
            if proof.get("status") != "confirmed":
                continue
            self.state["policy"]["require_prepayment"] = key != "payment_terms"
            original = proof["source"]
            for source in (True, ["Свидетельство"], {"file": "proof.txt"}, 1, " \t\n", "  Свидетельство  "):
                with self.subTest(check=key, source=source):
                    proof["source"] = source
                    valid = source == "  Свидетельство  "
                    result = deal.check(self.state, self.now)
                    self.assertEqual(result["ready"], valid)
                    self.assertEqual(key in result["blockers"], not valid)
                    self.assertEqual(key in deal.unfinished_checks(self.state, self.now), not valid)
                    self.assertEqual(proof["source"], source)
            proof["source"] = original

    def test_future_confirmation_keeps_specific_readiness_reason(self):
        self.confirm_readiness()
        self.state["checks"]["contract"]["checked_at"] = "2026-11-02T12:00:00+03:00"
        self.assertEqual(deal.check(self.state, self.now)["blockers"]["contract"],
                         ["Подписание договора и приложений: время подтверждения находится в будущем"])

    def test_completion_requires_text_source_for_automatic_and_explicit_proofs(self):
        self.confirm_readiness()
        self.state["completed_events"]["link"] = dict(self.state["checks"]["contract"])
        for section, key, event_id in (("checks", "creative_approval", "creative"),
                                       ("checks", "ord_creative", "erid"),
                                       ("completed_events", "link", "link")):
            proof = self.state[section][key]
            original = proof["source"]
            for source in (True, ["Свидетельство"], {"file": "proof.txt"}, 1, " \t\n", "  Свидетельство  "):
                with self.subTest(event=event_id, source=source):
                    proof["source"] = source
                    result = deal.completion(self.state, event_id, self.now)
                    self.assertEqual(result is not None, source == "  Свидетельство  ")
                    if event_id != "erid":
                        current = deal.plan(self.state, self.now)
                        self.assertEqual(event_id in {event["id"] for event in current["completed"]}, result is not None)
                        uid = f"{self.state['deal_id']}-{event_id}@blogger-ads-skill"
                        self.assertEqual(uid in deal.ics(current, self.now), result is None)
            proof["source"] = original

    def test_exchange_with_invalid_source_still_requires_an_action(self):
        proof = {"status": "confirmed", "checked_at": self.now.isoformat(), "recovery_expected_on": None}
        self.state["checks"]["edo_exchange"] = proof
        for source in (True, ["Свидетельство"], {"file": "proof.txt"}, 1, " \t\n"):
            with self.subTest(source=source):
                proof["source"] = source
                with self.assertRaisesRegex(ValueError, "План неполон:.*edo_exchange"):
                    deal.plan(self.state, self.now)
        proof["source"] = "  Квитанции отправки и получения  "
        self.assertNotIn("edo_exchange", deal.plan(self.state, self.now)["action_coverage"])

    def test_check_rejects_invalid_legacy_creative_links(self):
        self.assertIsNone(self.state["creative"].get("provenance"))
        for links in ("not-a-list", {"url": "https://example.org/"}, None,
                      [123], ["not-a-url"], ["https://example.org/a b"]):
            with self.subTest(links=links):
                self.state["creative"]["links"] = links
                self.confirm_readiness()
                with self.assertRaisesRegex(ValueError, "creative.links"):
                    deal.check(self.state, self.now)

    def test_strict_erir_policy_and_postpayment(self):
        self.state["policy"].update(require_erir_visibility=True, require_prepayment=False)
        missing = deal.check(self.state, self.now)["missing"]
        self.assertIn("Согласованная проверка видимости в ЕРИР", missing)
        self.assertIn("Согласованные условия оплаты", missing)
        self.assertNotIn("Получение согласованной предоплаты", missing)

    def test_invalid_zone_and_durations(self):
        self.state["schedule"]["timezone"] = "Europe/London"
        with self.assertRaises(ValueError):
            deal.plan(self.state, self.now)
        self.state["schedule"]["timezone"] = "Europe/Moscow"
        self.state["schedule"]["top_hours"] = 50
        with self.assertRaises(ValueError):
            deal.plan(self.state, self.now)

    def test_missing_publication_is_an_actionable_error(self):
        self.state["schedule"]["publication_at"] = None
        with self.assertRaisesRegex(ValueError, "Дата публикации не согласована"):
            deal.plan(self.state, self.now)

    def test_ics_utf8_fold_and_alarm(self):
        content = deal.ics(deal.plan(self.state, self.now), self.now)
        self.assertIn("BEGIN:VALARM", content)
        self.assertIn("DTSTART:20261109T060000Z", content)
        for line in content.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_dst_elapsed_hours(self):
        state = copy.deepcopy(self.state)
        state["schedule"].update(publication_at="2026-10-24T09:00:00+02:00", timezone="Europe/Berlin")
        events = {e["id"]: e for e in deal.plan(state, self.now)["events"]}
        self.assertEqual(events["placement_end"]["at"], "2026-10-26T08:00:00+01:00")

    def test_ord_and_fee_reminders_require_unique_local_time(self):
        for field, zone, publication, quarter, event_id, invalid, reason, valid, expected, utc in (
                ("ord_reporting", "Europe/Berlin", "2025-02-10T09:00:00+01:00", None,
                 "ord-2025-02", "02:30", "не существует", "03:30", "2025-03-30T03:30:00+02:00", "20250330T013000Z"),
                ("ord_reporting", "Europe/Berlin", "2033-09-10T09:00:00+02:00", None,
                 "ord-2033-09", "02:30", "встречается дважды", "03:30", "2033-10-30T03:30:00+01:00", "20331030T023000Z"),
                ("fee_obligation", "America/Santiago", "2026-11-09T09:00:00-03:00", "2027-Q2",
                 "fee-2027-Q2-payment", "00:30", "не существует", "01:30", "2027-09-05T01:30:00-03:00", "20270905T043000Z"),
                ("fee_obligation", "Africa/Casablanca", "2026-01-09T09:00:00+01:00", "2025-Q4",
                 "fee-2025-Q4-assessment", "02:30", "встречается дважды", "03:30", "2026-02-15T03:30:00+00:00", "20260215T033000Z")):
            with self.subTest(field=field, zone=zone, reason=reason), tempfile.TemporaryDirectory() as directory:
                state = copy.deepcopy(self.state)
                state["schedule"].update(timezone=zone, publication_at=publication)
                for action in state["schedule"]["reporting_deadlines"]:
                    action["at"] = "2024-01-01T10:00:00+03:00"
                if quarter:
                    state["schedule"][field] = self.fee_settings(quarters=[quarter])
                state["schedule"][field]["reminder_time"] = invalid
                state_path, output = Path(directory) / "state.json", Path(directory) / "plan"
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = state_path.read_bytes()
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/deal.py"), "plan", "--state", str(state_path),
                     "--now", self.now.isoformat(), "--output-dir", str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(field + ".reminder_time", result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertIn(zone, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(output.exists())
                self.assertEqual(state_path.read_bytes(), before)
                state["schedule"][field]["reminder_time"] = valid
                current = deal.plan(state, self.now)
                event = next(event for event in current["events"] if event["id"] == event_id)
                self.assertEqual(event["at"], expected)
                calendar_event = next(chunk for chunk in deal.ics(current, self.now).split("BEGIN:VEVENT")
                                      if event["uid"] in chunk)
                self.assertIn("DTSTART:" + utc, calendar_event)

    def test_creative_business_deadline_requires_unique_local_time(self):
        for publication, day, reason, expected in (
                ("2026-03-30T02:30:00+02:00", "2026-03-29", "не существует", "2026-03-29T03:30:00+02:00"),
                ("2026-10-26T02:30:00+01:00", "2026-10-25", "встречается дважды", "2026-10-25T03:30:00+01:00")):
            with self.subTest(day=day):
                self.state["schedule"].update(timezone="Europe/Berlin", publication_at=publication,
                                              text_before_business_days=1, working_days=[day])
                with self.assertRaisesRegex(ValueError, "text_before_business_days.*" + reason):
                    deal.plan(self.state, self.now)
                self.state["schedule"]["publication_at"] = publication.replace("T02:30", "T03:30")
                event = next(event for event in deal.plan(self.state, self.now)["events"] if event["id"] == "creative")
                self.assertEqual(event["at"], expected)

    def test_explicit_publication_offset_remains_valid_in_repeated_hour(self):
        for offset in ("+02:00", "+01:00"):
            with self.subTest(offset=offset):
                publication = "2026-10-25T02:30:00" + offset
                self.state["schedule"].update(timezone="Europe/Berlin", publication_at=publication,
                                              text_before_business_days=0)
                events = {event["id"]: event for event in deal.plan(self.state, self.now)["events"]}
                self.assertEqual(events["creative"]["at"], publication)
                self.assertEqual(events["publication"]["at"], publication)

    def test_ord_month_boundary_does_not_require_existing_midnight(self):
        self.state["schedule"].update(timezone="America/Asuncion", publication_at="2023-09-30T09:00:00-04:00")
        for action in self.state["schedule"]["reporting_deadlines"]:
            action["at"] = "2023-01-01T10:00:00+03:00"
        events = self.monthly_events(deal.plan(self.state, self.now))
        self.assertEqual(set(events), {"ord-2023-09", "ord-2023-10"})
        self.assertEqual(events["ord-2023-09"]["at"], "2023-10-30T09:00:00-03:00")

    def test_separate_deals_have_disjoint_calendar_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            states = []
            for name in ("first", "second"):
                output = Path(folder) / (name + ".json")
                subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), "init", "--output", str(output)],
                               check=True, capture_output=True)
                state = json.loads(output.read_text())
                state["schedule"]["publication_at"] = "2026-11-09T09:00:00+03:00"
                state["schedule"]["reporting_deadlines"] = planned_actions()
                states.append(state)
            ids = [{e["uid"] for e in deal.plan(state, self.now)["events"]} for state in states]
            self.assertFalse(ids[0] & ids[1])


class CreativeImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.state_path = self.folder / "deal-state.json"
        self.text_path = self.folder / "approved-post.txt"
        self.links_path = self.folder / "post-links.json"
        self.raw_text = "  🧩 Согласованный пост\r\n\r\nerid: test-token \r\n".encode("utf-8")
        self.links = ["https://example.org/a?utm_source=TG&x=1&x=2#section",
                      "https://example.org/a?utm_source=TG&x=1&x=2#section",
                      "http://EXAMPLE.org:8080/b?next=%2Fpage%3Fa%3D1&space=a+b"]
        self.text_path.write_bytes(self.raw_text)
        self.links_path.write_text(json.dumps(self.links, ensure_ascii=False), encoding="utf-8")
        self.state = json.loads((ROOT / "assets/deal.example.json").read_text())
        self.state["schedule"]["publication_at"] = "2026-11-09T09:00:00+03:00"
        self.state["schedule"]["reporting_deadlines"] = planned_actions()
        self.now = deal.stamp("2026-11-01T12:00:00+03:00")
        self.save_state()

    def save_state(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def import_source(self):
        result = deal.import_creative(self.state_path, self.text_path, self.links_path, self.now)
        self.state = deal.read_json(self.state_path)
        return result

    def inspect_text(self, text):
        self.text_path.write_bytes(text.encode("utf-8"))
        result = self.import_source()
        inspection = deal.inspect_creative(self.state, self.state_path)
        self.assertEqual(result["inspection"], inspection)
        return inspection

    def test_inspect_eof_literal_preserves_real_bracket_and_does_not_invent_one(self):
        for suffix in ("", "[", "]", ",."):
            with self.subTest(suffix=suffix):
                literal = "ExactCode" + suffix
                text = "Реклама.\nerid: " + literal
                inspection = self.inspect_text(text)
                self.assertEqual([item["literal"] for item in inspection["mentions"]], [literal])
                mention = inspection["mentions"][0]
                self.assertEqual(mention["span"], [text.index(literal), len(text)])
                self.assertEqual((mention["line"], mention["column"]), (2, 7))
                self.assertEqual(deal.inspect_creative(self.state, self.state_path, literal), inspection)
                wrong = "ExactCode[" if not suffix else "ExactCode"
                with self.assertRaisesRegex(ValueError, "отсутствует среди упоминаний") as error:
                    deal.inspect_creative(self.state, self.state_path, wrong)
                self.assertIn(json.dumps([literal], ensure_ascii=False), str(error.exception))

    def test_inspect_crlf_spans_unicode_columns_and_context_are_exact(self):
        text = "Первая строка\r\n🧩 ERID:\tАбв-1[\r\nДальше erid: repeat\r\n"
        inspection = self.inspect_text(text)
        self.assertEqual(inspection["text_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        first, second = inspection["mentions"]
        self.assertEqual((first["literal"], first["line"], first["column"]), ("Абв-1[", 2, 9))
        self.assertEqual((second["literal"], second["line"], second["column"]), ("repeat", 3, 14))
        for item in inspection["mentions"]:
            self.assertEqual(text[slice(*item["span"])], item["literal"])
            self.assertEqual(text[slice(*item["context_span"])], item["context"])
        self.assertIn("\r\n", first["context"])
        self.assertEqual(self.state["creative"]["text"], text)

    def test_inspect_preserves_mixed_order_repeats_and_raw_query_values(self):
        text = ("https://example.org/?x=1&erid=A%2FB%5B+Z&erid=repeat#fragment\n"
                "erid: repeat\nhttps://example.org/?erid=A%2FB%5B+Z\nERID: other[\n"
                "https://example.org/?erid=repeat)")
        inspection = self.inspect_text(text)
        self.assertEqual([(item["kind"], item["literal"]) for item in inspection["mentions"]],
                         [("query", "A%2FB%5B+Z"), ("query", "repeat"), ("label", "repeat"),
                          ("query", "A%2FB%5B+Z"), ("label", "other["), ("query", "repeat)")])
        starts = [item["span"][0] for item in inspection["mentions"]]
        self.assertEqual(starts, sorted(starts))
        for item in inspection["mentions"]:
            self.assertEqual(text[slice(*item["span"])], item["literal"])
        with self.assertRaisesRegex(ValueError, "отсутствует среди упоминаний"):
            deal.inspect_creative(self.state, self.state_path, "A/B[ Z")

    def test_inspect_ignores_false_markers_and_handles_absent_erid(self):
        for text in ("Обычный пост без идентификаторов.", "",
                     "noterid: wrong _erid: wrong erid_suffix: wrong еrid: wrong erid=plain "
                     "https://example.org/?myerid=wrong&erid_suffix=wrong&%65rid=wrong"):
            with self.subTest(text=text):
                inspection = self.inspect_text(text)
                self.assertEqual(inspection["mentions"], [])
                with self.assertRaisesRegex(ValueError, "Точные исходные значения: \\[\\]"):
                    deal.inspect_creative(self.state, self.state_path, "missing")

    def test_inspect_checks_imported_provenance_and_does_not_mutate_files_or_statuses(self):
        self.inspect_text("erid: actual")
        self.confirm()
        before = copy.deepcopy(self.state)
        files = {path.relative_to(self.folder): path.read_bytes() for path in self.folder.rglob("*") if path.is_file()}
        result = deal.inspect_creative(self.state, self.state_path, "actual")
        self.assertEqual(result["creative_sha256"], deal.fingerprint(self.state, self.state_path))
        self.assertEqual(self.state, before)
        self.assertEqual(files, {path.relative_to(self.folder): path.read_bytes()
                                 for path in self.folder.rglob("*") if path.is_file()})
        changed = copy.deepcopy(self.state)
        changed["creative"]["text"] += "["
        with self.assertRaisesRegex(ValueError, "creative.text не совпадает"):
            deal.inspect_creative(changed, self.state_path)
        for key in ("text", "links"):
            with self.subTest(source=key):
                source = self.folder / self.state["creative"]["provenance"][key]["path"]
                original = source.read_bytes()
                source.write_bytes(original + b" ")
                with self.assertRaisesRegex(ValueError, f"Исходник креатива {key} изменён"):
                    deal.inspect_creative(self.state, self.state_path)
                source.write_bytes(original)
        self.assertEqual(self.state, before)

    def test_inspect_full_hash_includes_media_and_links_but_text_hash_is_only_text(self):
        first = self.inspect_text("erid: actual")
        self.state["creative"]["media_sha256"] = [hashlib.sha256(b"media").hexdigest()]
        second = deal.inspect_creative(self.state, self.state_path)
        self.assertNotEqual(first["creative_sha256"], second["creative_sha256"])
        self.assertEqual(first["text_sha256"], second["text_sha256"])
        self.save_state()
        self.links_path.write_text(json.dumps(["https://example.org/new"]), encoding="utf-8")
        third = self.import_source()["inspection"]
        self.assertNotEqual(second["creative_sha256"], third["creative_sha256"])
        self.assertEqual(first["text_sha256"], third["text_sha256"])

    def test_inspect_cli_expected_literal_refuses_invented_tail_without_writes(self):
        inspection = self.inspect_text("erid: actual")
        before = self.state_path.read_bytes()
        command = [sys.executable, str(ROOT / "scripts/deal.py"), "inspect-creative", "--state", str(self.state_path)]
        for literal, expected_code in (("actual", 0), ("actual[", 1)):
            with self.subTest(literal=literal):
                result = subprocess.run(command + ["--expect-literal", literal], cwd=self.folder.parent,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected_code, result.stderr)
                if expected_code == 0:
                    self.assertEqual(json.loads(result.stdout), inspection)
                else:
                    self.assertEqual(result.stdout, "")
                    self.assertIn('Точные исходные значения: ["actual"]', result.stderr)
                self.assertEqual(self.state_path.read_bytes(), before)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), "fingerprint", "--state", str(self.state_path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, inspection["creative_sha256"] + "\n")

    def confirm(self):
        proof = {"status": "confirmed", "source": "Сообщение о согласовании этой версии",
                 "checked_at": self.now.isoformat()}
        for key in ("legality", "contract", "payment", "creative_approval", "erid", "ord_creative", "marking"):
            self.state["checks"][key] = dict(proof)
        for key in ("creative_approval", "erid", "ord_creative", "marking"):
            self.state["checks"][key]["creative_sha256"] = deal.fingerprint(self.state, self.state_path)
        for key in ("erid", "ord_creative", "marking"):
            self.state["checks"][key]["token"] = "test-token"
        self.state["checks"]["ord_creative"].update(
            verified={"text": True, "links": True, "media": True},
            source="Полная зарегистрированная карточка ОРД: текст, ссылки, отсутствие медиа")
        self.state["checks"]["scheduled_message"] = {
            "status": "confirmed", "source": "evidence/current-scheduled-queue.png: отложенных записей нет",
            "checked_at": self.now.isoformat(),
            "publication_at": self.state["schedule"]["publication_at"],
            "scheduled_message_id": self.state["publication"]["scheduled_message_id"]}
        self.save_state()

    def approval_message(self):
        source = self.folder / "user-message.txt"
        source.write_bytes("Пост уже согласовали.\r\nТокен ещё уточняем.\r\n".encode("utf-8"))
        return source

    def test_approve_records_full_version_and_does_not_confirm_erid_or_publication(self):
        self.import_source()
        self.state["creative"]["media_sha256"] = [hashlib.sha256(b"image").hexdigest()]
        self.state["checks"]["erid"] = {"status": "pending", "token": "one", "reason": "Получены разные токены"}
        self.state["checks"]["ord_creative"] = {"status": "pending", "token": "another"}
        self.save_state()
        before = copy.deepcopy(self.state)
        source = self.approval_message()
        original = source.read_bytes()
        result = deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        current = deal.read_json(self.state_path)
        proof = current["checks"]["creative_approval"]
        self.assertEqual(proof, result["creative_approval"])
        self.assertEqual(proof["status"], "confirmed")
        self.assertEqual(proof["checked_at"], self.now.isoformat())
        self.assertEqual(proof["creative_sha256"], deal.fingerprint(before, self.state_path))
        self.assertEqual(proof["quote"], "Пост уже согласовали.")
        self.assertEqual(proof["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(proof["original_path"], str(source.resolve()))
        self.assertEqual((self.folder / proof["source"]).read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        self.assertNotIn("creative_approval", deal.readiness_issues(current, self.now))
        self.assertIn("erid", deal.readiness_issues(current, self.now))
        self.assertFalse(deal.check(current, self.now, self.state_path)["ready"])
        self.assertEqual(current["history"][-1]["action"], "approve_creative")
        current["checks"]["creative_approval"] = before["checks"]["creative_approval"]
        current["history"] = before["history"]
        self.assertEqual(current, before)

    def test_approve_cli_uses_scene_time_and_state_relative_snapshot(self):
        self.import_source()
        source = self.approval_message()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deal.py"), "approve-creative", "--state", str(self.state_path),
             "--source", str(source), "--quote", "Пост уже согласовали.", "--now", self.now.isoformat()],
            cwd=self.folder.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)["creative_approval"]
        self.assertEqual(proof["checked_at"], self.now.isoformat())
        self.assertFalse(Path(proof["source"]).is_absolute())
        self.assertTrue((self.folder / proof["source"]).is_file())
        deal.validate(deal.read_json(self.state_path), self.state_path)

    def test_repeated_approval_preserves_first_proof_and_all_files(self):
        self.import_source()
        source = self.approval_message()
        first = deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        self.assertTrue(first["changed"])
        source.write_text("Пост я вчера поставил в очередь.", encoding="utf-8")
        files = {path.relative_to(self.folder): path.read_bytes()
                 for path in self.folder.rglob("*") if path.is_file()}
        later = deal.stamp("2026-11-02T11:30:00+03:00")
        repeated = deal.approve_creative(self.state_path, source, "Пост я вчера поставил в очередь.", later)
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["creative_approval"], first["creative_approval"])
        self.assertEqual(files, {path.relative_to(self.folder): path.read_bytes()
                                 for path in self.folder.rglob("*") if path.is_file()})

    def test_new_version_gets_new_approval_but_old_proof_stays_in_history(self):
        self.import_source()
        source = self.approval_message()
        first = deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        self.text_path.write_bytes(self.raw_text + "Новая версия.".encode("utf-8"))
        self.import_source()
        later = deal.stamp("2026-11-02T11:30:00+03:00")
        second = deal.approve_creative(self.state_path, source, "Пост уже согласовали.", later)
        self.assertTrue(second["changed"])
        self.assertNotEqual(second["creative_approval"]["creative_sha256"], first["creative_approval"]["creative_sha256"])
        self.assertEqual(second["creative_approval"]["checked_at"], later.isoformat())
        history = deal.read_json(self.state_path)["history"]
        approvals = [entry for entry in history if entry["action"] == "approve_creative"]
        self.assertEqual([entry["checked_at"] for entry in approvals], [self.now.isoformat(), later.isoformat()])

    def test_exact_technical_approval_copy_is_rejected_without_rewriting_history(self):
        self.import_source()
        source = self.approval_message()
        deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        self.state = deal.read_json(self.state_path)
        original = copy.deepcopy(self.state["history"][-1])
        self.state["history"] += [{"event": "Получены новые сведения по очереди"}, copy.deepcopy(original)]
        self.save_state()
        before = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "точную копию approve_creative"):
            deal.validate(self.state, self.state_path)
        with self.assertRaisesRegex(ValueError, "точную копию approve_creative"):
            deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.state["history"].pop()
        deal.validate(self.state, self.state_path)
        self.assertEqual(self.state["history"][-2], original)

    def test_approval_copy_guard_does_not_confuse_notes_with_technical_records(self):
        self.import_source()
        source = self.approval_message()
        deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        self.state = deal.read_json(self.state_path)
        note = {"event": "Согласование учтено; ОРД пока проверяется", "at": self.now.isoformat()}
        self.state["history"] += [note, copy.deepcopy(note)]
        deal.validate(self.state, self.state_path)

    def test_approve_rejects_absent_or_empty_quote_without_writes(self):
        self.import_source()
        source = self.approval_message()
        before = self.state_path.read_bytes()
        for quote in (None, "", "  ", "Пост согласовали", "пост уже согласовали."):
            with self.subTest(quote=quote):
                with self.assertRaisesRegex(ValueError, "дословная цитата"):
                    deal.approve_creative(self.state_path, source, quote, self.now)
                self.assertEqual(self.state_path.read_bytes(), before)
                self.assertFalse((self.folder / "creative/approvals").exists())
        with self.assertRaises(FileNotFoundError):
            deal.approve_creative(self.state_path, self.folder / "missing.txt", "Пост уже согласовали.", self.now)
        with self.assertRaisesRegex(ValueError, "часовой пояс"):
            deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now.replace(tzinfo=None))
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_approve_rejects_unimported_or_empty_creative(self):
        source = self.approval_message()
        self.state["creative"]["text"] = "Текст вручную, без сохранённого источника"
        self.save_state()
        for imported in (False, True):
            with self.subTest(imported=imported):
                if imported:
                    self.text_path.write_bytes(b" \r\n\t")
                    self.import_source()
                before = self.state_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "непустой креатив из import-creative"):
                    deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
                self.assertEqual(self.state_path.read_bytes(), before)
                self.assertFalse((self.folder / "creative/approvals").exists())

    def test_approve_validates_imported_sources_before_writing(self):
        self.import_source()
        self.state["creative"]["text"] += " Самовольная правка"
        self.save_state()
        before = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "creative.text не совпадает"):
            deal.approve_creative(self.state_path, self.approval_message(), "Пост уже согласовали.", self.now)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertFalse((self.folder / "creative/approvals").exists())

    def test_approval_source_copy_survives_original_changes_and_detects_tampering(self):
        self.import_source()
        source = self.approval_message()
        result = deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        proof = result["creative_approval"]
        saved = self.folder / proof["source"]
        original = saved.read_bytes()
        source.write_text("Редактированная исходная переписка", encoding="utf-8")
        current = deal.read_json(self.state_path)
        deal.validate(current, self.state_path)
        current["checks"]["creative_approval"]["quote"] = "Несуществующая цитата"
        with self.assertRaisesRegex(ValueError, "Цитата согласования отсутствует"):
            deal.validate(current, self.state_path)
        saved.write_bytes(b"changed snapshot")
        current = deal.read_json(self.state_path)
        with self.assertRaisesRegex(ValueError, "Источник согласования изменён"):
            deal.check(current, self.now, self.state_path)
        # Даже если запись подтверждения снята, существующая копия не перезаписывается.
        current["checks"]["creative_approval"] = {"status": "pending"}
        self.state_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        source.write_bytes(original)
        before = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "не будет перезаписан"):
            deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        self.assertEqual(saved.read_bytes(), b"changed snapshot")
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_new_text_links_or_media_invalidates_command_approval(self):
        self.import_source()
        source = self.approval_message()
        result = deal.approve_creative(self.state_path, source, "Пост уже согласовали.", self.now)
        approved = deal.read_json(self.state_path)
        for key in ("text", "links", "media_sha256"):
            with self.subTest(changed=key):
                self.state = copy.deepcopy(approved)
                self.text_path.write_bytes(self.raw_text + (b"new" if key == "text" else b""))
                self.links_path.write_text(json.dumps(self.links + (["https://example.org/new"] if key == "links" else [])), encoding="utf-8")
                if key == "media_sha256":
                    self.state["creative"][key] = [hashlib.sha256(b"new image").hexdigest()]
                self.save_state()
                self.import_source()
                self.assertEqual(self.state["checks"]["creative_approval"]["status"], "pending")
                self.assertEqual(self.state["checks"]["creative_approval"]["source_sha256"], result["creative_approval"]["source_sha256"])
                self.assertIn("creative_approval", deal.readiness_issues(self.state, self.now))
                self.assertIsNone(deal.completion(self.state, "creative", self.now))

    def test_import_preserves_every_byte_and_does_not_confirm(self):
        result = self.import_source()
        creative = self.state["creative"]
        self.assertEqual(creative["text"].encode("utf-8"), self.raw_text)
        self.assertEqual(creative["links"], self.links)
        self.assertEqual(self.text_path.read_bytes(), self.raw_text)
        for key, original in (("text", self.text_path), ("links", self.links_path)):
            source = creative["provenance"][key]
            saved = self.state_path.parent / source["path"]
            self.assertNotEqual(saved, original)
            self.assertEqual(saved.read_bytes(), original.read_bytes())
            self.assertEqual(source["sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertEqual(source["original_path"], str(original.resolve()))
        self.assertEqual(result["creative_sha256"], deal.fingerprint(self.state, self.state_path))
        self.assertTrue(all(self.state["checks"][key]["status"] == "pending"
                            for key in ("creative_approval", "erid", "ord_creative", "marking")))

    def test_import_command_resolves_sources_relative_to_state(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deal.py"), "import-creative", "--state", str(self.state_path),
             "--text", str(self.text_path), "--links", str(self.links_path), "--now", self.now.isoformat()],
            cwd=self.folder.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        imported = json.loads(result.stdout)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deal.py"), "fingerprint", "--state", str(self.state_path)],
            cwd=self.folder.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), imported["creative_sha256"])

    def test_import_preserves_url_order_duplicates_and_query(self):
        self.import_source()
        self.assertEqual(self.state["creative"]["links"], [
            "https://example.org/a?utm_source=TG&x=1&x=2#section",
            "https://example.org/a?utm_source=TG&x=1&x=2#section",
            "http://EXAMPLE.org:8080/b?next=%2Fpage%3Fa%3D1&space=a+b"])

    def test_manual_text_change_and_rehashed_confirmation_are_rejected(self):
        self.import_source()
        self.confirm()
        self.state["creative"]["text"] = "🧩 Согласованный пост"
        forged_hash = deal.digest(self.state["creative"])
        for key in ("creative_approval", "erid", "ord_creative", "marking"):
            self.state["checks"][key]["creative_sha256"] = forged_hash
        self.save_state()
        for command in ("fingerprint", "check", "plan"):
            with self.subTest(command=command):
                args = [sys.executable, str(ROOT / "scripts/deal.py"), command,
                        "--state", str(self.state_path)]
                if command != "fingerprint":
                    args += ["--now", self.now.isoformat()]
                if command == "plan":
                    args += ["--output-dir", str(self.folder / "plan")]
                result = subprocess.run(args, cwd=self.folder.parent, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("creative.text не совпадает", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertFalse((self.folder / "plan").exists())

    def test_changed_links_are_rejected_without_silent_normalization(self):
        self.import_source()
        self.state["creative"]["links"].reverse()
        with self.assertRaisesRegex(ValueError, "creative.links не совпадает"):
            deal.plan(self.state, self.now, self.state_path)

    def test_new_source_invalidates_old_proofs_and_can_be_approved_again(self):
        self.import_source()
        self.confirm()
        old_proofs = copy.deepcopy(self.state["checks"])
        old_source = self.state["creative"]["provenance"]["text"]
        self.text_path = self.folder / "approved-post-v2.txt"
        self.text_path.write_bytes(self.raw_text + "Новая строка".encode("utf-8"))
        result = self.import_source()
        self.assertTrue(result["changed"])
        for key in ("creative_approval", "erid", "ord_creative", "marking"):
            self.assertEqual(self.state["checks"][key]["status"], "pending")
            self.assertEqual(self.state["checks"][key]["creative_sha256"], old_proofs[key]["creative_sha256"])
            self.assertEqual(self.state["checks"][key]["source"], old_proofs[key]["source"])
            self.assertEqual(self.state["history"][-1]["invalidated_checks"][key], old_proofs[key])
        self.assertEqual((self.folder / old_source["path"]).read_bytes(), self.raw_text)
        self.assertFalse(deal.check(self.state, self.now, self.state_path)["ready"])
        self.assertIn("creative", {e["id"] for e in deal.plan(self.state, self.now, self.state_path)["events"]})
        self.assertIsNone(deal.completion(self.state, "erid", self.now))
        self.confirm()
        self.assertTrue(deal.check(self.state, self.now, self.state_path)["ready"])

    def test_identical_reimport_preserves_proofs_and_does_not_duplicate_history(self):
        self.import_source()
        self.confirm()
        before = copy.deepcopy(self.state)
        result = self.import_source()
        self.assertFalse(result["changed"])
        self.assertEqual(result["invalidated_checks"], [])
        self.assertEqual(self.state, before)

    def test_changed_text_link_map_or_media_resets_all_dependent_checks(self):
        self.import_source()
        self.confirm()
        before = copy.deepcopy(self.state)
        raw_links = self.links_path.read_bytes()
        dependent = {"creative_approval", "erid", "ord_creative", "marking", "legality", "scheduled_message"}
        for changed in ("text", "link_map", "media_sha256"):
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(before)
                self.text_path.write_bytes(self.raw_text + (b"new" if changed == "text" else b""))
                self.links_path.write_bytes(raw_links)
                if changed == "link_map":
                    link_map = copy.deepcopy(before["creative"]["link_map"])
                    link_map[0]["anchor"] = "Новый якорь при прежнем адресе"
                    self.links_path.write_text(json.dumps(link_map, ensure_ascii=False), encoding="utf-8")
                elif changed == "media_sha256":
                    self.state["creative"][changed] = [hashlib.sha256(b"new image").hexdigest()]
                self.save_state()
                result = self.import_source()
                self.assertTrue(result["changed"])
                self.assertEqual(set(result["invalidated_checks"]), dependent)
                for key, proof in before["checks"].items():
                    if key in dependent:
                        self.assertEqual(self.state["checks"][key]["status"], "pending")
                        self.assertEqual(self.state["history"][-1]["invalidated_checks"][key], proof)
                    else:
                        self.assertEqual(self.state["checks"][key], proof)
                self.assertEqual(self.state["publication"], before["publication"])
                self.assertEqual(self.state["history"][-1]["previous_creative_sha256"], deal.digest(before["creative"]))
                after = copy.deepcopy(self.state)
                repeated = self.import_source()
                self.assertFalse(repeated["changed"])
                self.assertEqual(repeated["invalidated_checks"], [])
                self.assertEqual(self.state, after)

    def test_reconfirmed_creative_still_requires_legality_and_queue_checks(self):
        self.import_source()
        self.confirm()
        before = copy.deepcopy(self.state)
        for scheduled_message_id in (None, "existing-message-id"):
            with self.subTest(scheduled_message_id=scheduled_message_id):
                self.state = copy.deepcopy(before)
                self.state["publication"]["scheduled_message_id"] = scheduled_message_id
                self.save_state()
                self.text_path.write_bytes(self.raw_text + b"new")
                self.import_source()
                for key in ("creative_approval", "erid", "ord_creative", "marking"):
                    self.state["checks"][key].update(status="confirmed", creative_sha256=deal.digest(self.state["creative"]))
                result = deal.check(self.state, self.now, self.state_path)
                self.assertFalse(result["ready"])
                self.assertEqual(set(result["blockers"]), {"legality", "scheduled_message"})
                self.assertEqual(self.state["publication"]["scheduled_message_id"], scheduled_message_id)
                for key in ("legality", "scheduled_message"):
                    self.state["checks"][key].update(status="confirmed", source="Повторно проверена новая версия",
                                                     checked_at=self.now.isoformat())
                    if key == "scheduled_message":
                        self.state["checks"][key].update(publication_at=self.state["schedule"]["publication_at"],
                                                         scheduled_message_id=scheduled_message_id)
                    result = deal.check(self.state, self.now, self.state_path)
                    self.assertEqual(set(result["blockers"]), {"scheduled_message"} if key == "legality" else set())
                self.assertTrue(result["ready"])

    def test_reimport_checks_optional_legality_and_queue_hashes(self):
        self.import_source()
        self.confirm()
        before = copy.deepcopy(self.state)
        for key in ("legality", "scheduled_message"):
            for version in (deal.digest(before["creative"]), "old-version"):
                with self.subTest(check=key, version=version):
                    self.state = copy.deepcopy(before)
                    self.state["checks"][key]["creative_sha256"] = version
                    self.save_state()
                    expected = copy.deepcopy(self.state["checks"])
                    result = self.import_source()
                    self.assertFalse(result["changed"])
                    self.assertEqual(result["invalidated_checks"], [key] if version == "old-version" else [])
                    if version == "old-version":
                        expected[key].update(status="pending", reason="Импортирован креатив; требуется подтверждение этой версии")
                    self.assertEqual(self.state["checks"], expected)

    def test_reimporting_manually_changed_text_does_not_keep_forged_approval(self):
        self.import_source()
        self.confirm()
        self.state["creative"]["text"] = "Новая, ещё не согласованная версия"
        for key in ("creative_approval", "erid", "ord_creative", "marking"):
            self.state["checks"][key]["creative_sha256"] = deal.digest(self.state["creative"])
        self.save_state()
        self.text_path = self.folder / "proposed-revision.txt"
        self.text_path.write_text(self.state["creative"]["text"], encoding="utf-8")
        result = self.import_source()
        self.assertTrue(result["changed"])
        self.assertTrue(all(self.state["checks"][key]["status"] == "pending"
                            for key in ("creative_approval", "erid", "ord_creative", "marking")))
        self.assertFalse(deal.check(self.state, self.now, self.state_path)["ready"])

    def test_snapshot_is_independent_but_cannot_be_silently_overwritten(self):
        self.import_source()
        source = self.state["creative"]["provenance"]["text"]
        saved = self.folder / source["path"]
        self.text_path.write_bytes(b"another incoming version")
        self.assertEqual(deal.fingerprint(self.state, self.state_path), deal.digest(self.state["creative"]))
        saved.write_bytes(b"damaged snapshot")
        with self.assertRaisesRegex(ValueError, "Исходник креатива text изменён"):
            deal.check(self.state, self.now, self.state_path)
        self.text_path.write_bytes(self.raw_text)
        with self.assertRaisesRegex(ValueError, "не будет перезаписан"):
            self.import_source()
        self.assertEqual(saved.read_bytes(), b"damaged snapshot")

    def test_absolute_and_state_relative_source_paths(self):
        self.import_source()
        with self.assertRaisesRegex(ValueError, "нужен путь файла состояния"):
            deal.fingerprint(self.state)
        for source in self.state["creative"]["provenance"].values():
            source["path"] = str(self.folder / source["path"])
        self.assertEqual(deal.fingerprint(self.state), deal.digest(self.state["creative"]))

    def test_invalid_links_input_does_not_change_state(self):
        before = self.state_path.read_bytes()
        for value in ({"unexpected": "object"}, [["Номер", "Якорь", "URL"],
                                                [1, "Якорь без адреса", "неизвестный переход"]],
                      ["видимая ссылка в тексте"], [""], [None], ["/relative"], ["//example.org/"],
                      ["mailto:mail@example.org"], ["https://"], ["https:///path"],
                      ["https://example.org/a b"], ["https://example.org/\npath"], ["https://[bad"]):
            with self.subTest(links=value):
                self.links_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Карта ссылок"):
                    self.import_source()
                self.assertEqual(self.state_path.read_bytes(), before)
                self.assertFalse((self.folder / "creative").exists())

    def test_recorded_source_rejects_non_urls_but_legacy_state_remains_compatible(self):
        self.import_source()
        links = [["Якорь", "URL"], ["https://example.org/", "видимая ссылка в тексте"]]
        raw = json.dumps(links, ensure_ascii=False).encode("utf-8")
        source = self.state["creative"]["provenance"]["links"]
        (self.folder / source["path"]).write_bytes(raw)
        source["sha256"] = hashlib.sha256(raw).hexdigest()
        self.state["creative"]["links"] = links
        with self.assertRaisesRegex(ValueError, "creative.link_map"):
            deal.fingerprint(self.state, self.state_path)
        del self.state["creative"]["provenance"]
        self.assertEqual(deal.fingerprint(self.state), deal.digest(self.state["creative"]))


if __name__ == "__main__":
    unittest.main()
