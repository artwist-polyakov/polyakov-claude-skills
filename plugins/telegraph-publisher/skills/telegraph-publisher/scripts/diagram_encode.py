#!/usr/bin/env python3
"""Encode diagram source for PlantUML/Mermaid public rendering servers.

Usage:
  python3 diagram_encode.py plantuml < diagram.puml
  python3 diagram_encode.py mermaid < diagram.mmd
  echo "graph LR; A-->B" | python3 diagram_encode.py mermaid

Output: full render URL

PlantUML: https://www.plantuml.com/plantuml/png/ENCODED
Mermaid:  https://mermaid.ink/img/pako:ENCODED
"""

import base64
import json
import sys
import zlib


# --------------- PlantUML encoding ---------------

# PlantUML uses a custom 6-bit encoding with a specific alphabet
PLANTUML_ALPHABET = (
    '0123456789'
    'ABCDEFGHIJ'
    'KLMNOPQRST'
    'UVWXYZ'
    'abcdefghij'
    'klmnopqrst'
    'uvwxyz'
    '-_'
)


def _plantuml_encode_6bit(b):
    """Encode a single 6-bit value to PlantUML alphabet char."""
    if 0 <= b < len(PLANTUML_ALPHABET):
        return PLANTUML_ALPHABET[b]
    return '?'


def _plantuml_encode_3bytes(b1, b2, b3):
    """Encode 3 bytes into 4 PlantUML chars."""
    c1 = b1 >> 2
    c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
    c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
    c4 = b3 & 0x3F
    return (
        _plantuml_encode_6bit(c1)
        + _plantuml_encode_6bit(c2)
        + _plantuml_encode_6bit(c3)
        + _plantuml_encode_6bit(c4)
    )


def encode_plantuml(text):
    """Encode PlantUML source to URL-safe string for plantuml.com."""
    data = zlib.compress(text.encode('utf-8'), 9)
    # Strip zlib header (2 bytes) and checksum (4 bytes) for raw deflate
    data = data[2:-4]

    result = ''
    i = 0
    while i < len(data):
        if i + 2 < len(data):
            result += _plantuml_encode_3bytes(data[i], data[i + 1], data[i + 2])
        elif i + 1 < len(data):
            result += _plantuml_encode_3bytes(data[i], data[i + 1], 0)
        else:
            result += _plantuml_encode_3bytes(data[i], 0, 0)
        i += 3

    return f'https://www.plantuml.com/plantuml/png/{result}'


# --------------- Mermaid encoding ---------------

def encode_mermaid(text):
    """Encode Mermaid source to URL for mermaid.ink.

    Format: https://mermaid.ink/img/pako:PAYLOAD
    PAYLOAD = URL-safe base64 (no padding) of raw deflate of compact JSON.
    """
    payload = json.dumps(
        {'code': text, 'mermaid': {'theme': 'default'}},
        separators=(',', ':'),
        ensure_ascii=False
    )

    # pako.deflate output (full zlib with header+checksum, not raw deflate)
    compressed = zlib.compress(payload.encode('utf-8'), 9)

    # URL-safe base64 without padding
    encoded = base64.urlsafe_b64encode(compressed).rstrip(b'=').decode('ascii')

    return f'https://mermaid.ink/img/pako:{encoded}'


# --------------- CLI ---------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('plantuml', 'mermaid'):
        print('Usage: python3 diagram_encode.py plantuml|mermaid < source', file=sys.stderr)
        sys.exit(1)

    diagram_type = sys.argv[1]
    source = sys.stdin.read()

    if not source.strip():
        print('Error: Empty diagram source.', file=sys.stderr)
        sys.exit(1)

    if diagram_type == 'plantuml':
        print(encode_plantuml(source))
    else:
        print(encode_mermaid(source))


if __name__ == '__main__':
    main()
