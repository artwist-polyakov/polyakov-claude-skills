# Telegraph Content Format Reference

## Node Format

Telegraph API content is a JSON array of Node objects.

A Node is either:
- A **string** (text content)
- A **NodeElement** object:
  ```json
  {
    "tag": "p",
    "attrs": {"href": "https://...", "src": "https://..."},
    "children": ["text", {"tag": "b", "children": ["bold"]}]
  }
  ```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | yes | HTML tag name |
| `attrs` | object | no | Only `href` and `src` allowed |
| `children` | array | no | Child Nodes (strings or NodeElements) |

## Supported Tags

| Tag | Attrs | Description |
|-----|-------|-------------|
| `a` | `href` | Hyperlink |
| `aside` | — | Aside/callout block |
| `b` | — | Bold |
| `blockquote` | — | Block quote |
| `br` | — | Line break (void element) |
| `code` | — | Inline code |
| `em` | — | Emphasis/italic |
| `figcaption` | — | Figure caption |
| `figure` | — | Container for img/iframe/video |
| `h3` | — | Heading level 3 |
| `h4` | — | Heading level 4 |
| `hr` | — | Horizontal rule (void element) |
| `i` | — | Italic |
| `iframe` | `src` | Embedded content (YouTube, etc.) |
| `img` | `src` | Image (void element) |
| `li` | — | List item |
| `ol` | — | Ordered list |
| `p` | — | Paragraph |
| `pre` | — | Preformatted/code block |
| `s` | — | Strikethrough |
| `strong` | — | Strong emphasis |
| `u` | — | Underline |
| `ul` | — | Unordered list |
| `video` | `src` | Video |

## Size Limit

- Maximum content size: **64 KB** (serialized UTF-8 JSON)
- The `content_converter.py` uses 60 KB threshold for auto-split (4 KB safety margin)

## Examples

### Simple paragraph
```json
[{"tag": "p", "children": ["Hello, world!"]}]
```

### Formatted text
```json
[{"tag": "p", "children": ["This is ", {"tag": "b", "children": ["bold"]}, " and ", {"tag": "i", "children": ["italic"]}]}]
```

### Image with caption
```json
[{"tag": "figure", "children": [
  {"tag": "img", "attrs": {"src": "https://example.com/photo.jpg"}},
  {"tag": "figcaption", "children": ["Photo description"]}
]}]
```

### YouTube embed
```json
[{"tag": "figure", "children": [
  {"tag": "iframe", "attrs": {"src": "https://www.youtube.com/embed/dQw4w9WgXcQ"}}
]}]
```

### Code block
```json
[{"tag": "pre", "children": [{"tag": "code", "children": ["def hello():\n    print('world')"]}]}]
```

### Complete article
```json
[
  {"tag": "h3", "children": ["Introduction"]},
  {"tag": "p", "children": ["This article covers..."]},
  {"tag": "figure", "children": [
    {"tag": "img", "attrs": {"src": "https://example.com/diagram.png"}},
    {"tag": "figcaption", "children": ["Architecture diagram"]}
  ]},
  {"tag": "h3", "children": ["Details"]},
  {"tag": "p", "children": ["The implementation uses ", {"tag": "code", "children": ["async/await"]}, " pattern."]}
]
```

## HTML to Node Mapping

The `content_converter.py` script converts HTML fragments to Node JSON:

```
<h3>Title</h3>              → {"tag": "h3", "children": ["Title"]}
<p>Text <b>bold</b></p>     → {"tag": "p", "children": ["Text ", {"tag": "b", "children": ["bold"]}]}
<img src="url">             → {"tag": "img", "attrs": {"src": "url"}}
<a href="url">link</a>      → {"tag": "a", "attrs": {"href": "url"}, "children": ["link"]}
```

Unsupported tags (e.g., `<div>`, `<span>`, `<h1>`, `<h2>`) are stripped — their children are preserved and moved to the parent element.

## Table Handling

Telegraph does not support native table nodes.

This skill accepts input HTML tables and converts them to a monospace `pre` block:

```
<table>
  <thead>
    <tr><th>Домен</th><th>Расход, руб.</th></tr>
  </thead>
  <tbody>
    <tr><td>metallik.ru</td><td>82 900</td></tr>
    <tr><td>mir-shtaketnika.ru</td><td>38 367</td></tr>
  </tbody>
</table>
```

becomes a `pre` node with aligned box-drawing output, for example:

```json
[{
  "tag": "pre",
  "children": ["┌────────────────────┬──────────────┐\n│ Домен              │ Расход, руб. │\n├────────────────────┼──────────────┤\n│ metallik.ru        │ 82 900       │\n│ mir-shtaketnika.ru │ 38 367       │\n└────────────────────┴──────────────┘"]
}]
```

Recommended usage:
- exact numbers and labels
- short comparison tables
- spend/domain breakdowns

Not recommended:
- wide tables with many columns
- dense financial statements
- anything that should become a chart instead of a table

Mobile note:
- `pre` tables are desktop-friendly but degrade quickly on narrow mobile screens
- if the table has many columns, long labels, or status text, prefer:
  - a diagram for the visual pattern
  - a bullet/card layout for exact values
  - or a narrowed 2-column table plus a short textual summary

## Platform Limitations

### No page deletion
Telegraph does not support deleting published pages — neither via API nor via browser. Once published, a page exists permanently at its URL.

**Workaround: page recycling.** You can overwrite a page's title and content via `editPage`:
```bash
# "Delete" by clearing content
sh scripts/edit_page.sh --path "Old-Page-03-09" --title "." --html "<p>.</p>"

# Later, reuse for new content
sh scripts/edit_page.sh --path "Old-Page-03-09" --title "New Article" --html-file article.html
```

This is useful for:
- Cleaning up test/draft pages
- Reusing page URLs for updated content
- "Unpublishing" by replacing with minimal content

**Note**: The original URL slug (e.g., `Old-Page-03-09`) cannot be changed. Only title and content are editable.

### No page URL customization
Page URL paths are auto-generated from the title at creation time. They cannot be changed later, even if you edit the title.

### Account token in browser cookies
The API `access_token` is stored as an httpOnly cookie named `tph_token` on `telegra.ph`. This is the same token used by `createAccount` API. You can extract it from Chrome DevTools → Application → Cookies (see [config/README.md](../config/README.md)).
