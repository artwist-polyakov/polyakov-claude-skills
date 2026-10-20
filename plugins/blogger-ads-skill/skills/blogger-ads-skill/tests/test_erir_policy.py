"""Информационная видимость ЕРИР не смешивается с обязательной проверкой ОРД."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from markdown_it import MarkdownIt

import test_deal as fixtures

deal = fixtures.deal
ROOT = fixtures.ROOT


class ErirPolicyTests(unittest.TestCase):
    setUp = fixtures.DealTests.setUp
    confirm_readiness = fixtures.DealTests.confirm_readiness

    def erir_action(self):
        self.state["schedule"]["reporting_deadlines"].append({
            "id": "erir-check", "title": "Проверить видимость ЕРИР", "at": self.now.isoformat(),
            "source": "Дополнительное согласованное условие", "kind": "organizational", "checks": ["erir"]})

    def test_pending_optional_erir_does_not_block_an_otherwise_ready_deal(self):
        self.confirm_readiness()
        self.state["policy"]["require_erir_visibility"] = False
        self.state["checks"]["erir"] = {"status": "pending"}
        before = copy.deepcopy(self.state)
        result = deal.check(self.state, self.now)
        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], {})
        self.assertEqual(result["missing"], [])
        self.assertFalse(result["readiness_policy"]["erir_visibility"]["required"])
        self.assertEqual(result["readiness_policy"]["erir_visibility"]["mode"], "informational")
        self.assertEqual(self.state, before)

    def test_optional_erir_does_not_waive_token_or_any_part_of_registered_material(self):
        self.confirm_readiness()
        self.state["policy"]["require_erir_visibility"] = False
        baseline = copy.deepcopy(self.state)
        for key in ("text", "links", "media", "issued_token"):
            with self.subTest(missing=key):
                self.state = copy.deepcopy(baseline)
                if key == "issued_token":
                    self.state["checks"]["erid"]["status"] = "pending"
                else:
                    self.state["checks"]["ord_creative"]["verified"][key] = False
                result = deal.check(self.state, self.now)
                self.assertFalse(result["ready"])
                self.assertIn("erid" if key == "issued_token" else "ord_creative", result["blockers"])
                self.assertNotIn("erir", result["blockers"])
                self.assertTrue(result["readiness_policy"]["ord"]["required"])
                self.assertEqual(result["readiness_policy"]["ord"]["content"], ["text", "links", "media"])

    def test_required_erir_is_an_additional_condition_and_needs_matching_token(self):
        self.confirm_readiness()
        self.state["policy"]["require_erir_visibility"] = True
        result = deal.check(self.state, self.now)
        self.assertFalse(result["ready"])
        self.assertIn("erir", result["blockers"])
        policy = result["readiness_policy"]["erir_visibility"]
        self.assertTrue(policy["required"])
        self.assertEqual(policy["mode"], "additional_agreed_requirement")
        self.state["checks"]["erir"] = {"status": "confirmed", "source": "Снимок результата ЕРИР",
                                           "checked_at": self.now.isoformat(), "token": "different-token"}
        self.assertFalse(deal.check(self.state, self.now)["ready"])
        self.state["checks"]["erir"]["token"] = self.state["checks"]["erid"]["token"]
        self.assertTrue(deal.check(self.state, self.now)["ready"])

    def test_cli_plan_json_and_markdown_explain_both_policy_modes(self):
        self.confirm_readiness()
        self.erir_action()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            for required in (False, True):
                with self.subTest(required=required):
                    self.state["policy"]["require_erir_visibility"] = required
                    state_path.write_text(json.dumps(self.state))
                    folder = root / str(required)
                    result = subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), "plan", "--state", str(state_path),
                                             "--now", self.now.isoformat(), "--output-dir", str(folder)],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    plan = json.loads((folder / "plan.json").read_text())
                    policy = plan["readiness_policy"]
                    self.assertEqual(policy, deal.check(self.state, self.now)["readiness_policy"])
                    self.assertEqual(policy["erir_visibility"]["required"], required)
                    markdown = (folder / "plan.md").read_text()
                    visible_text = "\n".join(
                        "".join(child.content for child in token.children or [] if child.type == "text")
                        for token in MarkdownIt().parse(markdown) if token.type == "inline"
                    )
                    self.assertIn(policy["ord"]["note"], visible_text)
                    self.assertIn(policy["erir_visibility"]["note"], visible_text)
                    self.assertIs(deal.validate_plan(self.state, plan, state_path), plan)

    def test_saved_plan_rejects_missing_tampered_or_stale_policy(self):
        self.confirm_readiness()
        self.erir_action()
        self.state["policy"]["require_erir_visibility"] = False
        plan = deal.plan(self.state, self.now)
        before = copy.deepcopy(plan)
        self.assertIs(deal.validate_plan(self.state, plan), plan)
        self.assertEqual(plan, before)
        for bad in (None, {"erir_visibility": {"required": True}}):
            with self.subTest(policy=bad):
                edited = copy.deepcopy(plan)
                if bad is None:
                    del edited["readiness_policy"]
                else:
                    edited["readiness_policy"] = bad
                with self.assertRaisesRegex(ValueError, "readiness_policy"):
                    deal.validate_plan(self.state, edited)
        self.state["policy"]["require_erir_visibility"] = True
        with self.assertRaisesRegex(ValueError, "readiness_policy"):
            deal.validate_plan(self.state, plan)


if __name__ == "__main__":
    unittest.main()
