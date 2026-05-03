---
name: github-pages-publisher
description: Publish static page artifacts from the publisher workspace to a GitHub Pages repository using a fine-grained token, with advisory image optimization and an original-image path. Use when a React/static page artifact is already prepared and needs to be copied into the Pages repo under a strict year/year-month/page-slug directory layout, then committed and pushed, with a final public artifact URL returned.
---

# GitHub Pages Publisher

Publish already-built static artifacts to a GitHub Pages repository.

This skill is the **deployment/output layer** for page artifacts created by other skills.

## Required repository layout

Every published artifact must go into this path shape inside the target repo:

`<year>/<year>-<month>/<page-slug>/`

Example:
- `2026/2026-03/my-landing-page/`

Never publish flat at repo root.
Never skip the year or year-month nesting.

## What this skill expects

Input should already exist as one of these:
- a folder of built static files
- a single HTML artifact plus local assets
- a small static site ready to serve from a subdirectory

This skill should not do major frontend design work. It should package and publish what already exists.

## Default publishing rules

- preserve relative asset paths when possible
- prefer `index.html` as entrypoint inside the target page directory
- keep the output self-contained inside the page folder
- avoid breaking existing published pages
- if the same slug is republished, update the existing folder contents deliberately

## URL contract

Always return the final public URL of the artifact.

Assume GitHub Pages serves from the repo's configured Pages base URL.
The final URL should be:

`<pages-base-url>/<year>/<year>-<month>/<page-slug>/`

or, if needed explicitly:

`.../<page-slug>/index.html`

Prefer the clean directory URL when it resolves correctly.

## Workflow

1. Determine the publish date bucket:
   - year = `YYYY`
   - year-month = `YYYY-MM`
2. Create or update target directory:
   - `<year>/<year-month>/<page-slug>/`
3. Copy artifact files into that directory
4. Optimize oversized raster images when practical, unless original-size sharing is intentional
5. Verify there is an entrypoint (`index.html` normally)
6. Run pre-publish validation (see section above)
7. Commit and push to the Pages repo using the configured fine-grained token workflow
8. Return the final public URL

## Publishing command

```bash
python3 scripts/publish_static.py --source <dir-or-html> --slug <name> [--date YYYY-MM-DD] [--image-max-kb 500]
```

The script copies the artifact into `YYYY/YYYY-MM/slug/`, optimizes oversized `.jpg`, `.jpeg`, `.png`, and `.webp` images, commits, pushes, and prints the final public URL.

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
- avoid spaces, underscores, Cyrillic, timestamps unless needed for uniqueness
- if the title is user-facing, the slug can still be normalized separately

## Pre-publish validation

Before pushing, the agent must verify:
- artifact has `index.html` entrypoint
- oversized raster images are optimized when practical, unless original-size sharing is intentional
- no absolute local paths in HTML/CSS
- no secrets in files
- all local assets reachable from the target folder
- page renders correctly (if browser tools are available, check at desktop 1440px and mobile 375px)

## Safety rules

- do not delete unrelated directories in the Pages repo
- only replace contents of the target page directory being published
- if overwriting, say so in the result
- do not expose the token in output, logs, or committed files
- do not push artifacts that fail local viewport validation

## Config

Read `config/README.md` for required environment variables and URL derivation.

## References

Read if needed:
- `references/publish-checklist.md` — operational checklist before pushing
