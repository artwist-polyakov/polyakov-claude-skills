# fal-ai-image Integration (Optional)

## Overview

If the `fal-ai-image` skill is installed, it can generate illustrations for Telegraph articles.

**This is optional.** The telegraph-publisher skill works fully without fal-ai-image.

## Prerequisites

1. `fal-ai-image` skill installed and configured (FAL_KEY in its config)
2. Read fal-ai-image SKILL.md before use
3. **User must confirm budget** before any generation ($0.15+/image)

## Workflow

1. **Draft article** in HTML
2. **Identify illustration needs** (hero image, section illustrations, diagrams)
3. **Confirm budget** with user: "N images × $0.15 = $X.XX. Proceed?"
4. **Generate images** via fal-ai-image scripts
5. **Save image URLs** (they expire in ~1 hour — download locally if needed)
6. **Insert URLs** into HTML as `<figure><img src="URL"></figure>`
7. **Publish** via `create_page.sh`

## House Style for Editorial Illustrations

When generating illustrations for articles, use this minimalist style:

### Style Guide
- **Feel**: editorial, infographic
- **Background**: light, clean (white or soft neutral)
- **Composition**: clean, minimal visual noise
- **Priority**: readability over decoration
- **Colors**: limited palette, high contrast for text/diagrams

### Prompt Template
```
[Subject description]. Editorial illustration style, clean white background,
minimalist infographic aesthetic, limited color palette, high contrast,
professional look, no visual clutter.
```

### Example Prompts

**Hero image for tech article:**
```
Abstract visualization of data flowing through neural network layers.
Editorial illustration style, clean white background, minimalist
infographic aesthetic, blue and teal color palette, high contrast,
professional look, no visual clutter.
```

**Section illustration:**
```
Simple diagram showing client-server architecture with labeled components.
Editorial illustration style, clean white background, minimalist flat design,
limited color palette, high contrast text labels, no decorative elements.
```

**Concept illustration:**
```
Metaphorical visualization of API as a bridge connecting two platforms.
Editorial illustration style, light background, clean geometric shapes,
minimal color palette, professional infographic feel.
```

## Important Notes

- fal-ai-image URLs expire in ~1 hour. For persistent articles, download images and re-upload to permanent hosting before publishing
- Always confirm pricing with user before generation
- Do not generate images for every section — use sparingly for maximum impact
- The model renders text natively (including Cyrillic) — useful for labeled diagrams
