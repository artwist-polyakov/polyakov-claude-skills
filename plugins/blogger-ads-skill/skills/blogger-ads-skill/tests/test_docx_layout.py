import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import handoff
import render_docx

REAL_CONVERT_TO_PDF = render_docx.convert_to_pdf


def write_test_pdf(source, pdf, count=2):
    """Синтетическая отрисовка зависит от байтов исходника, чтобы замечать подмену."""
    import pymupdf
    digest = render_docx.sha(source)
    with pymupdf.open() as document:
        for number in range(count):
            page = document.new_page(width=200, height=300)
            page.insert_text((10, 20), digest[:32], fontsize=8)
            page.insert_text((10, 35), digest[32:], fontsize=8)
            page.insert_text((10, 50), str(number + 1), fontsize=8)
        document.save(pdf)
    return pdf


def fake_convert_to_pdf(source, destination, soffice=None):
    return write_test_pdf(source, destination / (source.stem + '.pdf'))


def make_layout_fixture(source, destination, count=2):
    """Синтетический комплект для проверок связности, не визуальный вердикт о реальном документе."""
    destination.mkdir()
    pdf = destination / 'document.pdf'
    write_test_pdf(source, pdf, count)
    pages = []
    import pymupdf
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, 1):
            image = destination / f'page-{number:03}.png'
            render_docx.page_pixels(page).save(image)
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
        conversion = patch.object(render_docx, 'convert_to_pdf', side_effect=fake_convert_to_pdf)
        self.converter = conversion.start()
        self.addCleanup(conversion.stop)

    def package(self, name='layout', source=None):
        return make_layout_fixture(source or self.source, self.root / name)

    def confirm(self, manifest, result='passed'):
        return render_docx.confirm(manifest, [1, 2], result, 'Тестовый вердикт: проверены обе страницы, колонки и переносы')

    def register(self, files, group='contract', version='v1', status='draft', kind=None):
        return handoff.register(self.state, files, status, version, self.at, group=group, document_kind=kind)

    def test_render_creates_every_page_and_does_not_confirm_it(self):
        def conversion(command, **kwargs):
            destination = Path(command[command.index('--outdir') + 1])
            write_test_pdf(self.source, destination / 'final.pdf')
            self.assertIn('--headless', command)
            self.assertEqual(Path(command[-1]), self.source)
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(render_docx, 'convert_to_pdf', side_effect=REAL_CONVERT_TO_PDF):
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

    def test_pdf_only_binding_checks_all_pages_count_and_dimensions(self):
        pdf = self.root / 'source.pdf'
        write_test_pdf(self.source, pdf)
        render_docx.verify_rendered_pages(self.source, pdf, soffice='/chosen/soffice')
        self.assertEqual(self.converter.call_args.args[2], '/chosen/soffice')
        self.assertFalse(self.converter.call_args.args[1].exists())
        for change in ('count', 'last_page', 'size'):
            write_test_pdf(self.source, pdf)
            writer = PdfWriter(clone_from=pdf)
            if change == 'count':
                writer.remove_page(1)
            elif change == 'size':
                writer.pages[-1].mediabox.upper_right = (250, 300)
            else:
                writer.pages[-1].rotate(180)
            with pdf.open('wb') as stream:
                writer.write(stream)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'не совпадает'):
                render_docx.verify_rendered_pages(self.source, pdf)

    def test_pdf_only_binding_ignores_metadata(self):
        pdf = self.root / 'source.pdf'
        write_test_pdf(self.source, pdf)
        writer = PdfWriter(clone_from=pdf)
        writer.add_metadata({'/CreationDate': 'D:20400101000000Z', '/Title': 'Same pages'})
        with pdf.open('wb') as stream:
            writer.write(stream)
        render_docx.verify_rendered_pages(self.source, pdf)

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

    def test_rebinding_preview_to_another_docx_fails_even_with_updated_hashes(self):
        manifest = self.package()
        self.confirm(manifest)
        different = self.root / 'different.docx'
        different.write_bytes(b'changed DOCX with different layout')
        data = json.loads(manifest.read_text())
        data['source'] = {'path': str(different), 'sha256': render_docx.sha(different)}
        manifest.write_text(json.dumps(data))
        before = {path: path.read_bytes() for path in manifest.parent.iterdir()}
        with self.assertRaisesRegex(ValueError, 'PDF страницы 1 не совпадает'):
            render_docx.check(manifest)
        data['review'] = None
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'PDF страницы 1 не совпадает'):
            self.confirm(manifest)
        self.assertIsNone(json.loads(manifest.read_text())['review'])
        for path, contents in before.items():
            if path != manifest:
                self.assertEqual(path.read_bytes(), contents)
        temporary = self.converter.call_args.args[1]
        self.assertFalse(temporary.exists())

    def test_substituted_pdf_or_png_fails_after_hashes_are_updated(self):
        import pymupdf
        different = self.root / 'different.docx'
        different.write_bytes(b'different page content')
        wrong_pdf = write_test_pdf(different, self.root / 'different.pdf')
        for target in ('pdf', 'png'):
            with self.subTest(target=target):
                manifest = self.package(target)
                data = json.loads(manifest.read_text())
                if target == 'pdf':
                    item = data['pdf']
                    path = manifest.parent / item['path']
                    path.write_bytes(wrong_pdf.read_bytes())
                else:
                    item = data['pages'][0]
                    path = manifest.parent / item['path']
                    with pymupdf.open(wrong_pdf) as document:
                        render_docx.page_pixels(document[0]).save(path)
                item['sha256'] = render_docx.sha(path)
                manifest.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, target.upper() + ' страницы 1 не совпадает'):
                    self.confirm(manifest)

    def test_pdf_metadata_changes_do_not_break_visual_binding(self):
        manifest = self.package()
        data = json.loads(manifest.read_text())
        pdf = manifest.parent / data['pdf']['path']
        writer = PdfWriter(clone_from=PdfReader(pdf))
        writer.add_metadata({'/CreationDate': 'D:20400101000000Z', '/Title': 'Metadata does not change pixels'})
        with pdf.open('wb') as stream:
            writer.write(stream)
        data['pdf']['sha256'] = render_docx.sha(pdf)
        manifest.write_text(json.dumps(data))
        self.confirm(manifest)
        render_docx.check(manifest)

    def test_page_count_and_dimensions_must_match_fresh_conversion(self):
        import pymupdf
        for variant in ('count', 'size'):
            with self.subTest(variant=variant):
                manifest = self.package(variant)
                data = json.loads(manifest.read_text())
                pdf = manifest.parent / data['pdf']['path']
                if variant == 'count':
                    pdf.unlink()
                    write_test_pdf(self.source, pdf, count=1)
                    data['page_count'] = 1
                    data['pages'] = data['pages'][:1]
                else:
                    with pymupdf.open(pdf) as document:
                        document[0].set_mediabox(pymupdf.Rect(0, 0, 250, 350))
                        document.saveIncr()
                data['pdf']['sha256'] = render_docx.sha(pdf)
                manifest.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'не совпадает'):
                    render_docx.check(manifest, require_review=False)

    def test_converter_comes_from_caller_not_manifest(self):
        manifest = self.package()
        data = json.loads(manifest.read_text())
        data['render'] = {'executable': '/untrusted/manifest/command', 'dpi': 1}
        manifest.write_text(json.dumps(data))
        render_docx.check(manifest, require_review=False, soffice='/caller/converter')
        self.assertEqual(self.converter.call_args.args[2], '/caller/converter')
        render_docx.check(manifest, require_review=False)
        self.assertIsNone(self.converter.call_args.args[2])

    def test_missing_converter_and_timeout_are_actionable_failures(self):
        manifest = self.package()
        with patch.object(render_docx, 'convert_to_pdf', side_effect=REAL_CONVERT_TO_PDF):
            with patch.object(render_docx.shutil, 'which', return_value=None):
                with self.assertRaisesRegex(ValueError, 'LibreOffice не найден'):
                    render_docx.check(manifest, require_review=False)
            with patch.object(render_docx.subprocess, 'run', side_effect=subprocess.TimeoutExpired('soffice', 180)):
                with self.assertRaisesRegex(ValueError, 'Не удалось отрисовать DOCX'):
                    render_docx.check(manifest, require_review=False, soffice=sys.executable)

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
