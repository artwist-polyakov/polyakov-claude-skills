---
name: fal-ai-image
description: "Generates and edits images via fal.ai nano-banana-pro (Gemini 3 Pro Image). Creates images from text prompts, edits existing images using up to 14 reference images, and renders text natively in any language including Cyrillic and CJK. Use when the user asks to generate, create, draw, or edit images, needs infographics, banners, illustrations, or text rendered on images. Triggers: generate image, create image, edit image, draw, picture, illustration, AI art, infographic, fal.ai, banner, нарисуй, создай картинку, сгенерируй изображение."
---

# fal-ai-image

Generate images via fal.ai nano-banana-pro (Gemini 3 Pro Image).
Best for: infographics, text rendering, complex compositions.

## STOP — Read Before Acting

- **DO NOT** use Pillow, ImageMagick, or any post-processing for text/logo overlay — this model renders text natively, including Cyrillic and CJK
- **DO NOT** use Generate mode when user provides reference images — use **Edit mode**
- **DO NOT** launch general-purpose subagents — use `Task` with `subagent_type: "Bash"` and `model: "haiku"`
- **DO NOT** skip uploading local files — run `upload.sh` first to get URLs for `edit.sh`
- **DO NOT** guess script parameters — check the tables below

## Quick Start Decision

```
User gave reference images?  → Edit mode  (upload.sh → edit.sh)
User wants text-only gen?    → Generate mode (generate.sh)
Multiple images needed?      → Parallel Bash/haiku subagents
```

## Config

Requires `FAL_KEY` in `config/.env` or environment.
Get key: https://fal.ai/dashboard/keys

## Two Modes

### 1. Generate (text-to-image)
Create images from text prompt only.
Script: `scripts/generate.sh`

### 2. Edit (image-to-image)
Create images using reference images (up to 14).
Script: `scripts/edit.sh`

## Workflow

**IMPORTANT**: Run generation via Task tool with Haiku subagent to avoid blocking main context.

### For Generate mode:

1. **Clarify params** (if not specified):
   - Aspect ratio: `1:1`, `16:9`, `9:16`, `4:3`, etc.
   - Resolution: `1K` (default), `2K`, `4K`

2. **Propose save path** based on project structure:
   - Check for `./images/`, `./assets/`, `./static/`
   - Suggest: "Save to `./images/infographic_coffee.png`?"

3. **Show price & confirm**:
   ```
   Cost: $0.15/image (4K: $0.30)
   Confirm? (yes/no)
   ```

4. **Launch subagent**:
   ```
   Task tool:
   - subagent_type: "Bash"
   - model: "haiku"
   - run_in_background: true
   - prompt: Run generate.sh with params
   ```

5. **Report result**: Parse JSON output, show image URL, read saved file.

### For Edit mode:

1. **Get reference images**:
   - If URLs provided → use directly
   - If local files → run `upload.sh` first to get URLs

2. **Clarify prompt**: What to do with references?

3. **Show price & confirm**: Same as generate

4. **Launch subagent** with `edit.sh`

5. **Report result**

## Scripts

### generate.sh
```bash
sh scripts/generate.sh \
  --prompt "infographic about coffee brewing" \
  --aspect-ratio "9:16" \
  --resolution "1K" \
  --output-dir "./images" \
  --filename "coffee_infographic"
```

| Param | Required | Default | Values |
|-------|----------|---------|--------|
| `--prompt` | yes | - | text |
| `--aspect-ratio` | no | 1:1 | 21:9, 16:9, 3:2, 4:3, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16 |
| `--resolution` | no | 1K | 1K, 2K, 4K |
| `--num-images` | no | 1 | 1-4 |
| `--output-dir` | no | - | path |
| `--filename` | no | generated | base name |
| `--web-search` | no | false | flag |

### edit.sh
```bash
sh scripts/edit.sh \
  --prompt "combine these into a collage" \
  --image-urls "https://example.com/img1.png,https://example.com/img2.png" \
  --aspect-ratio "16:9" \
  --output-dir "./images" \
  --filename "collage"
```

| Param | Required | Default | Values |
|-------|----------|---------|--------|
| `--prompt` | yes | - | text |
| `--image-urls` | yes | - | comma-separated URLs (max 14) |
| `--aspect-ratio` | no | auto | auto, 1:1, 16:9, etc. |
| `--resolution` | no | 1K | 1K, 2K, 4K |
| `--num-images` | no | 1 | 1-4 variations |
| `--output-dir` | no | - | path |
| `--filename` | no | edited | base name |

### upload.sh (for local files)
```bash
# Get URL for local file
URL=$(sh scripts/upload.sh --file /path/to/image.png)

# Or get base64 data URI (for small files)
URI=$(sh scripts/upload.sh --file /path/to/image.png --base64)
```

## Parallel Generation

For multiple images — launch several subagents in parallel:

```
Task 1: generate "cat in space" → cat_space.png
Task 2: generate "dog on moon" → dog_moon.png
Task 3: generate "bird in ocean" → bird_ocean.png
```

Each runs independently via Haiku, results collected when done.

## Pricing

- Generate: **$0.15**/image
- Edit: **$0.15**/edit
- 4K resolution: **$0.30** (2x)
- Web search: **+$0.015**

Formula: `price = num_images * (resolution == "4K" ? 0.30 : 0.15) + (web_search ? 0.015 : 0)`

## Notes

- URLs expire in ~1 hour — save locally if needed
- Uploaded files stored 7 days on fal.ai, then auto-deleted
- Model excels at text rendering and infographics
