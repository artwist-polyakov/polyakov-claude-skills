"""Проверки печати исходного HTML без отдельной сборки сокращённого PDF."""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("print_html", ROOT / "scripts" / "print_html.py")
printer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(printer)


class PrintTests(unittest.TestCase):
    def test_prints_original_source_with_separate_profile_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "УПД с пробелом.html"
            source.write_text("<html>Исходные 19 колонок</html>", encoding="utf-8")
            output = Path(directory) / "upd.pdf"
            profiles = []
            def browser(command, **kwargs):
                self.assertEqual(command[-1], source.resolve().as_uri())
                profile = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--user-data-dir=")))
                self.assertNotEqual(profile.parent, source.parent)
                profiles.append(profile.parent)
                printed = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--print-to-pdf=")))
                printed.write_bytes(b"%PDF-1.7 test")
                return subprocess.CompletedProcess(command, 0, b"", b"")
            with patch.object(printer, "find_browser", return_value=Path("/test/chrome")), patch.object(printer.subprocess, "run", side_effect=browser):
                printer.print_pdf(source, output)
            record = json.loads(output.with_suffix(".pdf.source.json").read_text())
            self.assertEqual(record["source_sha256"], sha256(source.read_bytes()).hexdigest())
            self.assertEqual(record["pdf_sha256"], sha256(output.read_bytes()).hexdigest())
            self.assertFalse(profiles[0].exists())
            with self.assertRaises(FileExistsError):
                printer.print_pdf(source, output)

    def test_browser_failure_never_leaves_final_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invoice.html"
            source.write_text("<html>Счёт</html>", encoding="utf-8")
            output = Path(directory) / "invoice.pdf"
            with patch.object(printer, "find_browser", return_value=Path("/test/chrome")), patch.object(printer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"fail")):
                with self.assertRaisesRegex(ValueError, "не создал"):
                    printer.print_pdf(source, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".pdf.source.json").exists())


if __name__ == "__main__":
    unittest.main()
