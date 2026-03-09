# Image Workflows for Telegraph

## Primary Workflow: GitHub Assets + jsDelivr

Preferred setup for permanent media in Telegraph:
- store assets in a dedicated **public GitHub repo**
- publish them through **jsDelivr**
- use those URLs in Telegraph HTML

Security recommendation:
- use a **separate repo only for pictures/assets**
- use a **separate fine-grained token** that can write only to that repo

Why this is the default:
- URLs are stable and do not depend on Telegraph's unofficial upload endpoint
- the workflow is reproducible in shell scripts
- assets can be grouped per page and deleted later
- the repo doubles as an auditable media store

### Recommended structure

Use one public repo only for Telegraph media, for example:
`your-org/telegraph-assets`

Do not mix this with your main application repo unless you intentionally want media history there.

Recommended layout:
```text
pages/
  <telegraph_path>/
    hero.webp
    diagram-01.png
    diagram-02.png
manifests/
  <telegraph_path>.json
```

Serve files through jsDelivr using commit-pinned URLs:
```text
https://cdn.jsdelivr.net/gh/<owner>/<repo>@<commit>/pages/<telegraph_path>/hero.webp
```

Use commit-pinned URLs, not branch URLs, for published pages.

### Minimal commands

Upload one asset:
```bash
sh scripts/github_upload.sh \
  --file ./images/hero.webp \
  --page-path my-telegraph-page-path
```

Upload with explicit filename:
```bash
sh scripts/github_upload.sh \
  --file ./images/diagram.png \
  --page-path my-telegraph-page-path \
  --name diagram-01.png
```

Cleanup all assets for a page:
```bash
sh scripts/github_delete_page_assets.sh \
  --page-path my-telegraph-page-path
```

## Why connect GitHub at all

Without GitHub-backed hosting, the fallback is `upload.sh`, which relies on Telegraph's unofficial upload endpoint and behaves unreliably in real environments.

In practice, GitHub gives the skill something Telegraph itself does not:
- permanent asset hosting
- predictable cleanup
- asset grouping by page
- better control over revisions

## Asset Lifecycle

### Recommended: create page path first

Do not derive cleanup only from the page title.
Titles can change. The stable identifier is the Telegraph `path`.

Recommended two-pass publishing flow:
1. Create a stub Telegraph page to obtain its final `path`
2. Upload assets to GitHub under `pages/<telegraph_path>/...`
3. Build final HTML with jsDelivr URLs
4. Edit the page with final content
5. Write `manifests/<telegraph_path>.json` with:
   - GitHub asset paths
   - blob SHAs
   - optional metadata (title, created_at, source files)

This makes later cleanup deterministic.

### If stub-first is inconvenient

Fallback flow:
1. Generate a temporary page key
2. Upload assets under that key
3. Create the Telegraph page
4. Persist a manifest mapping:
   `telegraph_path -> github asset paths`

This is acceptable, but path-first is cleaner.

## Cascade Delete Strategy

Telegraph does not give you a clean hard-delete flow you should rely on, so cleanup should be driven by your own manifest.

When a page is removed, tombstoned, or explicitly cleaned up:
1. Take the Telegraph `path`
2. Read `manifests/<telegraph_path>.json`
3. Delete listed GitHub files using stored blob SHAs
4. Delete the manifest itself
5. Optionally remove the empty `pages/<telegraph_path>/` directory marker if you keep one

Do **not** try to infer all assets from the page title alone.
Use the manifest as the source of truth.

## Alternative: Already-hosted Images

If images are already on a public URL, use directly:
```html
<figure>
  <img src="https://cdn.example.com/photo.jpg">
  <figcaption>Caption</figcaption>
</figure>
```

## Legacy Fallback: upload.sh

If GitHub-backed hosting is not available, `upload.sh` can still be tried as a last resort:

```bash
URL=$(sh scripts/upload.sh --file /path/to/photo.jpg)
```

Treat it as emergency fallback only.

### Supported formats
- Images: jpg, jpeg, png, gif, webp
- Video: mp4
- Max file size: 5MB

### Limitations
- Unofficial endpoint
- No API guarantees
- May fail behind HTTPS-intercepting proxies/VPNs
- Can fail even when the rest of Telegraph API works

## Working with fal-ai-image

If using the `fal-ai-image` skill to generate illustrations:

1. Generated images come with **temporary URLs** (~1 hour expiry)
2. Download the image locally first
3. Upload to GitHub assets repo for a permanent URL
4. Use the jsDelivr URL in your article

```bash
# After fal-ai-image generates image and saves to local file:
URL="https://cdn.jsdelivr.net/gh/<owner>/<repo>@<commit>/pages/<telegraph_path>/generated_hero.png"
# Use $URL in your HTML content
```

## Best Practices

- Use **HTTPS** URLs (Telegraph serves over HTTPS, mixed content may be blocked)
- Prefer **PNG** for diagrams/screenshots, **JPEG** for photos
- Keep filenames deterministic inside each page folder
- Keep a manifest per Telegraph page
- Always wrap images in `<figure>` for proper Telegraph rendering
- Add `<figcaption>` for accessibility and context
