import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from test_deal import ROOT, deal, planned_actions
from test_accounting import accounting, fixture as accounting_fixture


class SceneClockTests(unittest.TestCase):
    def setUp(self):
        self.state = deal.read_json(ROOT / "assets/deal.example.json")
        self.state["schedule"].update(publication_at="2026-11-09T09:00:00+03:00",
                                      closing_after_end_hours=48, link_after_publication_hours=1,
                                      reporting_deadlines=planned_actions())
        self.at = "2026-11-01T12:00:00+03:00"
        self.source = "Первое сообщение: «Сейчас в сцене 1 ноября 2026 года, 12:00 МСК»"
        self.state["clock"] = {"mode": "scenario", "entries": [{"at": self.at, "source": self.source}]}

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), *map(str, args)],
                              capture_output=True, text=True)

    def save(self, directory):
        path = Path(directory) / "state.json"
        path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        return path

    def test_init_pins_inline_source_and_rejects_incomplete_clock_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = self.cli("init", "--output", path, "--scene-at", self.at, "--scene-source", self.source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(deal.read_json(path)["clock"], self.state["clock"])
            for index, flags in enumerate((("--scene-at", self.at), ("--scene-source", self.source),
                                           ("--scene-at", self.at, "--scene-source", " "))):
                with self.subTest(flags=flags):
                    rejected = Path(directory) / f"bad-{index}.json"
                    self.assertEqual(self.cli("init", "--output", rejected, *flags).returncode, 1)
                    self.assertFalse(rejected.exists())

    def test_plan_without_now_uses_saved_scene_and_accepts_equivalent_offset(self):
        first = deal.plan(self.state)
        equivalent = deal.plan(self.state, deal.stamp("2026-11-01T09:00:00Z"))
        self.assertEqual(first, equivalent)
        self.assertEqual(first["generated_at"], self.at)
        with tempfile.TemporaryDirectory() as directory:
            path = self.save(directory)
            plans = []
            for name in ("first", "second"):
                output = Path(directory) / name
                result = self.cli("plan", "--state", path, "--output-dir", output)
                self.assertEqual(result.returncode, 0, result.stderr)
                plans.append(deal.read_json(output / "plan.json"))
            self.assertEqual(plans[0], plans[1])
            self.assertEqual(plans[0], first)

    def test_mismatched_now_and_saved_generated_at_are_rejected(self):
        other = deal.stamp("2026-11-02T12:00:00+03:00")
        for command in (deal.plan, deal.check):
            with self.subTest(command=command.__name__), self.assertRaisesRegex(ValueError, "временем сцены"):
                command(self.state, other)
        plan = deal.plan(self.state)
        plan["generated_at"] = other.isoformat()
        with self.assertRaisesRegex(ValueError, "временем сцены"):
            deal.validate_plan(self.state, plan)
        with tempfile.TemporaryDirectory() as directory:
            path = self.save(directory)
            output = Path(directory) / "plan"
            result = self.cli("plan", "--state", path, "--output-dir", output, "--now", other.isoformat())
            self.assertEqual(result.returncode, 1)
            self.assertFalse(output.exists())

    def test_explicit_update_preserves_old_scene_history_and_same_instant_is_noop(self):
        self.state["history"] = [{"action": "facts_update", "scene_at": "2026-11-01T09:00:00Z"}]
        with tempfile.TemporaryDirectory() as directory:
            path = self.save(directory)
            later = "2026-11-03T18:20:00+03:00"
            source = "Новое сообщение: «Сейчас 3 ноября 2026 года, 18:20 МСК»"
            result = self.cli("set-scene-time", "--state", path, "--at", later, "--source", source)
            self.assertEqual(result.returncode, 0, result.stderr)
            updated = deal.read_json(path)
            self.assertEqual(updated["clock"]["entries"], self.state["clock"]["entries"] + [{"at": later, "source": source}])
            self.assertEqual(updated["history"], self.state["history"])
            self.assertEqual(deal.plan(updated)["generated_at"], later)
            before = path.read_bytes()
            self.assertFalse(deal.set_scene_time(path, "2026-11-03T15:20:00Z", source)["changed"])
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(ValueError):
                deal.set_scene_time(path, "2026-11-04T12:00:00+03:00", "")
            self.assertEqual(path.read_bytes(), before)

    def test_arbitrary_history_scene_time_is_rejected_but_observation_time_is_independent(self):
        observed = "2026-11-15T14:20:00+03:00"
        self.state["history"] = [{"action": "legal_source_review", "scene_at": self.at,
                                  "observed_at": observed, "recorded_at": observed}]
        original = copy.deepcopy(self.state)
        self.assertEqual(deal.plan(self.state)["generated_at"], self.at)
        self.assertEqual(self.state, original)
        self.state["history"][0]["scene_at"] = observed
        with self.assertRaisesRegex(ValueError, r"history\[0\].scene_at"):
            deal.check(self.state)

    def test_mutating_commands_inherit_clock_and_reject_override_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.save(directory)
            text = Path(directory) / "creative.txt"
            text.write_text("Реклама. Учебный рекламодатель.", encoding="utf-8")
            links = Path(directory) / "links.json"
            links.write_text("[]", encoding="utf-8")
            approval = Path(directory) / "approval.txt"
            approval.write_text("Текст согласован.", encoding="utf-8")
            commands = (("import-creative", "--text", text, "--links", links),
                        ("approve-creative", "--source", approval, "--quote", "Текст согласован."))
            for command in commands:
                with self.subTest(command=command[0]):
                    before = {p.relative_to(directory): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
                    rejected = self.cli(command[0], "--state", path, *command[1:], "--now", "2026-11-02T12:00:00+03:00")
                    self.assertEqual(rejected.returncode, 1, rejected.stderr)
                    self.assertIn("временем сцены", rejected.stderr)
                    self.assertEqual(before, {p.relative_to(directory): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()})
                    accepted = self.cli(command[0], "--state", path, *command[1:])
                    self.assertEqual(accepted.returncode, 0, accepted.stderr)
            updated = deal.read_json(path)
            self.assertEqual(updated["checks"]["creative_approval"]["checked_at"], self.at)
            self.assertTrue(all(row["at"] == self.at for row in updated["history"] if row.get("action") in ("import_creative", "approve_creative")))

    def test_legacy_live_state_retains_machine_or_explicit_time(self):
        del self.state["clock"]
        self.state["history"] = [{"scene_at": "Legacy свободное поле"}]
        explicit = deal.stamp(self.at)
        self.assertEqual(deal.plan(self.state, explicit)["generated_at"], self.at)
        before = datetime.now(timezone.utc)
        actual = deal.stamp(deal.plan(self.state)["generated_at"])
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, actual)
        self.assertLessEqual(actual, after)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            result = self.cli("init", "--output", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("clock", deal.read_json(path))

    def test_xml_generation_time_is_not_taken_from_scene_clock(self):
        data = accounting_fixture()
        data["clock"] = copy.deepcopy(self.state["clock"])
        data["document"]["generated_at"] = "2026-11-15T14:20:30+03:00"
        filename, xml = accounting.render_xml(data)
        document = ET.fromstring(xml).find("Документ")
        self.assertEqual(document.get("ДатаИнфПр"), "15.11.2026")
        self.assertEqual(document.get("ВремИнфПр"), "14.20.30")
        self.assertIn("_20261115_", filename)
        self.assertEqual(deal.plan(self.state)["generated_at"], self.at)


if __name__ == "__main__":
    unittest.main()
