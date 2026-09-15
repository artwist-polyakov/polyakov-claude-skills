"""Коды отказов прокси не меняют тип токена и источник оплаты баллов."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS)]

import accounts
from direct import Client, Retries
from errors import ApiFailure
import whoami
from test_proxy_transport import ACCOUNT, DETAIL, envelope, settings, wire_response


class ProxyErrorCodesTests(unittest.TestCase):
    def client(self, *replies):
        transport = Mock()
        transport.send.side_effect = [(status, {}, json.dumps(payload))
                                      for status, payload in replies]
        sleep = Mock()
        client = Client(settings(), transport, retries=Retries(sleep=sleep))
        return client, sleep

    def test_proxy_54_does_not_probe_client_token(self):
        for status in (400, 401, 403, 409, 422):
            with self.subTest(status=status):
                client, sleep = self.client((status, envelope(54)))
                with self.assertRaises(ApiFailure) as caught:
                    accounts.detect(client)
                self.assertIn(DETAIL, str(caught.exception))
                self.assertIsNone(caught.exception.code)
                self.assertEqual(caught.exception.raw["error_code"], 54)
                self.assertEqual(client.transport.send.call_count, 1)
                sleep.assert_not_called()

    def test_proxy_54_on_clients_keeps_original_explanation(self):
        client, sleep = self.client((200, envelope(54, "Не агентство")),
                                    (409, envelope(54)))
        with self.assertRaises(ApiFailure) as caught:
            accounts.detect(client)
        self.assertIn(DETAIL, str(caught.exception))
        self.assertNotIn("дело не в типе токена", str(caught.exception))
        self.assertEqual(client.transport.send.call_count, 2)
        sleep.assert_not_called()

    def test_relayed_54_still_identifies_client_token(self):
        client, sleep = self.client(
            (200, envelope(54, "Не агентство")),
            (200, {"result": {"Clients": [{"Login": ACCOUNT}]}}),
        )
        kind, owner, cabinets = accounts.detect(client)
        self.assertEqual(kind, accounts.CLIENT)
        self.assertEqual(owner, ACCOUNT)
        self.assertEqual([cabinet.login for cabinet in cabinets], [ACCOUNT])
        self.assertEqual(client.transport.send.call_count, 2)
        sleep.assert_not_called()

    def test_proxy_152_does_not_switch_to_agency_units(self):
        for status, expected in ((409, None), (200, True)):
            with self.subTest(status=status):
                client, sleep = self.client((status, envelope(152)))
                selected = accounts.Accounts(
                    profile="production", owner="agency", cabinets=[],
                    kind=accounts.AGENCY, client=client,
                )
                self.assertIs(selected.use_operator_units(ACCOUNT), expected)
                self.assertEqual(client.transport.send.call_count, 1)
                sleep.assert_not_called()

    def test_whoami_does_not_interpret_proxy_54_or_hide_its_details(self):
        for status in (400, 403, 409):
            for first_probe_succeeded in (False, True):
                with self.subTest(status=status, second_call=first_probe_succeeded):
                    responses = [wire_response(status, envelope(54))]
                    if first_probe_succeeded:
                        responses.insert(0, wire_response(200, envelope(54, "Не агентство")))
                    opener = Mock()
                    opener.open.side_effect = responses
                    output, errors = StringIO(), StringIO()
                    with (
                        patch.object(whoami, "preload_secrets"),
                        patch.object(whoami, "settings_from_env", return_value=settings()),
                        patch.object(whoami, "OPENER", opener),
                        redirect_stdout(output), redirect_stderr(errors),
                    ):
                        self.assertEqual(whoami.main(["--json"]), 1)
                    self.assertEqual(output.getvalue(), "")
                    self.assertIn(DETAIL, errors.getvalue())
                    self.assertNotIn("Traceback", errors.getvalue())
                    self.assertNotIn("дело не в типе токена", errors.getvalue())
                    self.assertEqual(opener.open.call_count, len(responses))


if __name__ == "__main__":
    unittest.main()
