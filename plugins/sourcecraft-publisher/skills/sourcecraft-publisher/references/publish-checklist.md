# Publish checklist

Before push:
- target path follows `YYYY/YYYY-MM/page-slug/`
- artifact has `index.html`
- all local assets remain reachable from that folder
- oversized raster images are optimized when practical or intentionally kept large
- no accidental absolute local paths
- no secrets in files
- `.sourcecraft/sites.yaml` exists in repo root
- final URL is computed and returned
