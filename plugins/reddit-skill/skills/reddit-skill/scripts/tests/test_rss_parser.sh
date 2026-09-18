#!/bin/sh
# Test Atom conversion without network requests or third-party dependencies.
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"
TMP_BASE=$(mktemp -d "${TMPDIR:-/tmp}/reddit-test-rss-parser.XXXXXX")
trap 'rm -rf "$TMP_BASE"' EXIT

python3 "$SCRIPTS_DIR/rss_to_listing.py" "$TEST_DIR/fixtures/posts.atom" "$TMP_BASE/posts.json"

python3 - "$SCRIPTS_DIR/rss_to_listing.py" "$TEST_DIR/fixtures/posts.atom" "$TMP_BASE" <<'PY'
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

parser, fixture, workdir = sys.argv[1:]
workdir = Path(workdir)
payload = json.loads((workdir / "posts.json").read_text())
assert payload["kind"] == "Listing" and payload["source"] == "rss"
children = payload["data"]["children"]
assert len(children) == 2 and all(child["kind"] == "t3" for child in children)
first, second = [child["data"] for child in children]
assert first["id"] == "abc123" and first["name"] == "t3_abc123"
assert first["title"] == "Python & RSS"
assert first["subreddit"] == "Python" and first["author"] == "alice"
assert first["created_utc"] == datetime.fromisoformat("2026-09-17T09:00:00+00:00").timestamp()
assert first["url"] == "https://example.com/article?a=1&b=2"
assert first["selftext"] == "Hello & world!\nA B\nNext line\n[link] [comments]"
assert first["selftext_html"].startswith("<p>Hello &amp;")
assert first["score"] is None and first["num_comments"] is None
assert second["author"] == "" and second["subreddit"] == ""
assert second["selftext"] == "" and second["selftext_html"] is None
assert second["url"] == "https://www.reddit.com/r/Python/comments/def456/"
assert second["created_utc"] == datetime.fromisoformat("2026-09-17T09:30:00+00:00").timestamp()

atom = "{http://www.w3.org/2005/Atom}"
empty = workdir / "empty.atom"
empty.write_text('<feed xmlns="http://www.w3.org/2005/Atom"/>')
subprocess.run([sys.executable, parser, str(empty), str(workdir / "empty.json")], check=True)
assert json.loads((workdir / "empty.json").read_text())["data"]["children"] == []

bad_inputs = ["<feed>", "<html>blocked</html>", "<feed><entry/></feed>"]
for field, bad_value in [("id", "t1_wrong"), ("title", ""), ("published", "yesterday"),
                         ("published", "2026-09-17T12:00:00")]:
    feed = ET.parse(fixture).getroot()
    feed.find(atom + "entry").find(atom + field).text = bad_value
    bad_inputs.append(ET.tostring(feed, encoding="unicode"))
feed = ET.parse(fixture).getroot()
feed.find(atom + "entry").find(atom + "link").set("href", "https://example.com/not-a-post")
bad_inputs.append(ET.tostring(feed, encoding="unicode"))
bad_inputs.append('<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>')
bad_inputs.append('<feed xmlns="http://www.w3.org/2005/Atom"><entry xmlns=""/></feed>')

for index, source in enumerate(bad_inputs):
    input_path = workdir / f"bad-{index}.atom"
    output_path = workdir / "unchanged.json"
    input_path.write_text(source)
    output_path.write_text("previous cache")
    result = subprocess.run([sys.executable, parser, str(input_path), str(output_path)],
                            capture_output=True, text=True)
    assert result.returncode != 0, f"invalid feed {index} was accepted"
    assert "Invalid RSS feed" in result.stderr
    assert output_path.read_text() == "previous cache", "invalid feed overwrote cache"
PY

echo "test_rss_parser: all assertions passed"
