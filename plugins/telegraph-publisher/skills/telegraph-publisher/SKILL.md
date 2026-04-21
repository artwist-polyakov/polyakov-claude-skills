---
name: telegraph-publisher
description: "Publishes content to Telegraph (telegra.ph) via API. Creates and edits pages with images, YouTube embeds, and diagrams. Converts HTML to Telegraph Node format, auto-splits articles exceeding 64KB, and supports permanent media hosting via GitHub + jsDelivr. Use when the user wants to publish a blog post, article, or page to Telegraph, create or edit a telegra.ph page, or embed media in Telegraph content. Triggers: telegraph, telegra.ph, publish article, blog post, create page, telegraph publish, опубликовать в telegraph, создать статью, статья в телеграф."
---

# telegraph-publisher

Publish content to Telegraph via API with media support.
Best for: articles, research reports, documentation, illustrated content.

## STOP — Read Before Acting

- **DO NOT** pass raw markdown — convert to HTML fragment first (Telegraph API accepts Node JSON, the converter accepts HTML)
- **DO NOT** pass content larger than 64KB without using auto-split — the script handles this automatically
- **DO NOT** hardcode access tokens — use `config/.env`
- **DO NOT** skip account setup — run `create_account.sh` first if no token exists

## Quick Start

```
No token?     → sh scripts/create_account.sh --name "Name"
Have token?   → Save to config/.env
Publish page? → sh scripts/create_page.sh --title "Title" --html "<p>Content</p>"
Edit page?    → sh scripts/edit_page.sh --path "Path-03-09" --title "Title" --html "<p>New</p>"
List pages?   → sh scripts/list_pages.sh
Account info? → sh scripts/account_info.sh
Permanent media? → sh scripts/github_upload.sh --file hero.webp --page-path page-path
```

## Account & Ownership

Telegraph accounts are API-only (no password/email). Key concepts:

1. `create_account.sh` generates `access_token` + one-time `auth_url`
2. Open `auth_url` in browser to bind API account to browser session
3. Pages belong to the account whose token was used in `createPage`
4. After browser binding: pages visible at telegra.ph, editable both via browser and API
5. Use `--revoke` to rotate token if compromised

See [config/README.md](config/README.md) for full ownership model.

## Compatibility

Scripts are POSIX sh compatible — work in cloud sandboxes (`/bin/sh`) and locally.
Python scripts use stdlib only (`html.parser`, `json`, `sys`).

## Config

Requires `TELEGRAPH_ACCESS_TOKEN` in `config/.env` or environment.

For permanent media hosting, prefer a separate public GitHub repo + jsDelivr CDN.
Reason: Telegraph's unofficial upload endpoint is unstable and should not be the default publishing path.

### GitHub Setup (recommended)

The agent should assume this is the default permanent media backend.

Required GitHub config:
```bash
GITHUB_TOKEN=ghp_...
GITHUB_ASSETS_REPO=owner/repo
GITHUB_ASSETS_BRANCH=main
GITHUB_ASSETS_BASE_DIR=pages
GITHUB_MANIFESTS_DIR=manifests
```

Recommended setup:
1. Create a **separate public GitHub repo only for Telegraph media**
2. Create a **fine-grained PAT only for that repo**
3. Grant only:
   - `Contents`: `Read and write`
4. Save token and repo to `config/.env`

Why this matters:
- permanent asset URLs via jsDelivr
- lower blast radius if token leaks
- no dependency on Telegraph's glitchy upload endpoint
- deterministic cleanup through page manifests

Agent rule:
- if local images/diagrams need permanent hosting and GitHub config exists, use GitHub-backed media workflow by default
- use `upload.sh` only as a legacy fallback

## Content Format

Telegraph API accepts an array of Node objects. This skill converts **HTML fragments** to Node JSON automatically.

Supported HTML tags (Telegraph API whitelist):
`a`, `aside`, `b`, `blockquote`, `br`, `code`, `em`, `figcaption`, `figure`, `h3`, `h4`, `hr`, `i`, `iframe`, `img`, `li`, `ol`, `p`, `pre`, `s`, `strong`, `u`, `ul`, `video`

Only `href` and `src` attributes are preserved. Unsupported tags are stripped (children kept).

Special case:
- input HTML tables (`table`, `thead`, `tr`, `th`, `td`) are converted into a monospace `pre` block
- use this for compact comparisons, domain spend breakdowns, KPI matrices, and similar tabular fragments
- do not force small tables into diagrams unless the user explicitly wants a visual chart instead of exact values

See [references/CONTENT_FORMAT.md](references/CONTENT_FORMAT.md) for Node format details.

## Scripts

### create_account.sh
```bash
sh scripts/create_account.sh --name "Author Name" [--author-url "https://..."]
sh scripts/create_account.sh --revoke  # rotate token
```

### account_info.sh
```bash
sh scripts/account_info.sh
sh scripts/account_info.sh --with-auth-url  # include auth_url in output
```

### create_page.sh
```bash
# From HTML string
sh scripts/create_page.sh --title "Article" --html "<h3>Hello</h3><p>World</p>"

# From HTML file
sh scripts/create_page.sh --title "Article" --html-file article.html

# From pre-built Node JSON
sh scripts/create_page.sh --title "Article" --content-file nodes.json

# With author info
sh scripts/create_page.sh --title "Article" --html-file a.html --author-name "Name"
```

| Param | Required | Description |
|-------|----------|-------------|
| `--title` | yes | Page title (1-256 chars) |
| `--html` | one of three | Inline HTML string |
| `--html-file` | one of three | Path to HTML file |
| `--content-file` | one of three | Path to Node JSON file |
| `--author-name` | no | Author name (0-128 chars) |
| `--author-url` | no | Author profile URL |

**Auto-split**: If content exceeds 60KB, automatically splits into multiple pages with an index page linking to parts.

### edit_page.sh
```bash
sh scripts/edit_page.sh --path "Page-Title-03-09" --title "Updated Title" --html "<p>New content</p>"
```

| Param | Required | Description |
|-------|----------|-------------|
| `--path` | yes | Page path (from URL or create output) |
| `--title` | yes | Page title |
| `--html` / `--html-file` / `--content-file` | yes | New content |
| `--author-name` | no | Author name |
| `--author-url` | no | Author URL |

### list_pages.sh
```bash
sh scripts/list_pages.sh
sh scripts/list_pages.sh --offset 0 --limit 20
```

### github_upload.sh
Upload local media to the GitHub assets repo and update page manifest:
```bash
sh scripts/github_upload.sh --file ./hero.webp --page-path my-page-path
sh scripts/github_upload.sh --file ./diagram.png --page-path my-page-path --name diagram-01.png
```

| Param | Required | Description |
|-------|----------|-------------|
| `--file` | yes | Local asset file |
| `--page-path` | yes | Telegraph page path used as manifest/asset key |
| `--name` | no | Override stored filename in GitHub |

Output: commit-pinned jsDelivr URL.

Manifest behavior:
- assets go under `pages/<telegraph_path>/...`
- manifest goes under `manifests/<telegraph_path>.json`
- manifest stores asset paths and SHAs for later cleanup

### github_delete_page_assets.sh
Delete all GitHub-backed assets for a page using its manifest:
```bash
sh scripts/github_delete_page_assets.sh --page-path my-page-path
```

| Param | Required | Description |
|-------|----------|-------------|
| `--page-path` | yes | Telegraph page path |

Cleanup rule:
- delete by manifest, not by title guessing
- use Telegraph `path` as the stable page identifier

### upload.sh
Legacy fallback for local image/video upload to Telegraph:
```bash
# Best-effort only
URL=$(sh scripts/upload.sh --file /path/to/photo.jpg)

# Use in HTML
echo "<figure><img src=\"$URL\"><figcaption>My photo</figcaption></figure>"
```

| Param | Required | Description |
|-------|----------|-------------|
| `--file` | yes | Path to image/video (jpg, png, gif, webp, mp4; max 5MB) |
| `--insecure` | no | Skip SSL verification (for HTTPS-intercepting proxies/VPNs) |

**Note**: Uses unofficial `telegra.ph/upload` endpoint. Do not treat it as the primary workflow. Best-effort only — may fail behind corporate proxies/VPNs or without any obvious reason.

### render_diagram.sh
Render PlantUML/Mermaid diagrams via public servers:
```bash
# Get render URL (image on public server)
sh scripts/render_diagram.sh --type plantuml --file arch.puml

# Render + upload to GitHub-backed permanent media
sh scripts/render_diagram.sh --type mermaid --file flow.mmd --github-page-path my-page-path --github-name cohort.png

# Legacy fallback: render + upload via Telegraph upload
sh scripts/render_diagram.sh --type mermaid --file flow.mmd --upload
```

| Param | Required | Description |
|-------|----------|-------------|
| `--type` | yes | `plantuml` or `mermaid` |
| `--file` | yes | Path to diagram source file |
| `--github-page-path` | no | Upload rendered file to GitHub assets under this Telegraph path |
| `--github-name` | no | Override GitHub filename for rendered asset |
| `--upload` | no | Legacy fallback: download rendered PNG and upload to Telegraph |

**Privacy**: Diagram source is sent to plantuml.com / mermaid.ink. Do not use for confidential content.

### content_converter.py (internal)
```bash
# HTML → Node JSON
echo '<p>Hello <b>world</b></p>' | python3 scripts/content_converter.py

# Check serialized size (bytes)
cat nodes.json | python3 scripts/content_converter.py --check-size

# Split large content
cat nodes.json | python3 scripts/content_converter.py --split --output-dir /tmp/parts
```

## Media Support

### Images

Preferred: GitHub-backed media via `github_upload.sh` (see Config section for setup). Fallback: `upload.sh` (legacy, unstable endpoint).

Asset lifecycle:
1. Create draft/stub Telegraph page to get its `path`
2. Upload images to GitHub via `github_upload.sh --file img.webp --page-path <telegraph_path>`
3. Publish final content with jsDelivr URLs
4. On cleanup: `github_delete_page_assets.sh --page-path <telegraph_path>`

Always use Telegraph `path` (not title) as page identifier for manifests and cleanup.

See [references/IMAGE_WORKFLOWS.md](references/IMAGE_WORKFLOWS.md) for detailed workflows.

### YouTube Embeds
YouTube URLs are automatically normalized to embed format:
```html
<figure>
  <iframe src="https://www.youtube.com/watch?v=VIDEO_ID"></iframe>
</figure>
```
The converter transforms `watch?v=` and `youtu.be/` URLs to `/embed/` format.

See [references/YOUTUBE_EMBEDS.md](references/YOUTUBE_EMBEDS.md) for details.

### Diagrams
Render PlantUML/Mermaid diagrams via `render_diagram.sh`. Preferred: store in GitHub assets via `--github-page-path`. Legacy fallback: `--upload` to Telegraph.

```bash
# Preferred: render + GitHub upload
sh scripts/render_diagram.sh --type plantuml --file arch.puml --github-page-path my-page-path --github-name arch.png

# Legacy fallback
sh scripts/render_diagram.sh --type plantuml --file arch.puml --upload
```

See [references/DIAGRAMS.md](references/DIAGRAMS.md) for details and privacy considerations.

### Tables
Telegraph does not support HTML tables natively. Input `<table>` elements are auto-converted to monospace `pre` blocks. Prefer tables for exact values; prefer diagrams for trends/flows. On mobile, keep tables narrow (2 columns max) or use bullet lists instead.

See [references/CONTENT_FORMAT.md](references/CONTENT_FORMAT.md) for table vs diagram guidance and mobile-first rules.

## Optional: Illustrations with fal-ai-image

If the `fal-ai-image` skill is installed, you can generate illustrations before publishing:

1. Read fal-ai-image SKILL.md first
2. **Confirm budget with user** before generating (from $0.15/image)
3. Generate images, save URLs
4. Include URLs in HTML as `<figure><img src="URL"></figure>`
5. Publish via `create_page.sh`

See [references/FAL_AI_INTEGRATION.md](references/FAL_AI_INTEGRATION.md) for house style guide and prompt examples.

**Important**: telegraph-publisher works fully without fal-ai-image. This is an optional enhancement.

## Limitations (v1)

- Input: HTML fragments only (no markdown conversion)
- No caching of API responses
- Auto-split boundary: if a single HTML element exceeds 60KB, manual splitting required
- `upload.sh` uses unofficial Telegraph endpoint and should be treated as legacy fallback only
- Diagram rendering sends source to public servers (privacy consideration)
