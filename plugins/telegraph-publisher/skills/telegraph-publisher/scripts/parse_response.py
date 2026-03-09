#!/usr/bin/env python3
"""Parse Telegraph API JSON responses into human-readable output.

Usage:
  echo '{"ok":true,"result":{...}}' | python3 parse_response.py account_info
  echo '{"ok":true,"result":{...}}' | python3 parse_response.py page_list
"""

import json
import sys


def parse_account_info(data):
    """Format getAccountInfo response."""
    result = data.get('result', {})
    lines = ['=== Account Info ===']
    if 'short_name' in result:
        lines.append(f"Short name:  {result['short_name']}")
    if 'author_name' in result:
        lines.append(f"Author name: {result['author_name']}")
    if 'author_url' in result:
        lines.append(f"Author URL:  {result['author_url']}")
    if 'page_count' in result:
        lines.append(f"Page count:  {result['page_count']}")
    if 'auth_url' in result:
        lines.append(f"Auth URL:    {result['auth_url']}")
        lines.append("  (open in browser to bind account, valid 5 min)")
    return '\n'.join(lines)


def parse_page_list(data):
    """Format getPageList response as a table."""
    result = data.get('result', {})
    total = result.get('total_count', 0)
    pages = result.get('pages', [])

    lines = [f'=== Pages ({total} total) ===']
    if not pages:
        lines.append('No pages found.')
        return '\n'.join(lines)

    # Header
    lines.append(f"{'#':<4} {'Title':<50} {'URL':<40} {'Views':<8}")
    lines.append('-' * 102)

    for i, page in enumerate(pages, 1):
        title = page.get('title', '(no title)')
        if len(title) > 48:
            title = title[:45] + '...'
        url = page.get('url', '')
        views = page.get('views', 0)
        lines.append(f"{i:<4} {title:<50} {url:<40} {views:<8}")

    if len(pages) < total:
        lines.append(f"\n... showing {len(pages)} of {total}. Use --offset/--limit for more.")

    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: parse_response.py <command>", file=sys.stderr)
        print("Commands: account_info, page_list", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    data = json.loads(sys.stdin.read(), strict=False)

    if command == 'account_info':
        print(parse_account_info(data))
    elif command == 'page_list':
        print(parse_page_list(data))
    else:
        # Fallback: pretty-print JSON
        print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
