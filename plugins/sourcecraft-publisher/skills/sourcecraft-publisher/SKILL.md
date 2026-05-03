---
name: sourcecraft-publisher
description: Publish static page artifacts to SourceCraft Sites (Yandex infrastructure, works in Russia), with advisory image optimization and an original-image path. Use when a static page/React artifact needs to be deployed to SourceCraft under YYYY/YYYY-MM/page-slug directory layout.
---

# SourceCraft Publisher

Publish already-built static artifacts to a SourceCraft Sites repository. Works from Russia (Yandex infrastructure).

This skill is the **deployment/output layer** for page artifacts created by other skills (e.g. `telegram-channel-parser` digest).

## Required repository layout

Every published artifact must go into this path shape inside the target repo:

`<year>/<year>-<month>/<page-slug>/`

Example:
- `2026/2026-03/ai-digest-week/`

Never publish flat at repo root. Never skip the year or year-month nesting.

## What this skill expects

Input should already exist as one of these:
- a folder of built static files
- a single HTML artifact plus local assets
- a small static site ready to serve from a subdirectory

This skill does not do frontend design work. It packages and publishes what already exists.

## Config

Read `config/README.md` for required environment variables and token setup.

```bash
cp config/.env.example config/.env
```

Required: `SOURCECRAFT_TOKEN`, `SOURCECRAFT_REPO`, `SOURCECRAFT_SITE_URL`.

## Workflow

1. Determine the publish date bucket:
   - year = `YYYY`
   - year-month = `YYYY-MM`
2. Create or update target directory:
   - `<year>/<year-month>/<page-slug>/`
3. Copy artifact files into that directory
4. Verify there is an entrypoint (`index.html` normally)
5. Ensure `.sourcecraft/sites.yaml` exists in repo root
6. Commit and force-push to the SourceCraft repo
7. Return the final public URL

## Publishing command

```bash
python3 scripts/publish_static.py --source <dir-or-html> --slug <name> [--date YYYY-MM-DD] [--image-max-kb 500]
```

The script:
- Clones the SourceCraft repo (shallow)
- Ensures `.sourcecraft/sites.yaml` config exists
- Copies artifact into `YYYY/YYYY-MM/slug/`
- Optimizes oversized `.jpg`, `.jpeg`, `.png`, and `.webp` images before commit
- Commits and force-pushes
- Prints the final public URL

## Image optimization

Image optimization is a recommendation, not a hard requirement. By default, the publishing script tries to keep each raster image under `500KB` by stripping metadata, recompressing, and resizing long edges when needed.

Use `--image-max-kb <kb>` for a different target.
Use `--keep-large-images` or `--image-max-kb 0` when the user explicitly needs original-size images (for example, a full-resolution infographic, map, or downloadable media asset).

The original `--source` artifact is not changed. Optimization happens only after files are copied into the publish repo target directory. If Pillow/uv is unavailable or optimization fails, continue publishing originals and mention the warning.

Manual console run for a prepared artifact:

```bash
uv run --with pillow python3 scripts/optimize_images.py <artifact-dir> --max-kb 500
```

## Slug rules

- use lowercase letters, digits, and hyphens
- keep it short and descriptive
- avoid spaces, underscores, Cyrillic

## URL contract

Always return the final public URL:
`<SOURCECRAFT_SITE_URL>/<year>/<year>-<month>/<page-slug>/`

## Pre-publish validation

Before pushing, verify:
- artifact has `index.html` entrypoint
- oversized raster images are optimized when practical, unless original-size sharing is intentional
- no absolute local paths in HTML/CSS
- no secrets in files
- all local assets reachable from the target folder

## Safety rules

- do not delete unrelated directories in the repo
- only replace contents of the target page directory
- if overwriting, say so in the result
- do not expose the token in output, logs, or committed files

## References

Read if needed:
- `references/publish-checklist.md` — operational checklist before pushing
