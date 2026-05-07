#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

uv run python - "$TMP_DIR/sample.epub" <<'PY'
import sys
import zipfile
from pathlib import Path

epub = Path(sys.argv[1])
with zipfile.ZipFile(epub, "w") as zf:
    zf.writestr("mimetype", "application/epub+zip")
    zf.writestr(
        "META-INF/container.xml",
        """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
    )
    zf.writestr(
        "OEBPS/content.opf",
        """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Sample EPUB</dc:title>
    <dc:creator>Test Author</dc:creator>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="app" href="appendix.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
    <itemref idref="app"/>
  </spine>
</package>
""",
    )
    zf.writestr(
        "OEBPS/toc.ncx",
        """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="nav1" playOrder="1">
      <navLabel><text>1. First chapter</text></navLabel>
      <content src="ch1.xhtml"/>
    </navPoint>
    <navPoint id="nav2" playOrder="2">
      <navLabel><text>Appendix</text></navLabel>
      <content src="appendix.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
""",
    )
    zf.writestr(
        "OEBPS/ch1.xhtml",
        """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>1.\u00a0First chapter</h1><p>Main content.</p></body></html>""",
    )
    zf.writestr(
        "OEBPS/appendix.xhtml",
        """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Appendix</h1><p>Short appendix.</p></body></html>""",
    )
PY

KNOWLEDGE_COMPILER_CACHE_DIR="$TMP_DIR/cache" \
    sh "$SKILL_DIR/scripts/prepare_source.sh" \
    --input "$TMP_DIR/sample.epub" \
    --job-id epub \
    >/dev/null

uv run python - "$TMP_DIR/cache/jobs/epub/metadata.json" <<'PY'
import json
import sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert meta["format"] == "epub"
assert meta["epub_title"] == "Sample EPUB"
assert meta["epub_author"] == "Test Author"
assert meta["epub_toc_source"] == "ncx"
assert len(meta["epub_toc"]) == 2
assert meta["epub_toc"][0]["label"] == "1. First chapter"
PY
