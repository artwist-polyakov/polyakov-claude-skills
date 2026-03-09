# Diagrams in Telegraph

Render PlantUML/Mermaid diagrams to images and publish them.

Preferred publishing target for rendered diagrams: GitHub assets repo + jsDelivr.
Do not rely on Telegraph upload as the default destination.

## Quick Start

```bash
# PlantUML: render + GitHub upload in one step
URL=$(sh scripts/render_diagram.sh --type plantuml --file arch.puml --github-page-path my-page-path --github-name arch.png)

# Mermaid: same workflow
URL=$(sh scripts/render_diagram.sh --type mermaid --file flow.mmd --github-page-path my-page-path --github-name flow.png)

# Then use URL in your HTML:
# <figure><img src="$URL"><figcaption>Architecture</figcaption></figure>
```

## Two Modes

### URL only (no upload)
Returns a render URL pointing to the public server. Image is rendered on-demand each time.

```bash
sh scripts/render_diagram.sh --type plantuml --file diagram.puml
# → https://www.plantuml.com/plantuml/png/ENCODED
```

### With GitHub upload (recommended)
Downloads the rendered PNG, stores it in the GitHub assets repo, and returns a commit-pinned jsDelivr URL.

```bash
sh scripts/render_diagram.sh --type mermaid --file flow.mmd --github-page-path my-page-path --github-name flow.png
```

### With upload (legacy fallback)
Downloads the rendered PNG and tries to upload it through Telegraph's unofficial upload endpoint.

```bash
sh scripts/render_diagram.sh --type mermaid --file flow.mmd --upload
# → https://telegra.ph/file/abc123.png
```

Do not treat this as the recommended publishing path.
Preferred path for articles:
1. render diagram
2. store PNG in GitHub assets repo
3. use jsDelivr URL in Telegraph

## Supported Diagram Types

### PlantUML
```
@startuml
Alice -> Bob: Authentication Request
Bob --> Alice: Authentication Response
@enduml
```
Server: plantuml.com

### Mermaid
```
graph LR
    A[Client] --> B[API Gateway]
    B --> C[Auth Service]
    B --> D[Data Service]
```
Server: mermaid.ink

## Privacy Warning

**Diagram source code is sent to external public servers** when using `render_diagram.sh`.

- PlantUML diagrams go to `plantuml.com`
- Mermaid diagrams go to `mermaid.ink`

**Do not use for confidential content.** If your diagram contains sensitive information:
1. Render locally (requires Java for PlantUML or Node.js for Mermaid)
2. Upload the resulting PNG to your GitHub assets repo
3. Use jsDelivr URL in Telegraph

## PlantUML Caveats

PlantUML's public server does **not** return HTTP errors for invalid diagrams. Instead, it returns a PNG image containing an error message. The script warns about this:

> "Verify the rendered image visually before publishing."

## Readability Checklist

Before publishing a diagram:
- Text is readable at Telegraph page width (~700px)
- Colors have sufficient contrast
- Diagram is not overly dense (split complex ones into multiple images)
- Caption describes what the diagram shows
- Use PNG format (Telegraph may not render SVG)

## Local Rendering (alternative)

If you don't want to send diagram source to external servers:

**PlantUML** (requires Java):
```bash
plantuml -tpng diagram.puml
# Then store diagram.png in GitHub assets repo and use jsDelivr URL
```

**Mermaid CLI** (requires Node.js):
```bash
npx @mermaid-js/mermaid-cli -i diagram.mmd -o diagram.png
# Then store diagram.png in GitHub assets repo and use jsDelivr URL
```

## Cleanup Strategy

Rendered diagrams should live under the page-specific GitHub folder:
`pages/<telegraph_path>/diagram-01.png`

Track them in the page manifest:
`manifests/<telegraph_path>.json`

When a page is cleaned up:
1. read manifest by Telegraph `path`
2. delete diagram files by recorded GitHub path + SHA
3. remove the manifest

This is more reliable than deriving assets from title text.
