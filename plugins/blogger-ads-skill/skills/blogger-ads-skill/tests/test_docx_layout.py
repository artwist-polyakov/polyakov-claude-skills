import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import handoff
import render_docx

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def make_layout_fixture(source, destination, count=2):
    """Синтетический комплект для проверок связности, не визуальный вердикт о реальном документе."""
    destination.mkdir()
    pdf = destination / 'document.pdf'
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=200, height=300)
    with pdf.open('wb') as stream:
        writer.write(stream)
    pages = []
    for number in range(1, count + 1):
        image = destination / f'page-{number:03}.png'
        image.write_bytes(PNG)
        pages.append({'number': number, 'path': image.name, 'sha256': render_docx.sha(image)})
    data = {'kind': 'docx_layout', 'format_version': 1,
            'source': {'path': str(source), 'sha256': render_docx.sha(source)},
            'pdf': {'path': pdf.name, 'sha256': render_docx.sha(pdf)},
            'page_count': count, 'pages': pages, 'review': None}
    manifest = destination / 'manifest.json'
    manifest.write_text(json.dumps(data))
    return manifest


class DocxLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'final.docx'
        self.source.write_bytes(b'test DOCX bytes; LO is mocked in unit tests')
        self.state = self.root / 'deal-state.json'
        self.state.write_bytes((ROOT / 'assets/deal.example.json').read_bytes())
        self.at = '2026-11-01T12:00:00+03:00'

    def package(self, name='layout', source=None):
        return make_layout_fixture(source or self.source, self.root / name)

    def confirm(self, manifest, result='passed'):
        return render_docx.confirm(manifest, [1, 2], result, 'Тестовый вердикт: проверены обе страницы, колонки и переносы')

    def register(self, files, group='contract', version='v1', status='draft', kind=None):
        return handoff.register(self.state, files, status, version, self.at, group=group, document_kind=kind)

    def test_render_creates_every_page_and_does_not_confirm_it(self):
        def conversion(command, **kwargs):
            destination = Path(command[command.index('--outdir') + 1])
            writer = PdfWriter()
            for _ in range(2):
                writer.add_blank_page(width=200, height=300)
            with (destination / 'final.pdf').open('wb') as stream:
                writer.write(stream)
            self.assertIn('--headless', command)
            self.assertEqual(Path(command[-1]), self.source)
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(render_docx.subprocess, 'run', side_effect=conversion):
            manifest = render_docx.render(self.source, self.root / 'rendered', sys.executable)
        data = render_docx.check(manifest, require_review=False)
        self.assertIsNone(data['review'])
        self.assertEqual([page['number'] for page in data['pages']], [1, 2])
        self.assertEqual(data['source']['sha256'], render_docx.sha(self.source))
        with self.assertRaisesRegex(ValueError, 'Нет положительного'):
            render_docx.check(manifest)

    def test_render_never_overwrites_existing_directory(self):
        folder = self.root / 'existing'
        folder.mkdir()
        sentinel = folder / 'keep.txt'
        sentinel.write_bytes(b'keep')
        with patch.object(render_docx.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'уже существует'):
                render_docx.render(self.source, folder, sys.executable)
        run.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertEqual(list(folder.iterdir()), [sentinel])

    def test_changed_docx_pdf_or_png_cannot_be_confirmed_or_reused(self):
        for label in ('DOCX', 'PDF', 'PNG'):
            with self.subTest(label=label):
                source = self.root / (label + '.docx')
                source.write_bytes(b'original')
                manifest = self.package(label, source)
                data = json.loads(manifest.read_text())
                target = {'DOCX': source, 'PDF': manifest.parent / data['pdf']['path'],
                          'PNG': manifest.parent / data['pages'][0]['path']}[label]
                original = target.read_bytes()
                target.write_bytes(original + b'changed')
                before = manifest.read_bytes()
                with self.assertRaisesRegex(ValueError, 'изменён'):
                    self.confirm(manifest)
                self.assertEqual(manifest.read_bytes(), before)
                target.write_bytes(original)
                self.confirm(manifest)
                target.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'изменён'):
                    render_docx.check(manifest)

    def test_pages_must_cover_actual_pdf_once_in_order(self):
        manifest = self.package()
        original = json.loads(manifest.read_text())
        variants = []
        missing = copy.deepcopy(original)
        missing['pages'].pop()
        variants.append(missing)
        duplicate = copy.deepcopy(original)
        duplicate['pages'][1] = duplicate['pages'][0]
        variants.append(duplicate)
        wrong_count = copy.deepcopy(original)
        wrong_count['page_count'] = 1
        wrong_count['pages'].pop()
        variants.append(wrong_count)
        same_file = copy.deepcopy(original)
        same_file['pages'][1].update(path=same_file['pages'][0]['path'])
        variants.append(same_file)
        for data in variants:
            with self.subTest(data=data):
                manifest.write_text(json.dumps(data))
                before = manifest.read_bytes()
                with self.assertRaises(ValueError):
                    self.confirm(manifest)
                self.assertEqual(manifest.read_bytes(), before)

    def test_confirmation_needs_all_viewed_pages_notes_and_explicit_verdict(self):
        manifest = self.package()
        before = manifest.read_bytes()
        for pages, result, notes in [([1], 'passed', 'Просмотрено'), ([1, 1], 'passed', 'Просмотрено'),
                                     ([2, 1], 'passed', 'Просмотрено'), ([1, 2], 'passed', ' '),
                                     ([1, 2], 'unknown', 'Просмотрено')]:
            with self.assertRaises(ValueError):
                render_docx.confirm(manifest, pages, result, notes)
            self.assertEqual(manifest.read_bytes(), before)
        self.confirm(manifest, result='needs_changes')
        with self.assertRaisesRegex(ValueError, 'needs_changes'):
            render_docx.check(manifest)
        confirmed = manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, 'уже зафиксирован'):
            self.confirm(manifest)
        self.assertEqual(manifest.read_bytes(), confirmed)

    def test_handoff_requires_registered_passed_layout_for_own_docx(self):
        self.register([self.source], kind='contract')
        manifest = self.package()
        self.confirm(manifest)
        self.assertFalse(handoff.audit(self.state)['valid'])  # Незарегистрированное подтверждение не используется.
        self.register([manifest], group='layout')
        before = self.state.read_bytes()
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(self.state.read_bytes(), before)
        (manifest.parent / 'document.pdf').write_bytes(b'changed PDF')
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any('PDF' in issue and 'изменён' in issue for issue in result['issues']))
        self.assertEqual(self.state.read_bytes(), before)

    def test_handoff_rejects_pending_and_needs_changes(self):
        self.register([self.source], kind='contract')
        for name, verdict in [('pending', None), ('rejected', 'needs_changes')]:
            with self.subTest(verdict=verdict):
                manifest = self.package(name)
                if verdict:
                    self.confirm(manifest, verdict)
                self.register([manifest], group='layout', version=name)
                result = handoff.audit(self.state)
                self.assertFalse(result['valid'])
                self.assertTrue(any('Нет положительного' in issue for issue in result['issues']))

    def test_received_original_and_historical_own_docx_need_no_layout(self):
        self.register([self.source], status='received', kind='payment')
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])
        data = json.loads(self.state.read_text())
        data['documents'][0].update(status='draft', current=False)
        handoff.save(self.state, data)
        result = handoff.audit(self.state)
        self.assertTrue(result['valid'], result['issues'])

    def test_replacing_docx_archives_layout_and_new_source_requires_new_view(self):
        self.register([self.source], kind='contract')
        manifest = self.package()
        self.confirm(manifest)
        self.register([manifest], group='layout')
        newer = self.root / 'final-v2.docx'
        newer.write_bytes(b'new final source')
        self.register([newer], version='v2', kind='contract')
        records = {item['path']: item for item in json.loads(self.state.read_text())['documents']}
        self.assertFalse(records['layout/manifest.json']['current'])
        result = handoff.audit(self.state)
        self.assertFalse(result['valid'])
        self.assertTrue(any(str(newer) in issue and 'просмотра' in issue for issue in result['issues']))

    def test_module_import_without_optional_dependencies(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in ('pypdf', 'pymupdf', 'fitz'):
        raise AssertionError('Heavy import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import handoff
import render_docx
assert handoff.layout_issues({}) == []
"""
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT / 'scripts', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
