# YouTube Embeds in Telegraph

## Supported Input Formats

The `content_converter.py` automatically normalizes these YouTube URL formats to embed:

| Input URL | Normalized to |
|-----------|--------------|
| `https://www.youtube.com/watch?v=VIDEO_ID` | `https://www.youtube.com/embed/VIDEO_ID` |
| `https://youtube.com/watch?v=VIDEO_ID` | `https://www.youtube.com/embed/VIDEO_ID` |
| `https://youtu.be/VIDEO_ID` | `https://www.youtube.com/embed/VIDEO_ID` |
| `https://www.youtube.com/embed/VIDEO_ID` | (unchanged) |

## How to Embed

Use `<iframe>` inside `<figure>`:

```html
<figure>
  <iframe src="https://www.youtube.com/watch?v=dQw4w9WgXcQ"></iframe>
  <figcaption>Optional video description</figcaption>
</figure>
```

The converter automatically transforms the `src` to embed format:
```json
{
  "tag": "figure",
  "children": [
    {"tag": "iframe", "attrs": {"src": "https://www.youtube.com/embed/dQw4w9WgXcQ"}},
    {"tag": "figcaption", "children": ["Optional video description"]}
  ]
}
```

## Notes

- Video ID is always 11 characters: `[a-zA-Z0-9_-]{11}`
- Only YouTube is auto-normalized. Other iframe sources pass through unchanged.
- Telegraph renders iframes with a fixed aspect ratio
- URL parameters (like `?t=120` for timestamps) on watch URLs are stripped during normalization. If you need a start time, use the embed parameter format: `https://www.youtube.com/embed/VIDEO_ID?start=120`

## Other Embeddable Services

Telegraph's `<iframe>` tag supports any embeddable URL. The converter passes non-YouTube iframe sources unchanged:

```html
<figure>
  <iframe src="https://www.example.com/embed/content"></iframe>
</figure>
```

Use this for Vimeo, Twitter embeds, or other services that provide embed URLs.
