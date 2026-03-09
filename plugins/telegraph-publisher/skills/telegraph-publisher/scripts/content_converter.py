#!/usr/bin/env python3
"""Convert HTML fragment to Telegraph Node JSON format.

Usage:
  echo '<p>Hello <b>world</b></p>' | python3 content_converter.py
  python3 content_converter.py < article.html
  python3 content_converter.py --check-size    # reads Node JSON from stdin, prints byte size
  python3 content_converter.py --split --output-dir DIR  # reads Node JSON from stdin, splits into parts

Supported Telegraph tags (output whitelist):
  a, aside, b, blockquote, br, code, em, figcaption, figure,
  h3, h4, hr, i, iframe, img, li, ol, p, pre, s, strong, u, ul, video

Special input handling:
  table, thead, tr, th, td -> converted to a monospace preformatted table

Unsupported tags are stripped (children preserved).
Only href and src attributes are kept.
"""

import json
import re
import sys
import os
from html.parser import HTMLParser

# Tags that Telegraph API accepts
ALLOWED_TAGS = frozenset([
    'a', 'aside', 'b', 'blockquote', 'br', 'code', 'em', 'figcaption',
    'figure', 'h3', 'h4', 'hr', 'i', 'iframe', 'img', 'li', 'ol',
    'p', 'pre', 's', 'strong', 'u', 'ul', 'video'
])

# Only these attributes are allowed by Telegraph
ALLOWED_ATTRS = frozenset(['href', 'src'])

# Tags that are block-level (for splitting)
BLOCK_TAGS = frozenset(['h3', 'h4', 'p', 'blockquote', 'pre', 'ul', 'ol', 'figure', 'hr', 'aside'])

# Void elements (no closing tag)
VOID_TAGS = frozenset(['br', 'hr', 'img'])

# Size limit in bytes (60KB threshold, Telegraph limit is 64KB)
SIZE_LIMIT = 61440


def format_table_mono(rows, header_idx=-1):
    """Format table rows as a monospace table using box-drawing chars."""
    if not rows:
        return ""

    n_cols = max(len(row) for row in rows)
    widths = [0] * n_cols

    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell.strip()))

    widths = [max(w, 1) for w in widths]

    def sep(left, mid, right, fill='-'):
        if fill == '-':
            fill = '─'
        return left + mid.join(fill * (w + 2) for w in widths) + right

    lines = [sep('┌', '┬', '┐')]
    for idx, row in enumerate(rows):
        cells = []
        for i in range(n_cols):
            val = row[i].strip() if i < len(row) else ''
            cells.append(f" {val:<{widths[i]}} ")
        lines.append('│' + '│'.join(cells) + '│')
        if idx == header_idx:
            lines.append(sep('├', '┼', '┤'))
    lines.append(sep('└', '┴', '┘'))
    return '\n'.join(lines)


class TelegraphHTMLParser(HTMLParser):
    """Parse HTML fragment into Telegraph Node array."""

    def __init__(self):
        super().__init__()
        self.result = []
        self.stack = []  # stack of (tag_or_none, children_list)
        self.stack.append((None, self.result))
        self._in_table = False
        self._table_rows = []
        self._current_row = []
        self._current_cell = []
        self._header_row_idx = -1
        self._in_thead = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == 'table':
            self._in_table = True
            self._table_rows = []
            self._current_row = []
            self._current_cell = []
            self._header_row_idx = -1
            self._in_thead = False
            return

        if self._in_table:
            if tag == 'thead':
                self._in_thead = True
            elif tag == 'tr':
                self._current_row = []
            elif tag in ('td', 'th'):
                self._current_cell = []
                if tag == 'th' or self._in_thead:
                    self._header_row_idx = len(self._table_rows)
            return

        if tag in ALLOWED_TAGS:
            node = {'tag': tag}
            filtered_attrs = {}
            for k, v in attrs:
                if k.lower() in ALLOWED_ATTRS and v:
                    # Normalize YouTube URLs in iframe src
                    if k.lower() == 'src' and tag == 'iframe':
                        v = normalize_youtube_embed(v)
                    filtered_attrs[k.lower()] = v
            if filtered_attrs:
                node['attrs'] = filtered_attrs
            if tag not in VOID_TAGS:
                node['children'] = []
                self.stack.append((tag, node['children']))
            # Add to parent's children
            _, parent_children = self.stack[-1] if tag in VOID_TAGS else self.stack[-2]
            parent_children.append(node)
        else:
            # Unsupported tag: skip tag, children go to parent
            pass

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == 'table' and self._in_table:
            self._in_table = False
            if self._table_rows:
                _, parent_children = self.stack[-1]
                parent_children.append({
                    'tag': 'pre',
                    'children': [format_table_mono(self._table_rows, self._header_row_idx)]
                })
            self._table_rows = []
            self._current_row = []
            self._current_cell = []
            self._header_row_idx = -1
            self._in_thead = False
            return

        if self._in_table:
            if tag == 'thead':
                self._in_thead = False
            elif tag in ('td', 'th'):
                self._current_row.append(''.join(self._current_cell))
                self._current_cell = []
            elif tag == 'tr':
                if self._current_row:
                    self._table_rows.append(self._current_row)
                self._current_row = []
            return

        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            # Pop from stack if matching
            if self.stack and self.stack[-1][0] == tag:
                self.stack.pop()

    def handle_data(self, data):
        if not data.strip() and not data:
            return
        if self._in_table:
            if data:
                self._current_cell.append(data)
            return
        if data:
            _, parent_children = self.stack[-1]
            parent_children.append(data)

    def handle_entityref(self, name):
        from html import unescape
        char = unescape(f'&{name};')
        if self._in_table:
            self._current_cell.append(char)
            return
        _, parent_children = self.stack[-1]
        parent_children.append(char)

    def handle_charref(self, name):
        from html import unescape
        char = unescape(f'&#{name};')
        if self._in_table:
            self._current_cell.append(char)
            return
        _, parent_children = self.stack[-1]
        parent_children.append(char)


def normalize_youtube_embed(url):
    """Normalize YouTube URL to embed format."""
    # Already embed format
    if '/embed/' in url:
        return url

    # youtube.com/watch?v=ID or youtu.be/ID
    video_id = None
    m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
    if m:
        video_id = m.group(1)
    else:
        m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
        if m:
            video_id = m.group(1)

    if video_id:
        return f'https://www.youtube.com/embed/{video_id}'

    return url


def html_to_nodes(html_str):
    """Convert HTML string to Telegraph Node array."""
    parser = TelegraphHTMLParser()
    parser.feed(html_str)
    return clean_nodes(parser.result)


def clean_nodes(nodes):
    """Remove empty text nodes and empty elements."""
    cleaned = []
    for node in nodes:
        if isinstance(node, str):
            # Skip whitespace-only strings
            if not node.strip():
                continue
            cleaned.append(node)
        elif isinstance(node, dict):
            if 'children' in node:
                node['children'] = clean_nodes(node['children'])
                # Remove block elements with no meaningful children
                if not node['children'] and node['tag'] not in VOID_TAGS:
                    if node['tag'] in BLOCK_TAGS:
                        continue
                    # Remove empty inline containers too
                    del node['children']
            cleaned.append(node)
    return cleaned


def serialize_size(nodes):
    """Return UTF-8 byte size of serialized Node JSON."""
    return len(json.dumps(nodes, ensure_ascii=False).encode('utf-8'))


def split_nodes(nodes, max_size=SIZE_LIMIT):
    """Split Node array into parts, each under max_size bytes.

    Strategy:
    1. Split by top-level h3 boundaries (sections)
    2. If a section exceeds limit, split by block-level elements
    3. If a single element exceeds limit, raise error
    """
    # First, try splitting by h3 sections
    sections = []
    current_section = []

    for node in nodes:
        if isinstance(node, dict) and node.get('tag') == 'h3' and current_section:
            sections.append(current_section)
            current_section = [node]
        else:
            current_section.append(node)
    if current_section:
        sections.append(current_section)

    # Now pack sections into parts respecting size limit
    parts = []
    current_part = []
    current_size = 2  # for [ ]

    for section in sections:
        section_size = serialize_size(section)

        if section_size > max_size:
            # Section too large — split by individual block elements
            if current_part:
                parts.append(current_part)
                current_part = []
                current_size = 2

            for element in section:
                elem_size = serialize_size([element])
                if elem_size > max_size:
                    tag = element.get('tag', 'text') if isinstance(element, dict) else 'text'
                    print(f"Error: Single element too large ({elem_size} bytes): <{tag}>. Split manually.",
                          file=sys.stderr)
                    sys.exit(1)

                if current_size + elem_size > max_size:
                    if current_part:
                        parts.append(current_part)
                    current_part = [element]
                    current_size = 2 + elem_size
                else:
                    current_part.append(element)
                    current_size += elem_size
        else:
            if current_size + section_size > max_size:
                if current_part:
                    parts.append(current_part)
                current_part = list(section)
                current_size = 2 + section_size
            else:
                current_part.extend(section)
                current_size += section_size

    if current_part:
        parts.append(current_part)

    return parts


def main():
    args = sys.argv[1:]

    if '--check-size' in args:
        data = sys.stdin.read()
        nodes = json.loads(data)
        print(serialize_size(nodes))
        return

    if '--split' in args:
        output_dir = '.'
        if '--output-dir' in args:
            idx = args.index('--output-dir')
            output_dir = args[idx + 1]

        data = sys.stdin.read()
        nodes = json.loads(data)
        parts = split_nodes(nodes)

        os.makedirs(output_dir, exist_ok=True)
        for i, part in enumerate(parts, 1):
            path = os.path.join(output_dir, f'part_{i}.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(part, f, ensure_ascii=False)
            size = serialize_size(part)
            print(f"part_{i}.json: {size} bytes", file=sys.stderr)

        print(len(parts))
        return

    # Default: convert HTML from stdin to Node JSON
    html_input = sys.stdin.read()
    nodes = html_to_nodes(html_input)
    print(json.dumps(nodes, ensure_ascii=False))


if __name__ == '__main__':
    main()
