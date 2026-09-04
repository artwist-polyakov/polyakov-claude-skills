#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Exercise source-map validation and source-index generation through the CLI."""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


QUALITY_GATE = Path(__file__).resolve().parents[1] / "quality_gate.py"


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "source.txt"
        self.source.write_text(
            "Исходный текст содержит подробные объяснения и примеры автора.\n"
            "Эта уникальная строка источника не должна попадать в указатель.\n",
            encoding="utf-8",
        )
        self.skill = root / "sample-skill"
        self.references = self.skill / "references"
        self.references.mkdir(parents=True)
        self.index = self.references / "source-index.md"
        self.concepts = self.references / "concepts.md"
        self.concept_text = (
            "# Concepts\n\n## diagnosis-before-action\n\n"
            "Сначала проверь ситуацию. Источник: seg-001, seg-002.\n"
        )
        self.concepts.write_text(self.concept_text, encoding="utf-8")
        (self.skill / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: Личная карта знаний.\n---\n\n"
            "# Sample Skill\n\nПрименяй правила к задаче пользователя.\n",
            encoding="utf-8",
        )
        (self.references / "decision-rules.md").write_text(
            "# Decision Rules\n\n## choose-after-intent\n\n"
            "Уточни намерение перед выбором действия. Источник: seg-001.\n",
            encoding="utf-8",
        )
        for name in ("playbooks", "anti-patterns", "glossary"):
            (self.references / f"{name}.md").write_text(
                f"# {name}\n\nПрикладные заметки по материалу.\n", encoding="utf-8"
            )
        (self.references / "knowledge-manifest.json").write_text(
            json.dumps({"title": "Sample", "source_sha256": "sha256:test"}),
            encoding="utf-8",
        )
        self.source_map = {
            "source_id": "sha256:test",
            "segments": [
                {
                    "segment_id": f"seg-{number:03d}",
                    "title": f"Глава {number}",
                    "char_start": (number - 1) * 60,
                    "char_end": number * 60,
                    "line_start": number,
                    "line_end": number,
                    "confidence": 0.8,
                }
                for number in (1, 2)
            ],
            "claims": [
                {
                    "claim_id": claim_id,
                    "artifact": artifact,
                    "source_segments": segments,
                    "extraction_type": "synthesized",
                    "verbatim_quote_words": 0,
                    "confidence": 0.9,
                }
                for claim_id, artifact, segments in (
                    ("diagnosis-before-action", "references/concepts.md", ["seg-001", "seg-002"]),
                    ("choose-after-intent", "references/decision-rules.md", ["seg-001"]),
                )
            ],
        }
        self.write_map(self.source_map)

    def write_map(self, data):
        (self.references / "source-map.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def run_gate(self, *flags, success=True, diagnostic=None):
        result = subprocess.run(
            [os.environ.get("UV", "uv"), "run", "--script", str(QUALITY_GATE),
             "--source", str(self.source), "--skill-dir", str(self.skill), *flags],
            text=True, capture_output=True, check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0 if success else 1, output)
        if diagnostic:
            self.assertIn(diagnostic, output)
        return output

    def snapshot(self):
        return {
            path.relative_to(self.skill): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.skill.rglob("*") if path.is_file()
        }

    def assert_rejected_without_index_write(self, diagnostic):
        self.index.unlink(missing_ok=True)
        self.run_gate("--write-source-index", success=False, diagnostic=diagnostic)
        self.assertFalse(self.index.exists())
        self.index.write_text("Сохранённый указатель.\n", encoding="utf-8")
        before = self.snapshot()
        self.run_gate("--write-source-index", success=False, diagnostic=diagnostic)
        self.assertEqual(self.snapshot(), before)

    def test_generate_stable_index_and_read_only_check(self):
        self.run_gate("--write-source-index")
        generated = self.index.read_bytes()
        text = generated.decode("utf-8")
        for expected in (
            "seg-001", "seg-002", "Глава 1", "Глава 2", "0.8",
            "concepts.md#diagnosis-before-action", "decision-rules.md#choose-after-intent",
        ):
            self.assertIn(expected, text)
        first, second = text.split("## seg-001", 1)[1].split("## seg-002", 1)
        for expected in ("1–1", "[0, 60)", "#diagnosis-before-action", "#choose-after-intent"):
            self.assertIn(expected, first)
        for expected in ("2–2", "[60, 120)", "#diagnosis-before-action"):
            self.assertIn(expected, second)
        self.assertNotIn("#choose-after-intent", second)
        self.assertNotIn("references/concepts.md#", text)
        self.assertNotIn(self.source.read_text(encoding="utf-8").splitlines()[1], text)
        self.assertNotIn(str(self.source), text)
        self.run_gate("--write-source-index")
        self.assertEqual(self.index.read_bytes(), generated)
        before = self.snapshot()
        self.run_gate()
        self.assertEqual(self.snapshot(), before)

    def test_missing_and_stale_index_require_explicit_write(self):
        before = self.snapshot()
        self.run_gate(success=False, diagnostic="source-index.md")
        self.assertEqual(self.snapshot(), before)
        self.index.write_text("Устаревший указатель: seg-999.\n", encoding="utf-8")
        self.run_gate("--write-source-index")
        original = self.index.read_bytes()
        self.source_map["segments"][0]["title"] = "Изменённая глава"
        self.write_map(self.source_map)
        before = self.snapshot()
        self.run_gate(success=False, diagnostic="source-index.md")
        self.assertEqual(self.snapshot(), before)
        self.run_gate("--write-source-index")
        self.assertNotEqual(self.index.read_bytes(), original)
        self.assertIn("Изменённая глава", self.index.read_text(encoding="utf-8"))
        self.run_gate()

    def test_invalid_source_map(self):
        mutations = (
            ("empty segments", lambda data: data.update(segments=[])),
            ("empty claims", lambda data: data.update(claims=[])),
            ("segments not a list", lambda data: data.update(segments={})),
            ("invalid segment id", lambda data: data["segments"][0].update(segment_id="chapter-1")),
            ("negative character start", lambda data: data["segments"][0].update(char_start=-1)),
            ("empty character range", lambda data: data["segments"][0].update(char_end=0)),
            ("invalid line start", lambda data: data["segments"][0].update(line_start=0)),
            ("inverted line range", lambda data: data["segments"][1].update(line_end=1)),
            ("invalid confidence", lambda data: data["segments"][0].update(confidence=1.1)),
            ("invalid claim id", lambda data: data["claims"][0].update(claim_id="Invalid ID")),
            ("empty source segments", lambda data: data["claims"][0].update(source_segments=[])),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                data = copy.deepcopy(self.source_map)
                mutate(data)
                self.write_map(data)
                self.assert_rejected_without_index_write("source-map")

    def test_duplicate_ids_and_unknown_claim_segment(self):
        cases = (
            ("segments", "duplicate segment_id"),
            ("claims", "duplicate claim_id"),
        )
        for collection, diagnostic in cases:
            with self.subTest(collection=collection):
                data = copy.deepcopy(self.source_map)
                data[collection].append(copy.deepcopy(data[collection][0]))
                self.write_map(data)
                self.assert_rejected_without_index_write(diagnostic)
        data = copy.deepcopy(self.source_map)
        data["claims"][0]["source_segments"] = ["seg-999"]
        self.write_map(data)
        self.assert_rejected_without_index_write("unknown segment")

    def test_missing_artifact(self):
        self.source_map["claims"][0]["artifact"] = "references/absent.md"
        self.write_map(self.source_map)
        self.assert_rejected_without_index_write("missing artifact")

    def test_artifact_must_be_reference_markdown(self):
        for artifact in ("../source.txt", "SKILL.md", "references/source-map.json", "references/source-index.md"):
            with self.subTest(artifact=artifact):
                data = copy.deepcopy(self.source_map)
                data["claims"][0]["artifact"] = artifact
                self.write_map(data)
                self.assert_rejected_without_index_write("artifact")

    def test_missing_duplicate_and_hidden_claim_heading(self):
        for text in (
            "# Concepts\n\n## another-id\n",
            self.concept_text + "\n## diagnosis-before-action\n",
            "# Concepts\n\n```markdown\n## diagnosis-before-action\n```\n",
            "# Concepts\n\n<!--\n## diagnosis-before-action\n-->\n",
            "# Diagnosis Before Action\n\n" + self.concept_text,
        ):
            with self.subTest(text=text):
                self.concepts.write_text(text, encoding="utf-8")
                self.assert_rejected_without_index_write("claim heading")

    def test_unknown_markdown_segment(self):
        self.concepts.write_text(self.concept_text + "\nИсточник: seg-999.\n", encoding="utf-8")
        self.assert_rejected_without_index_write("unknown segment")

    def test_fenced_examples_do_not_create_references_or_headings(self):
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                self.concepts.write_text(
                    self.concept_text + f"\n{fence}markdown\n## diagnosis-before-action\n"
                    f"Источник: seg-999.\n{fence}\n",
                    encoding="utf-8",
                )
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_html_comments_do_not_create_references_or_headings(self):
        self.concepts.write_text(
            self.concept_text + "\n<!--\n## diagnosis-before-action\nИсточник: seg-999.\n-->\n",
            encoding="utf-8",
        )
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_comment_start_inside_fenced_code_does_not_hide_heading(self):
        self.concepts.write_text(
            "```html\n<!--\n```\n\n" + self.concept_text, encoding="utf-8"
        )
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_indented_code_ignores_segment_examples(self):
        for indent in ("    ", "\t"):
            with self.subTest(indent=repr(indent)):
                self.concepts.write_text(
                    self.concept_text + f"\n{indent}Источник: seg-999.\n", encoding="utf-8"
                )
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_real_reference_after_indented_code_is_checked(self):
        self.concepts.write_text(
            self.concept_text + "\n    Источник: seg-001.\n\nИсточник: seg-999.\n",
            encoding="utf-8",
        )
        self.assert_rejected_without_index_write("unknown segment")

    def test_indentation_does_not_interrupt_paragraph(self):
        for indent in ("    ", "\t"):
            with self.subTest(indent=repr(indent)):
                self.concepts.write_text(
                    self.concept_text + f"{indent}Продолжение абзаца: seg-999.\n",
                    encoding="utf-8",
                )
                self.assert_rejected_without_index_write("unknown segment")

    def test_list_indentation_distinguishes_text_from_code(self):
        for item in ("- Пункт\n", "- Пункт\nПродолжение без отступа\n"):
            for width, is_code in ((4, False), (6, True)):
                with self.subTest(item=item, width=width):
                    self.concepts.write_text(
                        self.concept_text + "\n" + item + "\n" + " " * width + "seg-999\n",
                        encoding="utf-8",
                    )
                    if is_code:
                        self.run_gate("--write-source-index")
                        self.run_gate()
                    else:
                        self.assert_rejected_without_index_write("unknown segment")

    def test_setext_heading_conflicts_with_claim_anchor(self):
        headings = [
            f"{indent}diagnosis-before-action\n{indent}{underline}\n"
            for underline in ("---", "===") for indent in ("", " ", "  ", "   ")
        ]
        headings += [f"Diagnosis-\nBefore Action\n{underline}\n" for underline in ("---", "===")]
        for heading in headings:
            with self.subTest(heading=heading):
                self.concepts.write_text(heading + "\n" + self.concept_text, encoding="utf-8")
                self.assert_rejected_without_index_write("claim heading")

    def test_multiline_setext_anchor_does_not_add_hyphen_for_newline(self):
        for underline in ("---", "==="):
            with self.subTest(underline=underline):
                self.concepts.write_text(
                    f"Diagnosis\nBefore Action\n{underline}\n\n" + self.concept_text,
                    encoding="utf-8",
                )
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_setext_heading_does_not_replace_canonical_claim_heading(self):
        for underline in ("---", "==="):
            with self.subTest(underline=underline):
                self.concepts.write_text(
                    f"# Concepts\n\ndiagnosis-before-action\n{underline}\n", encoding="utf-8"
                )
                self.assert_rejected_without_index_write("claim heading")

    def test_thematic_break_after_blank_line_does_not_create_heading(self):
        self.concepts.write_text(
            "diagnosis-before-action\n\n---\n\n" + self.concept_text, encoding="utf-8"
        )
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_setext_examples_inside_code_and_comments_are_ignored(self):
        for example in (
            "```markdown\ndiagnosis-before-action\n---\n```\n",
            "    diagnosis-before-action\n    ---\n",
            "<!--\ndiagnosis-before-action\n---\n-->\n",
        ):
            with self.subTest(example=example):
                self.concepts.write_text(example + "\n" + self.concept_text, encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_code_inside_quotes_and_nested_lists_is_ignored(self):
        for example in (
            ">     seg-999\n",
            "> ```markdown\n> seg-999\n> ```\n",
            "> - Пункт\n>\n>       seg-999\n",
            "- Пункт\n\n  > ```markdown\n  > seg-999\n  > ```\n",
        ):
            with self.subTest(example=example):
                self.concepts.write_text(self.concept_text + "\n" + example, encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_quoted_references_are_still_checked(self):
        for reference in ("> Источник: seg-999.\n", "> Источник: `seg-999`.\n"):
            with self.subTest(reference=reference):
                self.concepts.write_text(self.concept_text + "\n" + reference, encoding="utf-8")
                self.assert_rejected_without_index_write("unknown segment")

    def test_quoted_setext_heading_conflicts_with_claim_anchor(self):
        self.concepts.write_text(
            "> Diagnosis Before Action\n> ---\n\n" + self.concept_text, encoding="utf-8"
        )
        self.assert_rejected_without_index_write("claim heading")

    def test_reference_link_heading_conflicts_with_claim_anchor(self):
        for heading, definition in (
            ("[Diagnosis Before Action][details]", "[details]: /details"),
            ("[Diagnosis Before Action][]", "[Diagnosis Before Action]: /details"),
            ("[Diagnosis Before Action]", "[Diagnosis Before Action]: /details"),
        ):
            with self.subTest(heading=heading):
                self.concepts.write_text(
                    f"{heading}\n---\n\n" + self.concept_text + f"\n{definition}\n",
                    encoding="utf-8",
                )
                self.assert_rejected_without_index_write("claim heading")

    def test_unresolved_reference_link_keeps_visible_label_in_anchor(self):
        self.concepts.write_text(
            "[Diagnosis Before Action][details]\n---\n\n" + self.concept_text,
            encoding="utf-8",
        )
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_duplicate_heading_suffix_conflicts_with_claim_anchor(self):
        self.source_map["claims"][0]["claim_id"] = "diagnosis-1"
        self.write_map(self.source_map)
        self.concepts.write_text(
            "# Diagnosis\n\n# Diagnosis\n\n## diagnosis-1\n\nИсточник: seg-001.\n",
            encoding="utf-8",
        )
        self.assert_rejected_without_index_write("claim heading")

    def test_literal_underscores_remain_in_heading_anchor(self):
        for heading in (r"# diagnosis\_-before-action", "# `diagnosis_-before-action`"):
            with self.subTest(heading=heading):
                self.concepts.write_text(heading + "\n\n" + self.concept_text, encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_emphasized_heading_conflicts_with_claim_anchor(self):
        self.concepts.write_text(
            "# _Diagnosis Before Action_\n\n" + self.concept_text, encoding="utf-8"
        )
        self.assert_rejected_without_index_write("claim heading")

    def test_combining_mark_remains_in_heading_anchor(self):
        self.source_map["claims"][0]["claim_id"] = "cafe"
        self.write_map(self.source_map)
        self.concepts.write_text(
            "# cafe\u0301\n\n## cafe\n\nИсточник: seg-001.\n", encoding="utf-8"
        )
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_other_number_is_removed_from_heading_anchor(self):
        self.concepts.write_text(
            "# diagnosis²-before-action\n\n" + self.concept_text, encoding="utf-8"
        )
        self.assert_rejected_without_index_write("claim heading")

    def test_reference_link_destination_segment_is_checked(self):
        self.concepts.write_text(
            self.concept_text + "\n[Источник][details]\n\n"
            "[details]: source-index.md#seg-999\n",
            encoding="utf-8",
        )
        self.assert_rejected_without_index_write("unknown segment")

    def test_inline_html_comment_does_not_create_segment_reference(self):
        self.concepts.write_text(
            self.concept_text + "\nЭто пример <!-- seg-999 --> без ссылки.\n",
            encoding="utf-8",
        )
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_inline_html_heading_conflicts_by_visible_text(self):
        self.concepts.write_text(
            "# <em>Diagnosis</em> Before Action\n\n" + self.concept_text, encoding="utf-8"
        )
        self.assert_rejected_without_index_write("claim heading")

    def test_html_text_boundaries_preserve_segment_references(self):
        for example in (
            "seg-001<br>seg-999",
            "<p>seg-001</p><p>seg-999</p>",
            "seg-<em>999</em>",
        ):
            with self.subTest(example=example):
                self.concepts.write_text(
                    self.concept_text + "\n" + example + "\n", encoding="utf-8"
                )
                self.assert_rejected_without_index_write("unknown segment")

    def test_existing_quality_errors_prevent_index_write(self):
        with (self.skill / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("\nTODO\n")
        self.assert_rejected_without_index_write("placeholder")


if __name__ == "__main__":
    unittest.main()
