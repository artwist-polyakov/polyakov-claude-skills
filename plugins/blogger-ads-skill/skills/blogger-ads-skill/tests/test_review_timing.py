"""Извлечённые сроки связаны с нужными полями/событиями и не принимаются автоматически."""
import copy
import json
import unittest

import test_review as fixtures

review, deal = fixtures.review, fixtures.deal


class ReviewTimingTests(unittest.TestCase):
    setUp = fixtures.ReviewTests.setUp
    pdf = staticmethod(fixtures.ReviewTests.pdf)
    save = fixtures.ReviewTests.save
    build = fixtures.ReviewTests.build
    check = fixtures.ReviewTests.check
    save_state_plan = fixtures.ReviewTests.save_state_plan

    def term(self, category="client_reporting", target="/schedule/link_after_publication_hours", value=1,
             mode="provisional", item_id="link-deadline", event_id="link", event_at="2026-11-09T10:00:00+03:00"):
        item = copy.deepcopy(self.data["decisions"][0])
        item.update(id=item_id, category=category, status="proposed")
        item["source"]["quote"] = "Send the link within 1 hour after publication."
        self.pdf(self.source, ["One.", "Two.", item["source"]["quote"]])
        item["decision"] = {"action": "keep", "reason": "План по полученному предложению до отдельного решения.",
                            "selected": "Предварительный срок", "plan": "Показать его в плане с оговоркой."}
        timing = {"subject": "Ссылка и снимок заказчику после публикации", "target": target, "mode": mode,
                  "received_value": value, "selected_value": value if mode in ("provisional", "agreed") else None,
                  "reason": "Полученное предложение; отсутствие подписи не скрывает содержание срока."}
        item["implementation"] = {"kind": "known", "timing": timing,
                                  "state": [{"path": target, "value": timing["selected_value"]}],
                                  "plan": [{"id": event_id, "at": event_at}]}
        if mode in ("deferred", "unresolved"):
            item["implementation"].update(kind="unresolved", reason="Пользователь ещё выбирает редакцию.",
                                          plan=[{"id": "report-clarify", "at": "2026-11-02T10:00:00+03:00"}])
            item["decision"]["action"] = "defer"
        self.data["decisions"] = [item]
        self.data["scope"]["categories"] = [category]
        self.state["schedule"][target.rsplit("/", 1)[1]] = timing["selected_value"]
        return item

    def record(self):
        self.state["schedule"]["reviewed_terms"] = {
            item["id"]: review.reviewed_timing_record(item) for item in self.data["decisions"]
            if "timing" in item["implementation"]}

    def prepare(self):
        self.record()
        self.build()
        return self.save_state_plan()

    def test_missing_timing_cannot_hide_one_hour_behind_contract_status(self):
        item = self.term()
        item["implementation"] = {"kind": "unresolved", "reason": "Нет подписи",
                                  "state": [{"path": "/checks/contract/status", "value": "pending"}],
                                  "plan": [{"id": "report-clarify", "at": "2026-11-02T10:00:00+03:00"}]}
        with self.assertRaisesRegex(ValueError, "нужен implementation.timing"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_known_proposed_link_survives_as_conditional_event_and_check_is_readonly(self):
        self.term()
        plan = self.prepare()
        before = {p: p.read_bytes() for p in (self.state_path, self.plan_path, *self.output.iterdir())}
        self.assertTrue(self.check()["state_checked"])
        event = next(row for row in plan["events"] if row["id"] == "link")
        self.assertEqual(event["at"], "2026-11-09T10:00:00+03:00")
        self.assertEqual(event["reviewed_terms"][0]["mode"], "provisional")
        self.assertIn("принятие не подтверждено", event["note"])
        self.assertEqual(self.state["checks"]["contract"]["status"], "pending")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_confirmed_check_cannot_replace_canonical_deadline(self):
        item = self.term()
        item["implementation"]["state"] = [{"path": "/checks/contract/status", "value": "confirmed"}]
        with self.assertRaisesRegex(ValueError, "свяжите выбранный срок.*link_after_publication_hours"):
            self.build()
        item["implementation"]["timing"]["target"] = "/checks/contract/status"
        with self.assertRaisesRegex(ValueError, "checks.status не является сроком"):
            self.build()

    def test_unrelated_plan_event_does_not_replace_link_event(self):
        item = self.term()
        item["implementation"]["plan"] = [{"id": "report-clarify", "at": "2026-11-02T10:00:00+03:00"}]
        self.prepare()
        with self.assertRaisesRegex(ValueError, "соответствующее событие/контроль"):
            self.check()

    def test_state_must_keep_typed_value_and_source_by_decision_id(self):
        self.term()
        self.prepare()
        self.state["schedule"]["reviewed_terms"].clear()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "schedule.reviewed_terms"):
            self.check()

    def test_known_proposal_cannot_be_labeled_unknown(self):
        item = self.term(mode="deferred")
        item["implementation"]["timing"]["mode"] = "unresolved"
        with self.assertRaisesRegex(ValueError, "Известное предложение нельзя объявить неизвестным"):
            self.build()

    def test_deferred_claims_preserved_without_imposing_them(self):
        item = self.term(category="acceptance", target="/schedule/claims_after_end_hours", value=24,
                         mode="deferred", item_id="claims-proposal")
        item["implementation"]["timing"]["subject"] = "Замечания в течение 24 часов после окончания размещения"
        plan = self.prepare()
        self.assertTrue(self.check()["state_checked"])
        self.assertIsNone(self.state["schedule"]["claims_after_end_hours"])
        self.assertEqual(self.state["schedule"]["reviewed_terms"]["claims-proposal"]["received_value"], 24)
        self.assertFalse(any(event["id"] == "claims" for event in plan["events"]))
        self.assertEqual(next(event for event in plan["events"] if event["id"] == "report-clarify")["reviewed_terms"][0]["mode"], "deferred")

    def test_deferred_term_must_not_also_be_scheduled_as_obligation(self):
        item = self.term(category="acceptance", target="/schedule/claims_after_end_hours", value=24,
                         mode="deferred", item_id="claims-proposal")
        item["implementation"]["state"] = [{"path": "/checks/contract/status", "value": "pending"}]
        self.state["schedule"]["claims_after_end_hours"] = 24
        self.prepare()
        with self.assertRaisesRegex(ValueError, "отложенный срок не должен становиться обязанностью"):
            self.check()

    def test_agreed_requires_explicit_acceptance_source_and_can_preserve_previous_value(self):
        item = self.term(mode="agreed")
        timing = item["implementation"]["timing"]
        with self.assertRaisesRegex(ValueError, "agreement_source"):
            self.build()
        timing.update(agreement_source="Сохранённое письмо обеих сторон от 1 ноября: срок 2 часа.", selected_value=2)
        item["implementation"]["state"][0]["value"] = 2
        item["implementation"]["plan"][0]["at"] = "2026-11-09T11:00:00+03:00"
        self.state["schedule"]["link_after_publication_hours"] = 2
        plan = self.prepare()
        self.assertTrue(self.check()["state_checked"])
        record = self.state["schedule"]["reviewed_terms"][item["id"]]
        self.assertEqual((record["received_value"], record["selected_value"]), (1, 2))
        event = next(event for event in plan["events"] if event["id"] == "link")
        self.assertEqual(event["reviewed_terms"][0]["mode"], "agreed")

    def test_same_category_supports_link_statistics_and_non_timed_condition(self):
        link = self.term()
        statistics = copy.deepcopy(link)
        statistics.update(id="statistics", topic="Статистика после размещения")
        record = {"id": "statistics", "title": "Сохранить статистику", "at": "2026-11-11T12:00:00+03:00",
                  "source": "Полученный проект: статистика через три часа после окончания размещения."}
        timing = statistics["implementation"]["timing"]
        timing.update(subject="Статистика после размещения", target="/schedule/reporting_deadlines/1",
                      received_value=record, selected_value=record)
        statistics["implementation"]["state"] = [{"path": timing["target"], "value": record}]
        statistics["implementation"]["plan"] = [{"id": "report-statistics", "at": record["at"]}]
        self.state["schedule"]["reporting_deadlines"].append(record)
        formats = copy.deepcopy(link)
        formats.update(id="statistics-format", topic="Формат статистики")
        formats["implementation"] = {"kind": "no_state_change", "reason": "Состав показателей не меняет расписание.",
                                     "timing": {"subject": "Формат и состав показателей", "target": None,
                                                "mode": "not_applicable", "received_value": None, "selected_value": None,
                                                "reason": "Пункт перечисляет просмотры и снимок; срока в нём нет."}}
        self.data["decisions"] = [link, statistics, formats]
        self.prepare()
        self.assertTrue(self.check()["state_checked"])
        self.assertEqual(set(self.state["schedule"]["reviewed_terms"]), {"link-deadline", "statistics", "statistics-format"})

    def test_non_timer_needs_reason_but_not_number_of_hours(self):
        item = self.term()
        item["implementation"] = {"kind": "no_state_change", "reason": "Состав отчёта",
                                  "timing": {"subject": "Состав отчёта", "target": None,
                                             "mode": "not_applicable", "received_value": None, "selected_value": None, "reason": ""}}
        with self.assertRaisesRegex(ValueError, "timing.reason"):
            self.build()

    def erid(self):
        term = {"precision": "relative", "condition": "Передать ERID за один рабочий день до публикации",
                "source": "Полученный договор 4.1; принятие пока не подтверждено",
                "calculation": {"kind": "business_days_before_publication", "days": 1, "requires": []}}
        item = self.term(category="erid", target="/schedule/deadline_terms/erid", value=term,
                         item_id="erid-deadline", event_id="report-clarify", event_at="2026-11-02T10:00:00+03:00")
        self.state["schedule"].pop("erid", None)
        self.state["schedule"]["deadline_terms"]["erid"] = term
        item["implementation"]["timing"]["subject"] = "Передача ERID до публикации"
        return item

    def test_relative_erid_survives_without_inventing_deadline_hour(self):
        self.erid()
        plan = self.prepare()
        self.assertTrue(self.check()["state_checked"])
        control = next(row for row in plan["deadline_controls"] if row["check"] == "erid")
        self.assertEqual(control["date"], "2026-11-06")
        self.assertIsNone(self.state["schedule"]["erid_due_at"])
        event = next(row for row in plan["events"] if row["id"] == "report-clarify")
        self.assertEqual(event["reviewed_terms"][0]["mode"], "provisional")

    def test_erid_confirmed_status_cannot_hide_unspecified_term(self):
        item = self.erid()
        item["implementation"]["state"] = [{"path": "/checks/erid/status", "value": "confirmed"}]
        with self.assertRaisesRegex(ValueError, "свяжите выбранный срок.*deadline_terms/erid"):
            self.build()

    def test_erid_plan_needs_control_for_selected_term(self):
        self.erid()
        other = copy.deepcopy(self.state["schedule"]["reporting_deadlines"][0])
        other["id"] = "other-checks"
        self.state["schedule"]["reporting_deadlines"].append(other)
        self.state["schedule"]["reporting_deadlines"][0]["checks"] = ["legality"]
        self.prepare()
        with self.assertRaisesRegex(ValueError, "соответствующее событие/контроль"):
            self.check()

    def completed_erid_control(self, precision):
        item = self.erid()
        if precision == "date":
            term = {"precision": "date", "condition": "Передать ERID 6 ноября; час не задан",
                    "source": "Полученный договор 4.1, ожидает принятия"}
            item["implementation"]["timing"].update(received_value=term, selected_value=term)
            item["implementation"]["state"][0]["value"] = term
            self.state["schedule"]["deadline_terms"]["erid"] = term
        remaining = copy.deepcopy(self.state["schedule"]["reporting_deadlines"][0])
        remaining["id"] = "remaining-checks"
        self.state["schedule"]["reporting_deadlines"].append(remaining)
        self.state["checks"]["erid"].update(status="confirmed", source="Токен получен из карточки",
                                             checked_at="2026-11-01T11:00:00+03:00")
        self.state.setdefault("completed_events", {})["report-clarify"] = {
            "status": "confirmed", "checked_at": "2026-11-01T11:00:00+03:00", "source": "Проверка токена выполнена."}
        plan = self.prepare()
        self.assertTrue(self.check()["state_checked"])
        event = next(row for row in plan["completed"] if row["id"] == "report-clarify")
        self.assertEqual(event["reviewed_terms"][0]["mode"], "provisional")
        self.assertIsNone(self.state["schedule"]["erid_due_at"])
        self.assertIn(item["implementation"]["timing"]["selected_value"]["condition"], event["note"])

    def test_completed_relative_erid_control_is_still_checked(self):
        self.completed_erid_control("relative")

    def test_completed_date_only_erid_control_does_not_gain_deadline_hour(self):
        self.completed_erid_control("date")

    def test_unknown_publication_preserves_content_without_fake_event(self):
        item = self.term()
        item["implementation"].update(plan=[], plan_pending_reason="Дата публикации ещё не согласована.")
        self.state["schedule"]["publication_at"] = None
        self.record()
        self.build()
        self.state["documents"] = [{"path": self.source.name, "sha256": review.sha256(self.source.read_bytes()), "status": "received"}]
        self.state_path.write_text(json.dumps(self.state))
        self.assertTrue(review.check_state(self.output, self.state_path)["state_checked"])
        self.assertEqual(self.state["schedule"]["link_after_publication_hours"], 1)


if __name__ == "__main__":
    unittest.main()
