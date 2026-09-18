#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Exercise source-map validation and source-index generation through the CLI."""

import copy
import hashlib
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
        source_text = (
            "Исходный текст содержит подробные объяснения и примеры автора.\n"
            "Эта уникальная строка источника не должна попадать в указатель.\n"
        )
        self.source.write_text(source_text, encoding="utf-8")
        first_line_end = source_text.index("\n") + 1
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
        source_id = "sha256:" + "a" * 64
        (self.references / "knowledge-manifest.json").write_text(
            json.dumps({"title": "Sample", "source_sha256": source_id}),
            encoding="utf-8",
        )
        self.source_map = {
            "source_id": source_id,
            "segments": [
                {
                    "segment_id": f"seg-{number:03d}",
                    "title": f"Глава {number}",
                    "char_start": 0 if number == 1 else first_line_end,
                    "char_end": first_line_end if number == 1 else len(source_text),
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
        self.assertNotIn("Traceback", output)
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
        first_end = self.source_map["segments"][0]["char_end"]
        source_end = self.source_map["segments"][1]["char_end"]
        for expected in ("1–1", f"[0, {first_end})", "#diagnosis-before-action", "#choose-after-intent"):
            self.assertIn(expected, first)
        for expected in ("2–2", f"[{first_end}, {source_end})", "#diagnosis-before-action"):
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

    def test_unencodable_segment_title_fails_without_index_write(self):
        data = copy.deepcopy(self.source_map)
        data["segments"][0]["title"] = "bad\ud800"
        (self.references / "source-map.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        self.assert_rejected_without_index_write("title must be valid UTF-8 text")

    def test_claim_provenance_backlinks_follow_map_not_inline_mentions(self):
        for sources, text in (
            (["seg-001", "seg-002"], "Сначала проверь ситуацию."),
            (["seg-001", "seg-002"], "Сначала проверь ситуацию. Источник: seg-001."),
            (["seg-001"], "Источник: [seg-001](source-index.md#seg-001).\n\n"
             "Для сравнения см. [seg-002](source-index.md#seg-002)."),
        ):
            with self.subTest(sources=sources, text=text):
                self.source_map["claims"][0]["source_segments"] = sources
                self.write_map(self.source_map)
                self.concepts.write_text(
                    "# Concepts\n\n## diagnosis-before-action\n\n" + text + "\n", encoding="utf-8"
                )
                self.run_gate("--write-source-index")
                index = self.index.read_text(encoding="utf-8")
                first, second = index.split("## seg-001", 1)[1].split("## seg-002", 1)
                for segment_id, section in (("seg-001", first), ("seg-002", second)):
                    self.assertEqual(
                        section.count("(concepts.md#diagnosis-before-action)"), int(segment_id in sources)
                    )
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_invalid_utf8_markdown_fails_without_index_write(self):
        for path, diagnostic in (
            (self.concepts, "cannot read Markdown"),
            (self.skill / "SKILL.md", "cannot read Markdown SKILL.md"),
        ):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(b"\xff\n")
                    self.assert_rejected_without_index_write(diagnostic)
                finally:
                    path.write_bytes(original)

    def test_unreadable_generated_files_fail_without_index_write(self):
        extra = self.references / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        self.run_gate("--write-source-index")
        for path, diagnostic in (
            (self.concepts, "cannot read Markdown"),
            (self.skill / "SKILL.md", "cannot read SKILL.md"),
            (extra, "cannot read generated skill"),
        ):
            with self.subTest(path=path.name):
                before = self.snapshot()
                mode = path.stat().st_mode & 0o777
                try:
                    path.chmod(0)
                    try:
                        path.read_bytes()
                    except PermissionError:
                        self.run_gate("--write-source-index", success=False, diagnostic=diagnostic)
                    else:
                        self.skipTest("File read permissions are not enforced for this user")
                finally:
                    path.chmod(mode)
                    self.assertEqual(self.snapshot(), before)

    def test_unreadable_existing_index_fails_read_only_and_preserves_file(self):
        self.run_gate("--write-source-index")
        original = self.index.read_bytes()
        for failure in ("invalid_utf8", "permission"):
            with self.subTest(failure=failure):
                self.index.write_bytes(b"\xff\n" if failure == "invalid_utf8" else original)
                before = self.snapshot()
                mode = self.index.stat().st_mode & 0o777
                try:
                    if failure == "permission":
                        self.index.chmod(0)
                        try:
                            self.index.read_bytes()
                        except PermissionError:
                            pass
                        else:
                            self.skipTest("File read permissions are not enforced for this user")
                    self.run_gate(success=False, diagnostic="cannot read source-index.md")
                finally:
                    self.index.chmod(mode)
                    self.assertEqual(self.snapshot(), before)

    def test_cyclic_references_directory_fails_without_mutations(self):
        self.run_gate("--write-source-index")
        before = self.snapshot()
        backup = self.skill / "references-backup"
        self.references.rename(backup)
        try:
            self.references.symlink_to(self.references.name)
            for flags in ((), ("--write-source-index",)):
                with self.subTest(flags=flags):
                    self.run_gate(*flags, success=False, diagnostic="cannot inspect source-index.md")
        finally:
            self.references.unlink(missing_ok=True)
            backup.rename(self.references)
            self.assertEqual(self.snapshot(), before)

    def test_source_identity_fields_are_required(self):
        for filename, field in (
            ("source-map.json", "source_id"),
            ("knowledge-manifest.json", "source_sha256"),
        ):
            with self.subTest(field=field):
                path = self.references / filename
                original = path.read_text(encoding="utf-8")
                data = json.loads(original)
                del data[field]
                path.write_text(json.dumps(data), encoding="utf-8")
                try:
                    self.assert_rejected_without_index_write(field)
                finally:
                    path.write_text(original, encoding="utf-8")

    def test_source_id_requires_lowercase_sha256_format(self):
        for value in (
            None, True, 1, [], {}, "", "sha256:test", "a" * 64,
            "sha256:" + "a" * 63, "sha256:" + "a" * 65,
            "sha256:" + "A" * 64, "sha256:" + "g" * 64, "sha256:" + "a" * 64 + "\n",
        ):
            with self.subTest(value=value):
                data = copy.deepcopy(self.source_map)
                data["source_id"] = value
                self.write_map(data)
                (self.references / "knowledge-manifest.json").write_text(
                    json.dumps({"source_sha256": value}), encoding="utf-8"
                )
                self.assert_rejected_without_index_write("source_id")

    def test_manifest_identity_must_be_an_object_matching_source_id(self):
        for manifest in (
            None, False, 1, "manifest", [],
            *({"source_sha256": value} for value in (None, True, 1, [], {}, "sha256:" + "b" * 64)),
        ):
            with self.subTest(manifest=manifest):
                (self.references / "knowledge-manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                self.assert_rejected_without_index_write("knowledge-manifest")

    def test_matching_original_source_identity_is_not_rehashed(self):
        extracted_id = "sha256:" + hashlib.sha256(self.source.read_bytes()).hexdigest()
        for source_id in ("sha256:" + "1" * 64, "sha256:" + "abcdef0123456789" * 4):
            with self.subTest(source_id=source_id):
                self.assertNotEqual(source_id, extracted_id)
                self.source_map["source_id"] = source_id
                self.write_map(self.source_map)
                (self.references / "knowledge-manifest.json").write_text(
                    json.dumps({"title": "Sample", "source_sha256": source_id}), encoding="utf-8"
                )
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

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

    def test_claim_provenance_fields_are_required(self):
        for field in ("extraction_type", "verbatim_quote_words", "confidence"):
            with self.subTest(field=field):
                data = copy.deepcopy(self.source_map)
                del data["claims"][0][field]
                self.write_map(data)
                self.assert_rejected_without_index_write(field)

    def test_claim_provenance_fields_reject_invalid_values(self):
        for field, values in (
            ("extraction_type", ("unknown", None, [], {})),
            ("verbatim_quote_words", (-1, True, 1.0, "1", None)),
            ("confidence", (-0.1, 1.1, True, "0.5", None, float("nan"), float("inf"), -float("inf"))),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.source_map)
                    data["claims"][0][field] = value
                    self.write_map(data)
                    self.assert_rejected_without_index_write(field)

    def test_claim_provenance_fields_accept_valid_values(self):
        for field, values in (
            ("extraction_type", ("synthesized", "named-framework", "short-quote", "inferred")),
            ("verbatim_quote_words", (0, 3)),
            ("confidence", (0, 1, 0.5)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.source_map)
                    data["claims"][0][field] = value
                    self.write_map(data)
                    self.run_gate("--write-source-index")
                    self.run_gate()

    def test_segment_ranges_cannot_exceed_source_bounds(self):
        source_text = self.source.read_text(encoding="utf-8")
        for field, value in (
            ("char_end", len(source_text) + 1),
            ("line_end", len(source_text.splitlines()) + 1),
        ):
            with self.subTest(field=field):
                data = copy.deepcopy(self.source_map)
                data["segments"][-1][field] = value
                self.write_map(data)
                self.assert_rejected_without_index_write("source-map")

    def test_line_ranges_must_match_character_ranges(self):
        for index, changes in (
            (0, {"line_start": 2, "line_end": 2}),
            (1, {"line_start": 1, "line_end": 1}),
            (0, {"line_end": 2}),
        ):
            with self.subTest(segment=index, changes=changes):
                data = copy.deepcopy(self.source_map)
                data["segments"][index].update(changes)
                self.write_map(data)
                self.assert_rejected_without_index_write("line range does not match char range")

    def test_character_ranges_handle_line_boundaries_and_separators(self):
        for separator in ("\n", "\f"):
            self.source.write_text(f"Я{separator}🙂{separator}Б", encoding="utf-8")
            self.source_map["segments"][1].update(char_start=4, char_end=5, line_start=3, line_end=3)
            for start, end, first, last in (
                (0, 1, 1, 1),
                (0, 2, 1, 1),
                (1, 2, 1, 1),
                (2, 3, 2, 2),
                (1, 3, 1, 2),
                (0, 5, 1, 3),
            ):
                with self.subTest(separator=repr(separator), start=start, end=end):
                    self.source_map["segments"][0].update(
                        char_start=start, char_end=end, line_start=first, line_end=last
                    )
                    self.write_map(self.source_map)
                    self.run_gate("--write-source-index")
                    self.run_gate()

    def test_gate_accepts_real_segmenter_output(self):
        self.source.write_text(
            "# Первая глава\n\n" + " ".join(f"наблюдение{i}" for i in range(45))
            + "\n\n# Вторая глава\n\n" + " ".join(f"проверка{i}" for i in range(45)) + "\n",
            encoding="utf-8",
        )
        output_dir = self.source.parent / "segments"
        result = subprocess.run(
            [os.environ.get("UV", "uv"), "run", "--script", str(QUALITY_GATE.with_name("segment_text.py")),
             "--input", str(self.source), "--out", str(output_dir), "--min-headings", "2"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.source_map["segments"] = json.loads(
            (output_dir / "outline.json").read_text(encoding="utf-8")
        )["segments"]
        segment_ids = [segment["segment_id"] for segment in self.source_map["segments"]]
        self.assertEqual(segment_ids, ["seg-001", "seg-002"])
        self.source_map["claims"][0]["source_segments"] = segment_ids
        self.source_map["claims"][1]["source_segments"] = segment_ids[:1]
        self.write_map(self.source_map)
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_exact_source_upper_bounds_are_allowed(self):
        for text in ("А\nБ", "А\nБ\n", "А\fБ"):
            with self.subTest(text=repr(text)):
                self.source.write_text(text, encoding="utf-8")
                self.source_map["segments"][0].update(char_start=0, char_end=2)
                self.source_map["segments"][1].update(char_start=2, char_end=len(text), line_end=2)
                self.write_map(self.source_map)
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_unicode_crlf_coordinates_count_normalized_characters_not_bytes(self):
        raw_text = "Я\r\n🙂\r\n"
        self.source.write_bytes(raw_text.encode("utf-8"))
        self.source_map["segments"][0].update(char_start=0, char_end=2)
        self.source_map["segments"][1].update(char_start=2, char_end=4)
        self.write_map(self.source_map)
        self.run_gate("--write-source-index")
        self.run_gate()
        for end in (len(raw_text), len(raw_text.encode("utf-8"))):
            with self.subTest(char_end=end):
                self.source_map["segments"][1]["char_end"] = end
                self.write_map(self.source_map)
                self.assert_rejected_without_index_write("source-map")

    def test_empty_source_cannot_have_nonempty_segments(self):
        self.source.write_text("", encoding="utf-8")
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

    def test_mixed_case_markdown_artifacts_preserve_filename_in_index(self):
        for suffix in (".MD", ".Md"):
            with self.subTest(suffix=suffix):
                artifact = self.references / f"notes{suffix}"
                data = copy.deepcopy(self.source_map)
                data["claims"][0]["artifact"] = f"references/{artifact.name}"
                self.write_map(data)
                artifact.write_text(self.concept_text, encoding="utf-8")
                try:
                    self.run_gate("--write-source-index")
                    self.run_gate()
                    self.assertIn(
                        f"({artifact.name}#diagnosis-before-action)",
                        self.index.read_text(encoding="utf-8"),
                    )
                finally:
                    artifact.unlink()
                    self.write_map(self.source_map)

    def test_artifact_must_be_reference_markdown(self):
        for artifact in ("../source.txt", "SKILL.md", "references/source-map.json", "references/source-index.md"):
            with self.subTest(artifact=artifact):
                data = copy.deepcopy(self.source_map)
                data["claims"][0]["artifact"] = artifact
                self.write_map(data)
                self.assert_rejected_without_index_write("artifact")

    def test_artifact_links_to_source_index_are_rejected(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                self.index.write_text(self.concept_text, encoding="utf-8")
                alias = self.references / "alias.md"
                if kind == "symlink":
                    alias.symlink_to(self.index.name)
                else:
                    os.link(self.index, alias)
                data = copy.deepcopy(self.source_map)
                data["claims"][0]["artifact"] = "references/alias.md"
                self.write_map(data)
                before = self.snapshot()
                try:
                    self.run_gate("--write-source-index", success=False, diagnostic="invalid artifact")
                    self.assertEqual(self.snapshot(), before)
                finally:
                    alias.unlink()

    def test_source_index_case_alias_artifact_is_rejected(self):
        alias = self.references / "source-index.MD"
        alias.write_text(self.concept_text, encoding="utf-8")
        if not self.index.exists() or not alias.samefile(self.index):
            self.skipTest("Файловая система различает регистр имён")
        self.source_map["claims"][0]["artifact"] = "references/source-index.MD"
        self.write_map(self.source_map)
        before = self.snapshot()
        self.run_gate("--write-source-index", success=False, diagnostic="invalid artifact")
        self.assertEqual(self.snapshot(), before)

    def test_source_index_hardlink_to_unclaimed_file_is_rejected(self):
        os.link(self.references / "glossary.md", self.index)
        before = self.snapshot()
        self.run_gate(
            "--write-source-index", success=False,
            diagnostic="source-index.md must not share hard links",
        )
        self.assertEqual(self.snapshot(), before)

    def test_unclaimed_symlink_to_existing_or_missing_index_is_allowed(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.index.unlink(missing_ok=True)
                if existing:
                    self.run_gate("--write-source-index")
                alias = self.references / "index-alias.md"
                alias.symlink_to(self.index.name)
                alias_state = (alias.readlink(), alias.lstat().st_mtime_ns)
                derived = {self.index.relative_to(self.skill), alias.relative_to(self.skill)}
                before = {path: state for path, state in self.snapshot().items() if path not in derived}
                try:
                    self.run_gate("--write-source-index")
                    generated = self.snapshot()
                    self.run_gate()
                    self.assertEqual(self.snapshot(), generated)
                    self.assertEqual(
                        {path: state for path, state in generated.items() if path not in derived}, before
                    )
                    self.assertTrue(alias.is_symlink())
                    self.assertEqual((alias.readlink(), alias.lstat().st_mtime_ns), alias_state)
                finally:
                    alias.unlink()

    def test_artifact_links_to_other_files_and_similar_names_are_allowed(self):
        for kind in ("symlink", "hardlink", "separate"):
            with self.subTest(kind=kind):
                artifact = self.references / "source-index-notes.md"
                if kind == "symlink":
                    artifact.symlink_to(self.concepts.name)
                elif kind == "hardlink":
                    os.link(self.concepts, artifact)
                else:
                    artifact.write_text(self.concept_text, encoding="utf-8")
                data = copy.deepcopy(self.source_map)
                data["claims"][0]["artifact"] = f"references/{artifact.name}"
                self.write_map(data)
                before = (artifact.read_bytes(), artifact.stat().st_mtime_ns)
                try:
                    self.run_gate("--write-source-index")
                    self.run_gate()
                    self.assertEqual((artifact.read_bytes(), artifact.stat().st_mtime_ns), before)
                finally:
                    artifact.unlink()

    def test_malformed_artifact_paths_fail_without_traceback_or_index_write(self):
        for artifact in ("references/\x00.md", "references/" + "x" * 300 + ".md"):
            with self.subTest(artifact=artifact):
                data = copy.deepcopy(self.source_map)
                data["claims"][0]["artifact"] = artifact
                self.write_map(data)
                self.assert_rejected_without_index_write("invalid artifact")

    def test_cyclic_artifact_and_segment_link_fail_without_index_write(self):
        loop = self.references / "loop.md"
        loop.symlink_to(loop.name)
        for location, diagnostic in (("artifact", "artifact"), ("href", "segment link")):
            with self.subTest(location=location):
                data = copy.deepcopy(self.source_map)
                text = self.concept_text
                if location == "artifact":
                    data["claims"][0]["artifact"] = "references/loop.md"
                else:
                    text += '\n<a href="loop.md#seg-001">seg-001</a>'
                self.write_map(data)
                self.concepts.write_text(text, encoding="utf-8")
                self.assert_rejected_without_index_write(diagnostic)

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

    def test_mixed_case_markdown_auxiliary_errors_are_checked(self):
        for suffix in (".MD", ".Md"):
            for text, diagnostic in (
                ("Источник: seg-999.\n", "unknown segment"),
                ("[seg-001](wrong.md#seg-001)\n", "segment link"),
            ):
                with self.subTest(suffix=suffix, text=text):
                    notes = self.references / f"notes{suffix}"
                    notes.write_text(text, encoding="utf-8")
                    try:
                        self.assert_rejected_without_index_write(diagnostic)
                    finally:
                        notes.unlink()

    def test_mixed_case_markdown_auxiliary_valid_links_are_accepted(self):
        for suffix in (".MD", ".Md"):
            with self.subTest(suffix=suffix):
                notes = self.references / f"notes{suffix}"
                notes.write_text("[seg-001](source-index.md#seg-001)\n", encoding="utf-8")
                try:
                    self.run_gate("--write-source-index")
                    self.run_gate()
                finally:
                    notes.unlink()

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

    def test_script_and_style_data_do_not_create_source_references(self):
        for tag in ("script", "style"):
            for markup in (
                f"<{tag}>seg-999</{tag}>",
                f'<a href="source-index.md#seg-001">seg-001<{tag}> seg-002 </{tag}></a>',
            ):
                with self.subTest(tag=tag, markup=markup):
                    self.concepts.write_text(markup + "\n\n" + self.concept_text, encoding="utf-8")
                    self.run_gate("--write-source-index")
                    before = self.snapshot()
                    self.run_gate()
                    self.assertEqual(self.snapshot(), before)

    def test_visible_content_after_script_and_style_is_still_checked(self):
        for tag in ("script", "style"):
            for text, diagnostic in (
                ("Источник: seg-999.", "unknown segment"),
                ("## diagnosis-before-action", "claim heading"),
            ):
                with self.subTest(tag=tag, text=text):
                    self.concepts.write_text(
                        self.concept_text + f"\n<{tag}>ignored</{tag}>\n\n{text}\n", encoding="utf-8"
                    )
                    self.assert_rejected_without_index_write(diagnostic)

    def test_explicit_script_and_style_ids_still_conflict_with_claim_heading(self):
        for tag in ("script", "style"):
            with self.subTest(tag=tag):
                self.concepts.write_text(
                    f'<{tag} id="diagnosis-before-action">ignored</{tag}>\n\n' + self.concept_text,
                    encoding="utf-8",
                )
                self.assert_rejected_without_index_write("claim heading")

    def test_explicit_html_anchors_conflict_with_claim_heading(self):
        for markup in (
            '<h1 id="diagnosis-before-action">Другая тема</h1>',
            '<div id="diagnosis-before-action">Пример</div>',
            '<pre id="diagnosis-before-action">Пример</pre>',
            '<a name="diagnosis-before-action"></a>',
            '<div id="diagnosis&#45;before-action"></div>',
        ):
            for position in ("before", "after"):
                with self.subTest(markup=markup, position=position):
                    text = (markup + "\n\n" + self.concept_text if position == "before"
                            else self.concept_text + "\n" + markup + "\n")
                    self.concepts.write_text(text, encoding="utf-8")
                    self.assert_rejected_without_index_write("claim heading")

    def test_distinct_html_ids_and_non_anchor_names_are_allowed(self):
        for markup in (
            '<div id="other-anchor"></div>',
            '<a name="other-anchor"></a>',
            '<div id="Diagnosis-Before-Action"></div>',
            '<div name="diagnosis-before-action"></div>',
        ):
            with self.subTest(markup=markup):
                self.concepts.write_text(markup + "\n\n" + self.concept_text, encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_html_anchor_examples_inside_code_and_comments_are_ignored(self):
        markup = (
            '<h1 id="diagnosis-before-action">Тема</h1>'
            '<div id="diagnosis-before-action"></div>'
            '<pre id="diagnosis-before-action">Пример</pre>'
            '<a name="diagnosis-before-action"></a>'
        )
        for example in (f"```html\n{markup}\n```", f"    {markup}", f"`{markup}`", f"<!-- {markup} -->"):
            with self.subTest(example=example):
                self.concepts.write_text(example + "\n\n" + self.concept_text, encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_explicit_html_anchor_does_not_number_automatic_headings(self):
        self.source_map["claims"][0]["claim_id"] = "other-1"
        self.write_map(self.source_map)
        self.concepts.write_text(
            '<div id="other"></div>\n\n# Other\n\n'
            + self.concept_text.replace("diagnosis-before-action", "other-1"), encoding="utf-8"
        )
        self.run_gate("--write-source-index")
        self.run_gate()

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

    def test_segment_links_must_point_to_matching_index_entry(self):
        links = [
            f"[seg-001]({target})"
            for target in (
                "source-index.md#seg-002",
                "source-index.md#seg-001-extra",
                "source-index.md#prefix-seg-001",
                "source-index.md#seg-001_extra",
                "source%2Dindex.md#seg%2D002",
                "source%2Dindex.md#seg%2D999",
                "concepts.md#seg-001",
                "missing.md#seg-001",
                "https://example.com/source-index.md#seg-001",
                f"{self.index.as_posix()}#seg-001",
            )
        ]
        links.append("[seg-001][source]\n\n[source]: source-index.md#seg-002")
        links.append("[Источник](other.md#seg-001)")
        for link in links:
            with self.subTest(link=link):
                self.concepts.write_text(self.concept_text + "\n" + link + "\n", encoding="utf-8")
                self.assert_rejected_without_index_write("segment link")

    def test_first_duplicate_html_attribute_is_validated(self):
        for markup, diagnostic in (
            ('<a href="other.md#seg-001" href="source-index.md#seg-001">seg-001</a>', "segment link"),
            ('<img src="icon.png#seg-999" src="icon.png">', "unknown segment"),
            ('<img src="icon.png" alt="seg-999" alt="diagram">', "unknown segment"),
            ('<div id="diagnosis-before-action" id="other"></div>', "claim heading"),
            ('<a name="diagnosis-before-action" name="other"></a>', "claim heading"),
        ):
            with self.subTest(markup=markup):
                self.concepts.write_text(self.concept_text + "\n" + markup, encoding="utf-8")
                self.assert_rejected_without_index_write(diagnostic)

    def test_later_duplicate_html_attributes_are_ignored(self):
        for markup in (
            '<a href="source-index.md#seg-001" href="other.md#seg-999">seg-001</a>',
            '<img src="icon.png" src="icon.png#seg-999">',
            '<img src="icon.png" alt="diagram" alt="seg-999">',
            '<div id="other" id="diagnosis-before-action"></div>',
            '<a name="other" name="diagnosis-before-action"></a>',
        ):
            with self.subTest(markup=markup):
                self.concepts.write_text(self.concept_text + "\n" + markup, encoding="utf-8")
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_segment_substrings_are_allowed_in_claim_ids_and_links(self):
        for claim_id in ("concept-seg-001", "seg-001-extra", "prefix-seg-001", "concept-seg-999"):
            with self.subTest(claim_id=claim_id):
                self.source_map["claims"][0]["claim_id"] = claim_id
                self.write_map(self.source_map)
                self.concepts.write_text(
                    self.concept_text.replace("diagnosis-before-action", claim_id)
                    + f"\n[Тезис](concepts.md#{claim_id})\n",
                    encoding="utf-8",
                )
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_unclosed_html_segment_links_are_checked(self):
        for link in (
            '<a href="other.md#seg-001">source',
            '<a href="source-index.md#seg-002">seg-001',
            '<a href="other.md">seg-001',
            '<a href>seg-001',
            '<a href="">seg-001',
            '<a href="source-index.md#seg-002"><img src="icon.png" alt="seg-001">',
        ):
            with self.subTest(link=link):
                self.concepts.write_text(self.concept_text + "\n" + link, encoding="utf-8")
                self.assert_rejected_without_index_write("segment link")

    def test_new_html_anchor_does_not_discard_previous_unclosed_link(self):
        for next_link in (
            '<a href="https://example.com">guide</a>',
            '<a href="source-index.md#seg-001">seg-001</a>',
            '<a name="aside">aside</a>',
        ):
            with self.subTest(next_link=next_link):
                self.concepts.write_text(
                    self.concept_text + '\n<a href="other.md#seg-001">source ' + next_link,
                    encoding="utf-8",
                )
                self.assert_rejected_without_index_write("segment link")

    def test_valid_and_unrelated_unclosed_html_links_are_allowed(self):
        for link in (
            '<a href="source-index.md#seg-001">source',
            '<a href="source-index.md#seg-001">seg-001',
            '<a href="source-index.md#seg-001"><img src="icon.png" alt="seg-001">',
            '<a href="other.md#ordinary-anchor">source',
            '<a name="section">seg-001</a>',
            '<a name="section">seg-001',
        ):
            with self.subTest(link=link):
                self.concepts.write_text(self.concept_text + "\n" + link, encoding="utf-8")
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_exact_segment_ids_are_reserved_from_claim_ids(self):
        for claim_id in ("seg-001", "seg-002", "seg-999"):
            with self.subTest(claim_id=claim_id):
                self.source_map["claims"][0]["claim_id"] = claim_id
                self.write_map(self.source_map)
                self.concepts.write_text(
                    self.concept_text.replace("diagnosis-before-action", claim_id), encoding="utf-8"
                )
                self.assert_rejected_without_index_write("reserved for segments")

    def test_segment_link_paths_are_relative_to_containing_document(self):
        nested = self.references / "topics" / "details.md"
        nested.parent.mkdir()
        nested.write_text("# Details\n", encoding="utf-8")
        for path in (self.skill / "SKILL.md", nested):
            with self.subTest(path=path.relative_to(self.skill)):
                original = path.read_text(encoding="utf-8")
                try:
                    path.write_text(
                        original + "\n[seg-001](source-index.md#seg-001)\n", encoding="utf-8"
                    )
                    self.assert_rejected_without_index_write("segment link")
                finally:
                    path.write_text(original, encoding="utf-8")

    def test_composite_segment_fragments_must_use_canonical_index_anchor(self):
        for target in (
            "other.md#ref/seg-001",
            "other.md#ref%2Fseg-001",
            "source-index.md#ref/seg-001",
            "source-index.md#ref%2Fseg-001",
            "%00#seg-001",
        ):
            with self.subTest(target=target):
                self.concepts.write_text(
                    self.concept_text + f"\n[docs]({target})\n", encoding="utf-8"
                )
                self.assert_rejected_without_index_write("segment link")

    def test_encoded_path_separators_are_not_directory_separators(self):
        nested = self.references / "topics" / "details.md"
        nested.parent.mkdir()
        for separator in ("%2F", "%2f", "%5C", "%5c"):
            with self.subTest(separator=separator):
                nested.write_text(
                    f"# Details\n\n[seg-001](..{separator}source-index.md#seg-001)\n",
                    encoding="utf-8",
                )
                self.assert_rejected_without_index_write("segment link")

    def test_index_link_path_cannot_descend_through_index_file(self):
        for target in (
            "source-index.md/#seg-001",
            "source-index.md/.#seg-001",
            "source-index.md/%2E#seg-001",
            "source-index.md//#seg-001",
            "source-index.md//.#seg-001",
            "source-index.md//%2E#seg-001",
            "source-index.md/child/..#seg-001",
            "source-index.md/child/%2E%2E#seg-001",
            "source-index.md/child/.%2e#seg-001",
            "topics//../source-index.md#seg-001",
            ".//source-index.md#seg-001",
        ):
            with self.subTest(target=target):
                self.concepts.write_text(
                    self.concept_text + f"\n[seg-001]({target})\n", encoding="utf-8"
                )
                self.assert_rejected_without_index_write("segment link")
        for target in (
            "./source-index.md#seg-001",
            "topics/../source-index.md#seg-001",
            "topics/%2E%2E/source-index.md#seg-001",
            "topics/.././source-index.md#seg-001",
        ):
            with self.subTest(target=target):
                self.concepts.write_text(
                    self.concept_text + f"\n[seg-001]({target})\n", encoding="utf-8"
                )
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_plain_segments_and_valid_segment_links_are_allowed(self):
        for link in (
            "seg-001",
            "[seg-001](source-index.md#seg-001)",
            "[seg-001](source%2Dindex.md#seg%2D001)",
            "[seg-001][source]\n\n[source]: source-index.md#seg-001",
            "[`seg-001`](source-index.md#seg-001)",
        ):
            with self.subTest(link=link):
                self.concepts.write_text(self.concept_text + "\n" + link + "\n", encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_valid_segment_links_from_root_and_nested_documents(self):
        skill_md = self.skill / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8")
            + "\n[seg-001](references/source-index.md#seg-001)\n",
            encoding="utf-8",
        )
        nested = self.references / "topics" / "details.md"
        nested.parent.mkdir()
        nested.write_text("# Details\n\n[seg-001](../source-index.md#seg-001)\n", encoding="utf-8")
        self.run_gate("--write-source-index")
        self.run_gate()

    def test_invalid_segment_links_inside_code_and_comments_are_ignored(self):
        link = "[seg-001](source-index.md#seg-002)"
        for example in (f"```markdown\n{link}\n```", f"    {link}", f"`{link}`", f"<!-- {link} -->"):
            with self.subTest(example=example):
                self.concepts.write_text(self.concept_text + "\n" + example + "\n", encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_linked_image_alt_must_match_segment_destination(self):
        for segment_id, valid in (("seg-002", False), ("seg-001", True)):
            with self.subTest(segment_id=segment_id):
                self.concepts.write_text(
                    self.concept_text + f"\n[![seg-001](icon.png)](source-index.md#{segment_id})\n",
                    encoding="utf-8",
                )
                if valid:
                    self.run_gate("--write-source-index")
                    self.run_gate()
                else:
                    self.assert_rejected_without_index_write("segment link")

    def test_image_map_segment_links_reject_invalid_destinations(self):
        for area in (
            '<area href="other.md#seg-001" alt="Источник">',
            '<area href="source-index.md#seg-002" alt="seg-001">',
            '<area href="source-index.md#seg-002" alt="seg-001" />',
            '<area href="" alt="seg-001">',
            '<area href="source-index.md" alt="seg-001">',
            '<area href="other.md#seg-001" href="source-index.md#seg-001" alt="seg-001">',
            '<area href="source-index.md#seg-002" alt="seg-001" alt="seg-002">',
        ):
            with self.subTest(area=area):
                self.concepts.write_text(
                    self.concept_text + '\n<img src="diagram.png" usemap="#sources">'
                    '<map name="sources">' + area + "</map>\n", encoding="utf-8"
                )
                self.assert_rejected_without_index_write("segment link")

    def test_valid_and_unrelated_image_map_links_are_allowed(self):
        for area in (
            '<area href="source-index.md#seg-001" alt="seg-001">',
            '<area href="source-index.md#seg-001" alt="Источник">',
            '<area href="https://example.com/help" alt="Справка">',
            '<area alt="seg-001">',
            '<area href="source-index.md#seg-001" alt="seg-001" />',
            '<area href="source-index.md#seg-001" href="other.md#seg-999" alt="seg-001">',
            '<area href="source-index.md#seg-001" alt="seg-001" alt="seg-999">',
        ):
            with self.subTest(area=area):
                self.concepts.write_text(
                    self.concept_text + '\n<img src="diagram.png" usemap="#sources">'
                    '<map name="sources">' + area + "</map>\n", encoding="utf-8"
                )
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_svg_xlink_segment_links_reject_invalid_destinations(self):
        for markup in (
            '<svg><a xlink:href="other.md#seg-001">seg-001</a></svg>',
            '<svg><a href="other.md#seg-001" xlink:href="source-index.md#seg-001">seg-001</a></svg>',
            '<svg><a href="" xlink:href="source-index.md#seg-001">seg-001</a></svg>',
            '<svg><foreignObject><svg><a xlink:href="other.md#seg-001">seg-001</a></svg>'
            '</foreignObject></svg>',
            '<svg><title><svg><a xlink:href="other.md#seg-001">seg-001</a></svg></title></svg>',
        ):
            with self.subTest(markup=markup):
                self.concepts.write_text(self.concept_text + "\n" + markup + "\n", encoding="utf-8")
                self.assert_rejected_without_index_write("segment link")

    def test_valid_and_non_svg_xlink_segment_references_are_allowed(self):
        for markup in (
            '<svg><a xlink:href="source-index.md#seg-001">seg-001</a></svg>',
            '<svg><a href="source-index.md#seg-001" xlink:href="other.md#seg-999">seg-001</a></svg>',
            '<a xlink:href="other.md#seg-001">seg-001</a>',
            '<svg><foreignObject><a xlink:href="other.md#seg-001">seg-001</a>'
            '</foreignObject></svg>',
            '<svg><foreignObject><svg><a xlink:href="source-index.md#seg-001">seg-001</a></svg>'
            '</foreignObject></svg>',
            '<svg><desc><a xlink:href="other.md#seg-001">seg-001</a></desc></svg>',
            '<svg><title><a xlink:href="other.md#seg-001">seg-001</a></title></svg>',
            '<svg><desc><svg><a xlink:href="source-index.md#seg-001">seg-001</a></svg></desc></svg>',
            '<svg><title><svg><a xlink:href="source-index.md#seg-001">seg-001</a></svg></title></svg>',
        ):
            with self.subTest(markup=markup):
                self.concepts.write_text(self.concept_text + "\n" + markup + "\n", encoding="utf-8")
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)

    def test_same_named_svg_integration_points_keep_context_balanced(self):
        for point in ("foreignObject", "desc", "title"):
            with self.subTest(point=point, position="nested"):
                markup = (
                    f'<svg><{point}><{point}></{point}>'
                    f'<a xlink:href="other.md#seg-001">seg-001</a></{point}></svg>'
                )
                self.concepts.write_text(self.concept_text + "\n" + markup + "\n", encoding="utf-8")
                self.run_gate("--write-source-index")
                before = self.snapshot()
                self.run_gate()
                self.assertEqual(self.snapshot(), before)
        for point in ("foreignObject", "desc", "title"):
            with self.subTest(point=point, position="after"):
                markup = (
                    f'<svg><{point}></{point}>'
                    '<a xlink:href="other.md#seg-001">seg-001</a></svg>'
                )
                self.concepts.write_text(self.concept_text + "\n" + markup + "\n", encoding="utf-8")
                self.assert_rejected_without_index_write("segment link")

    def test_image_alt_is_excluded_from_heading_anchor(self):
        self.concepts.write_text(
            "# ![Diagnosis Before Action](icon.png)\n\n" + self.concept_text, encoding="utf-8"
        )
        self.run_gate("--write-source-index")
        self.run_gate()
        self.concepts.write_text(
            "# ![badge](icon.png)Diagnosis Before Action\n\n" + self.concept_text, encoding="utf-8"
        )
        self.assert_rejected_without_index_write("claim heading")

    def test_unknown_segment_in_image_alt_is_checked(self):
        self.concepts.write_text(self.concept_text + "\n![seg-999](icon.png)\n", encoding="utf-8")
        self.assert_rejected_without_index_write("unknown segment")

    def test_segment_names_in_url_paths_and_queries_are_not_references(self):
        for example in (
            "[docs](https://example.com/seg-999/guide)",
            "![diagram](images/seg-999.png)",
            "[docs](https://example.com/guide?section=seg-999)",
            "[docs][guide]\n\n[guide]: https://example.com/seg-999/guide",
        ):
            with self.subTest(example=example):
                self.concepts.write_text(self.concept_text + "\n" + example + "\n", encoding="utf-8")
                self.run_gate("--write-source-index")
                self.run_gate()

    def test_unknown_segment_fragments_and_alt_are_checked(self):
        for example in (
            "[docs](source-index.md#seg-999)",
            "![diagram](images/icon.svg#seg-999)",
            "![seg-999](images/icon.png)",
            "[docs](source-index.md#seg%2D999)",
            "![diagram](images/icon.svg#seg%2D999)",
        ):
            with self.subTest(example=example):
                self.concepts.write_text(self.concept_text + "\n" + example + "\n", encoding="utf-8")
                self.assert_rejected_without_index_write("unknown segment")

    def test_existing_quality_errors_prevent_index_write(self):
        with (self.skill / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("\nTODO\n")
        self.assert_rejected_without_index_write("placeholder")


if __name__ == "__main__":
    unittest.main()
