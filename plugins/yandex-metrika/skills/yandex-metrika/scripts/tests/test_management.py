#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Offline integration checks for API segments and counter grants."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit


SCRIPTS = Path(__file__).resolve().parents[1]


def fake_curl(args):
    if any("Authorization: OAuth " in argument for argument in args):
        raise AssertionError("OAuth token must not be passed in curl argv")
    header_files = [Path(args[index + 1][1:])
                    for index, argument in enumerate(args[:-1])
                    if argument == "-H" and args[index + 1].startswith("@")]
    if not header_files:
        raise AssertionError("curl must read private headers from a file")
    if not any("Authorization: OAuth " in path.read_text()
               for path in header_files):
        raise AssertionError("OAuth header is missing")
    if any(path.stat().st_mode & 0o077 for path in header_files):
        raise AssertionError("OAuth header file is not private")

    method = "GET"
    headers = body_file = url = None
    query = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-D", "-H", "-X", "--data-binary", "--data-urlencode"):
            value = args[index + 1]
            if arg == "-D":
                headers = Path(value)
            elif arg == "-X":
                method = value
            elif arg == "--data-binary":
                body_file = Path(value.removeprefix("@"))
            elif arg == "--data-urlencode":
                key, value = value.split("=", 1)
                query[key] = value
            index += 2
            continue
        if arg.startswith("https://"):
            url = arg
        index += 1

    path = urlsplit(url).path
    body = json.loads(body_file.read_text()) if body_file else None
    log_path = Path(os.environ["FAKE_CURL_LOG"])
    with log_path.open("a") as stream:
        stream.write(json.dumps({"method": method, "path": path,
                                 "query": query, "body": body}) + "\n")

    state_path = Path(os.environ["FAKE_STATE"])
    state = json.loads(state_path.read_text())
    permission = os.environ.get("FAKE_PERMISSION", "edit")
    status = 200
    response = {}

    forced = os.environ.get("FAKE_MUTATION_STATUS")
    if method != "GET" and forced:
        status = int(forced)
        response = {"errors": [{"message": "synthetic failure"}]}
    elif path == "/management/v1/counter/12345":
        response = {"counter": {"id": 12345, "owner_login": "counter-owner",
                                "permission": permission}}
    elif path.endswith("/apisegment/segments"):
        if method == "GET":
            response = {"segments": state["segments"]}
        elif method == "POST":
            segment = dict(body["segment"], segment_id=501, counter_id=12345,
                           status="active", segment_source="api",
                           create_time="2026-09-09T10:00:00+03:00")
            if os.environ.get("FAKE_STALE_SEGMENT_NAME"):
                segment["name"] = os.environ["FAKE_STALE_SEGMENT_NAME"]
            state["segments"].append(segment)
            response = {"segment": segment}
    elif "/apisegment/segment/" in path:
        segment_id = int(path.rsplit("/", 1)[1])
        segment = next((item for item in state["segments"]
                        if item["segment_id"] == segment_id), None)
        if method == "GET":
            if segment is None:
                status, response = 404, {"errors": [{"message": "not found"}]}
            else:
                response = {"segment": segment}
        elif method == "DELETE":
            state["segments"] = [item for item in state["segments"]
                                 if item["segment_id"] != segment_id]
            response = {"success": True}
    elif path.endswith("/grants"):
        if method == "GET":
            response = {"grants": state["grants"]}
        elif method == "POST":
            grant = dict(body["grant"], created_at="2026-09-09T10:00:00Z")
            if os.environ.get("FAKE_STALE_PERMISSION"):
                grant["perm"] = os.environ["FAKE_STALE_PERMISSION"]
            state["grants"].append(grant)
            response = {"grant": grant}
    elif path.endswith("/grant"):
        login = query.get("user_login")
        if method == "GET":
            grant = next((item for item in state["grants"]
                          if item["user_login"] == login), None)
            if grant is None:
                if login == "valid-no-grant":
                    status, response = 400, {
                        "errors": [{"error_type": "invalid_parameter",
                                    "message": "У пользователя valid-no-grant нет гранта на счетчик 12345",
                                    "location": "user_login"}],
                        "code": 400,
                    }
                elif login == "unknown-user":
                    status, response = 400, {
                        "errors": [{"error_type": "invalid_parameter",
                                    "message": "This user doesn't exist",
                                    "location": "user_login"}],
                        "code": 400,
                    }
                elif login == "ambiguous-error":
                    status, response = 400, {
                        "errors": [{"error_type": "invalid_parameter",
                                    "message": "У пользователя нет гранта",
                                    "location": "counter_id"}],
                        "code": 400,
                    }
                else:
                    status, response = 404, {"errors": [{"message": "not found"}]}
            else:
                response = {"grant": grant}
        elif method == "PUT":
            login = body["grant"]["user_login"]
            grant = next(item for item in state["grants"]
                         if item["user_login"] == login)
            created_at = grant["created_at"]
            grant.clear()
            grant.update(body["grant"], created_at=created_at)
            if os.environ.get("FAKE_STALE_PERMISSION"):
                grant["perm"] = os.environ["FAKE_STALE_PERMISSION"]
            response = {"grant": grant}
    else:
        raise AssertionError(f"Unexpected request: {method} {path}")

    state_path.write_text(json.dumps(state))
    reason = {200: "OK", 403: "Forbidden", 404: "Not Found", 429: "Too Many Requests"}.get(status, "Error")
    headers.write_text(f"HTTP/1.1 {status} {reason}\r\n\r\n")
    print(json.dumps(response), end="")
    return 0


class ManagementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="metrika-management-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("common.sh", "segments.sh", "grants.sh",
                     "management_json.py"):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        config = self.root / "config"
        config.mkdir()
        self.token = "synthetic-secret-token"
        (config / ".env").write_text(f"YANDEX_METRIKA_TOKEN={self.token}\n")
        (config / ".env").chmod(0o600)
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "requests.jsonl"
        self.reset_state()

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        commands = ("sh", "dirname", "mkdir", "sed", "tr", "grep", "awk", "stat",
                    "head", "wc", "mktemp", "rm", "cp", "cat", "sleep")
        for command in commands:
            executable = shutil.which(command, path=os.defpath)
            self.assertIsNotNone(executable, command)
            (bin_dir / command).symlink_to(executable)
        (bin_dir / "python3").symlink_to(sys.executable)
        curl = bin_dir / "curl"
        curl.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                        f"{shlex.quote(str(Path(__file__).resolve()))} --fake-curl \"$@\"\n")
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir),
                        TMPDIR=str(self.root / "tmp"),
                        FAKE_CURL_LOG=str(self.log_path),
                        FAKE_STATE=str(self.state_path),
                        FAKE_PERMISSION="edit")
        self.env.pop("FAKE_MUTATION_STATUS", None)
        self.env.pop("FAKE_STALE_PERMISSION", None)
        self.env.pop("FAKE_STALE_SEGMENT_NAME", None)

    def reset_state(self):
        self.state_path.write_text(json.dumps({
            "segments": [
                {"segment_id": 42, "counter_id": 12345,
                 "name": "Existing", "expression": "ym:s:isRobot=='No'",
                 "status": "active", "segment_source": "api",
                 "create_time": "2026-01-01T00:00:00Z"},
                {"segment_id": 43, "counter_id": 12345,
                 "name": "Second segment",
                 "expression": "ym:s:deviceCategory=='mobile'",
                 "status": "active", "segment_source": "api",
                 "create_time": "2026-01-02T00:00:00Z"},
            ],
            "grants": [
                {"user_login": "existing-client", "perm": "view",
                 "comment": "campaign", "partner_data_access": False,
                 "created_at": "2026-01-01T00:00:00Z"},
                {"user_login": "second-client", "perm": "analyst",
                 "comment": "second", "partner_data_access": True,
                 "created_at": "2026-01-02T00:00:00Z"},
                {"user_login": "restricted-client",
                 "perm": "analyst_access_filter", "comment": "restricted",
                 "partner_data_access": False, "access_filters": [{"id": 77}],
                 "created_at": "2026-01-03T00:00:00Z"},
                {"user_login": "", "perm": "public_stat", "comment": "",
                 "partner_data_access": False,
                 "created_at": "2026-01-04T00:00:00Z"},
            ],
        }))

    def run_script(self, script, *args, success=True):
        result = subprocess.run(["sh", str(self.scripts / script),
                                 "--counter", "12345", *args],
                                env=self.env, capture_output=True, text=True,
                                timeout=20)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)
        return result

    def requests(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def mutations(self):
        return [item for item in self.requests() if item["method"] != "GET"]

    def test_segment_preview_and_create_escape_values(self):
        name = 'Audience "A" \\ mobile'
        expression = "EXISTS(ym:pv:URL=@'/catalog/\\'special')"
        preview = self.run_script("segments.sh", "--action", "create",
                                  "--name", name, "--expression", expression)
        self.assertIn("Только проверка", preview.stdout)
        self.assertEqual(self.mutations(), [])

        self.log_path.unlink()
        result = self.run_script("segments.sh", "--action", "create",
                                 "--name", name, "--expression", expression,
                                 "--apply")
        mutation = self.mutations()
        self.assertEqual([(item["method"], item["path"]) for item in mutation], [
            ("POST", "/management/v1/counter/12345/apisegment/segments")])
        self.assertEqual(mutation[0]["body"], {"segment": {
            "name": name, "expression": expression}})
        self.assertIn("Создано и проверено", result.stdout)

    def test_character_limits_are_independent_of_locale(self):
        self.env["LC_ALL"] = "C"
        result = self.run_script(
            "segments.sh", "--action", "create", "--name", "я" * 200,
            "--expression", "ym:s:isRobot=='No'")
        self.assertIn("Только проверка", result.stdout)
        self.assertEqual(self.mutations(), [])

    def test_segment_permissions_and_delete(self):
        self.env["FAKE_PERMISSION"] = "analyst"
        self.run_script("segments.sh", "--action", "create", "--name", "Allowed",
                        "--expression", "ym:s:isRobot=='No'")
        denied = self.run_script("segments.sh", "--action", "delete",
                                 "--segment-id", "42", success=False)
        self.assertIn("не позволяет удалять", denied.stderr)
        self.assertEqual(self.mutations(), [])

        self.env["FAKE_PERMISSION"] = "edit"
        self.log_path.unlink()
        preview = self.run_script("segments.sh", "--action", "delete",
                                  "--segment-id", "42")
        self.assertIn("Только проверка", preview.stdout)
        self.assertEqual(self.mutations(), [])

        self.log_path.unlink()
        self.run_script("segments.sh", "--action", "delete", "--segment-id", "42",
                        "--apply")
        self.assertEqual([item["method"] for item in self.mutations()], ["DELETE"])

    def test_segment_create_and_delete_are_repeatable(self):
        existing = self.run_script(
            "segments.sh", "--action", "create", "--name", "Existing",
            "--expression", "ym:s:isRobot=='No'", "--apply")
        self.assertIn("уже существует", existing.stdout)
        self.assertEqual(self.mutations(), [])

        conflict = self.run_script(
            "segments.sh", "--action", "create", "--name", "Existing",
            "--expression", "ym:s:isRobot=='Yes'", "--apply", success=False)
        self.assertIn("выражение отличается", conflict.stderr)
        self.assertEqual(self.mutations(), [])

        missing = self.run_script(
            "segments.sh", "--action", "delete", "--segment-id", "999",
            "--apply")
        self.assertIn("уже отсутствует", missing.stdout)
        self.assertEqual(self.mutations(), [])

    def test_interface_segment_is_not_changed(self):
        state = json.loads(self.state_path.read_text())
        state["segments"].append({
            "segment_id": 44, "counter_id": 12345, "name": "From interface",
            "expression": "ym:s:isRobot=='No'", "status": "active",
            "segment_source": "interface",
            "create_time": "2026-01-03T00:00:00Z",
        })
        self.state_path.write_text(json.dumps(state))
        result = self.run_script("segments.sh", "--action", "delete",
                                 "--segment-id", "44", "--apply", success=False)
        self.assertIn("только сегмент с источником api", result.stderr)
        self.assertEqual(self.mutations(), [])

    def test_post_write_verification_compares_actual_segment(self):
        self.env["FAKE_STALE_SEGMENT_NAME"] = "Changed elsewhere"
        result = self.run_script(
            "segments.sh", "--action", "create", "--name", "Expected",
            "--expression", "ym:s:isRobot=='No'", "--apply", success=False)
        self.assertIn("фактическое состояние", result.stderr)
        self.assertEqual(len(self.mutations()), 1)

    def test_list_and_exact_access_check(self):
        segments = self.run_script("segments.sh", "--action", "list")
        self.assertIn("Existing", segments.stdout)
        self.assertIn("Second segment", segments.stdout)
        listing = self.run_script("grants.sh", "--action", "list")
        self.assertIn("Ваша роль: edit", listing.stdout)
        self.assertIn("тип\tлогин\tроль", listing.stdout)
        self.assertIn("владелец\tcounter-owner\town", listing.stdout)
        self.assertIn("counter-owner", listing.stdout)
        self.assertIn("existing-client", listing.stdout)
        self.assertIn("second-client", listing.stdout)
        self.assertIn(
            "прямой\trestricted-client\tanalyst_access_filter\tfalse\t77",
            listing.stdout)
        self.assertIn(
            "публичный\t(публичный)\tpublic_stat\tfalse\t-",
            listing.stdout)
        found = self.run_script("grants.sh", "--action", "get",
                                "--login", "existing-client")
        self.assertIn("Источник: прямой доступ", found.stdout)
        owner = self.run_script("grants.sh", "--action", "get",
                                "--login", "counter-owner")
        self.assertIn("Источник: владелец счётчика", owner.stdout)
        missing = self.run_script("grants.sh", "--action", "get",
                                  "--login", "missing-client", success=False)
        self.assertEqual(missing.returncode, 3)
        self.assertIn("Прямой доступ: не найден", missing.stdout)

    def test_grant_preview_add_and_update_roles(self):
        login = "direct-client"
        comment = 'Direct "audience" \\ access'
        preview = self.run_script("grants.sh", "--action", "add", "--login", login,
                                  "--permission", "view", "--comment", comment)
        self.assertIn("Только проверка", preview.stdout)
        self.assertEqual(self.mutations(), [])

        self.log_path.unlink()
        self.run_script("grants.sh", "--action", "add", "--login", login,
                        "--permission", "view", "--comment", comment, "--apply")
        add = self.mutations()[0]
        self.assertEqual((add["method"], add["path"]),
                         ("POST", "/management/v1/counter/12345/grants"))
        self.assertEqual(add["body"]["grant"]["user_login"], login)
        self.assertEqual(add["body"]["grant"]["comment"], comment)
        self.assertIs(add["body"]["grant"]["partner_data_access"], False)

        self.log_path.unlink()
        update_preview = self.run_script(
            "grants.sh", "--action", "update", "--login", login,
            "--permission", "analyst")
        self.assertIn("Только проверка", update_preview.stdout)
        self.assertEqual(self.mutations(), [])

        self.log_path.unlink()
        self.run_script("grants.sh", "--action", "update", "--login", login,
                        "--permission", "analyst", "--apply")
        update = self.mutations()[0]
        self.assertEqual((update["method"], update["path"]),
                         ("PUT", "/management/v1/counter/12345/grant"))
        self.assertEqual(update["body"]["grant"]["comment"], comment)
        self.assertIs(update["body"]["grant"]["partner_data_access"], False)

    def test_add_defaults_to_view_but_other_roles_are_supported(self):
        preview = self.run_script(
            "grants.sh", "--action", "add", "--login", "valid-no-grant")
        self.assertIn("Новая роль: view", preview.stdout)
        analyst = self.run_script(
            "grants.sh", "--action", "add", "--login", "valid-no-grant",
            "--permission", "analyst")
        self.assertIn("Новая роль: analyst", analyst.stdout)
        editor = self.run_script(
            "grants.sh", "--action", "add", "--login", "valid-no-grant",
            "--permission", "edit")
        self.assertIn("Новая роль: edit", editor.stdout)
        self.assertEqual(self.mutations(), [])

    def test_existing_add_preserves_comment_and_rejects_changes(self):
        same = self.run_script(
            "grants.sh", "--action", "add", "--login", "existing-client",
            "--permission", "view")
        self.assertIn("уже есть запрошенная роль", same.stdout)
        self.assertIn("Отчёты «Монетизация»: false", same.stdout)

        broader = self.run_script(
            "grants.sh", "--action", "add", "--login", "second-client",
            "--permission", "analyst")
        self.assertIn("уже есть запрошенная роль", broader.stdout)
        self.assertIn("Отчёты «Монетизация»: true", broader.stdout)

        changed = self.run_script(
            "grants.sh", "--action", "add", "--login", "existing-client",
            "--permission", "view", "--comment", "changed", success=False)
        self.assertIn("используйте --action update", changed.stderr)

        restricted = self.run_script(
            "grants.sh", "--action", "add", "--login", "restricted-client",
            "--permission", "analyst", success=False)
        self.assertIn("доступ с фильтром данных", restricted.stderr)
        self.assertEqual(self.mutations(), [])

    def test_missing_grant_400_is_distinguished_from_unknown_login(self):
        preview = self.run_script(
            "grants.sh", "--action", "add", "--login", "valid-no-grant",
            "--permission", "view")
        self.assertIn("Только проверка", preview.stdout)
        unknown = self.run_script(
            "grants.sh", "--action", "get", "--login", "unknown-user",
            success=False)
        self.assertIn("doesn't exist", unknown.stderr)
        self.assertNotIn("Прямой доступ: не найден", unknown.stdout)
        ambiguous = self.run_script(
            "grants.sh", "--action", "get", "--login", "ambiguous-error",
            success=False)
        self.assertIn("не удалось проверить", ambiguous.stderr)
        self.assertNotIn("Прямой доступ: не найден", ambiguous.stdout)
        self.assertEqual(self.mutations(), [])

    def test_update_refuses_to_drop_access_filter(self):
        state = json.loads(self.state_path.read_text())
        state["grants"][0]["access_filters"] = [{"id": 77}]
        self.state_path.write_text(json.dumps(state))
        result = self.run_script(
            "grants.sh", "--action", "update", "--login", "existing-client",
            "--permission", "analyst", "--apply", success=False)
        self.assertIn("изменение доступа с фильтром", result.stderr)
        self.assertEqual(self.mutations(), [])

    def test_config_file_must_be_private(self):
        config = self.root / "config" / ".env"
        config.chmod(0o644)
        result = self.run_script("grants.sh", "--action", "list", success=False)
        self.assertIn("доступен группе или другим пользователям", result.stderr)
        self.assertEqual(self.requests(), [])

    def test_post_write_verification_compares_actual_grant(self):
        self.env["FAKE_STALE_PERMISSION"] = "analyst"
        result = self.run_script(
            "grants.sh", "--action", "add", "--login", "direct-client",
            "--permission", "view", "--apply", success=False)
        self.assertIn("фактические настройки", result.stderr)
        self.assertEqual(len(self.mutations()), 1)

    def test_grants_require_edit_and_mutations_are_not_retried(self):
        self.env["FAKE_PERMISSION"] = "analyst"
        denied = self.run_script("grants.sh", "--action", "add",
                                 "--login", "direct-client", "--permission", "view",
                                 "--apply", success=False)
        self.assertIn("не позволяет выдавать", denied.stderr)
        self.assertEqual(self.mutations(), [])

        self.env["FAKE_PERMISSION"] = "edit"
        self.env["FAKE_MUTATION_STATUS"] = "429"
        self.log_path.unlink()
        failure = self.run_script("grants.sh", "--action", "add",
                                  "--login", "direct-client", "--permission", "view",
                                  "--apply", success=False)
        self.assertIn("HTTP 429", failure.stderr)
        self.assertEqual(len(self.mutations()), 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fake-curl":
        raise SystemExit(fake_curl(sys.argv[2:]))
    unittest.main()
