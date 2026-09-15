"""Получение отчёта через прокси учитывает очередь, ограничения и срок ожидания."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from config import DirectFailure, forget_secrets, resolve_settings
from direct import Client, Retries
from errors import TransportFailure
from reports import POLL_DEFAULT, one_page


REPORT = "CampaignId\tClicks\n123\t5\nTotal rows: 1\n"


class Clock:
    def __init__(self):
        self.now = 0
        self.pauses = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds


class SequenceTransport:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def send(self, url, body, headers):
        self.calls.append((url, body, headers))
        return next(self.replies)


def refusal(detail="Попробуйте позднее"):
    return json.dumps({"error": {"error_code": 90001, "error_string": "Ответ сервиса",
                                 "error_detail": detail}}, ensure_ascii=False)


class ProxyReportsTests(unittest.TestCase):
    def setUp(self):
        forget_secrets()
        self.addCleanup(forget_secrets)
        self.clock = Clock()

    def client(self, replies):
        settings = resolve_settings(
            from_file={"YANDEX_DIRECT_TOKEN": "proxy-test-token",
                       "YANDEX_DIRECT_API_BASE_URL": "https://proxy.example/direct"},
            environ={},
        )
        client = Client(settings, SequenceTransport(replies),
                        retries=Retries(sleep=lambda seconds: self.fail("Вложенный повтор")))
        return client

    def run_report(self, client, **kwargs):
        return one_page(
            client, {"ReportName": "test-report"}, account="advertiser",
            report_type="CAMPAIGN_PERFORMANCE_REPORT", processing_mode="offline",
            use_operator_units=False, sleep=self.clock.sleep, clock=self.clock, **kwargs,
        )

    def test_429_then_queue_then_report_with_and_without_error_envelope(self):
        for raw in (refusal(), "Too many requests"):
            with self.subTest(raw=raw):
                self.clock = Clock()
                client = self.client([
                    (429, {"rEtRyIn": "12"}, raw),
                    (201, {"retryIn": "2"}, ""),
                    (202, {"retryIn": "3"}, ""),
                    (200, {}, REPORT),
                ])
                self.assertEqual(self.run_report(client), REPORT)
                self.assertEqual(self.clock.pauses, [12, 2, 3])
                self.assertEqual(len(client.transport.calls), 4)
                self.assertTrue(all(call == client.transport.calls[0]
                                    for call in client.transport.calls))

    def test_429_retry_in_is_not_limited_by_queue_polling_cap(self):
        client = self.client([(429, {"retryIn": "120"}, refusal()), (200, {}, REPORT)])
        self.assertEqual(self.run_report(client, wait=200), REPORT)
        self.assertEqual(self.clock.pauses, [120])

    def test_429_wait_is_bounded_by_total_budget_for_both_error_forms(self):
        for raw in (refusal(), "Too many requests"):
            for wait in (10, 12):
                with self.subTest(raw=raw, wait=wait):
                    self.clock = Clock()
                    client = self.client([(429, {"retryIn": "12"}, raw)])
                    with self.assertRaises(DirectFailure) as caught:
                        self.run_report(client, wait=wait)
                    self.assertIn(f"за {wait} с", str(caught.exception))
                    self.assertIn("429", str(caught.exception))
                    self.assertEqual(self.clock.pauses, [wait])
                    self.assertEqual(len(client.transport.calls), 1)

    def test_queue_uses_remaining_budget_after_429(self):
        client = self.client([
            (429, {"retryIn": "8"}, refusal()), (201, {"retryIn": "5"}, ""),
        ])
        with self.assertRaises(DirectFailure):
            self.run_report(client, wait=10)
        self.assertEqual(self.clock.pauses, [8, 2])
        self.assertEqual(len(client.transport.calls), 2)

    def test_invalid_retry_in_uses_existing_default(self):
        for value in (None, "wrong", "nan", "inf", "-5"):
            with self.subTest(value=value):
                self.clock = Clock()
                client = self.client([(429, {"retryIn": value}, refusal()), (200, {}, REPORT)])
                self.assertEqual(self.run_report(client), REPORT)
                self.assertEqual(self.clock.pauses, [POLL_DEFAULT])

    def test_retryable_503_envelope_is_retried(self):
        client = self.client([(503, {}, refusal()), (200, {}, REPORT)])
        self.assertEqual(self.run_report(client), REPORT)
        self.assertEqual(self.clock.pauses, [POLL_DEFAULT])

    def test_expired_retry_budget_preserves_409_details_without_claiming_queue(self):
        detail = "Выберите логин токена. " * 30 + "Полная инструкция: X-Token-Login."
        client = self.client([(409, {}, refusal(detail)), (409, {}, refusal(detail))])
        with self.assertRaises(DirectFailure) as caught:
            self.run_report(client, wait=10)
        self.assertIn(detail, str(caught.exception))
        self.assertIn("за 10 с", str(caught.exception))
        self.assertNotIn("очереди", str(caught.exception))
        self.assertEqual(self.clock.pauses, [5, 5])
        self.assertEqual(len(client.transport.calls), 2)

    def test_expired_retry_budget_preserves_429_details(self):
        detail = "Лимит запросов. " * 30 + "Повторите через 12 секунд."
        client = self.client([(429, {"retryIn": "12"}, refusal(detail))])
        with self.assertRaises(DirectFailure) as caught:
            self.run_report(client, wait=10)
        self.assertIn(detail, str(caught.exception))
        self.assertNotIn("очереди", str(caught.exception))

    def test_successful_queue_response_clears_old_proxy_error(self):
        detail = "Временная ошибка прокси, которая уже устранена."
        client = self.client([
            (429, {"retryIn": "8"}, refusal(detail)), (201, {"retryIn": "5"}, ""),
        ])
        with self.assertRaises(DirectFailure) as caught:
            self.run_report(client, wait=10)
        self.assertNotIn(detail, str(caught.exception))
        self.assertIn("очереди", str(caught.exception))

    def test_second_500_preserves_full_proxy_explanation(self):
        detail = "Повторяемое пояснение. " * 30 + "Конец инструкции."
        client = self.client([(500, {}, refusal()), (500, {}, refusal(detail))])
        with self.assertRaises(TransportFailure) as caught:
            self.run_report(client)
        self.assertIn(detail, str(caught.exception))
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(len(client.transport.calls), 2)

    def test_502_still_switches_to_offline(self):
        client = self.client([(502, {}, refusal()), (200, {}, REPORT)])
        result = one_page(
            client, {"ReportName": "test-report"}, account="advertiser",
            report_type="CAMPAIGN_PERFORMANCE_REPORT", processing_mode="online",
            use_operator_units=False, sleep=self.clock.sleep, clock=self.clock,
        )
        self.assertEqual(result, REPORT)
        self.assertEqual([call[2]["processingMode"] for call in client.transport.calls],
                         ["online", "offline"])


if __name__ == "__main__":
    unittest.main()
