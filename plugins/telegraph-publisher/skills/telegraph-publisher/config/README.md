# Telegraph Publisher — Configuration

## Quick Start

1. Create a Telegraph account:
   ```bash
   sh scripts/create_account.sh --name "Your Name"
   ```
   This outputs: `access_token`, `auth_url`, `short_name`.

2. Copy the token to config:
   ```bash
   cp config/.env.example config/.env
   # Edit config/.env and paste your access_token
   ```

3. (Optional) Open `auth_url` in your browser to bind the account to your browser session.

## Access Token

The `TELEGRAPH_ACCESS_TOKEN` is required for all operations except reading public pages.

You can set it in two ways:
- **File**: `config/.env` (recommended)
- **Environment variable**: `export TELEGRAPH_ACCESS_TOKEN=...`

## GitHub Media Hosting (recommended)

For permanent images and diagrams, configure a separate public GitHub repo and serve assets through jsDelivr.

Recommended architecture:
- create a **separate public repo only for Telegraph media**
- do **not** reuse your main code repo for images
- create a **separate fine-grained PAT** that has access **only to that media repo**

Required variables:

```bash
GITHUB_TOKEN=ghp_...
GITHUB_ASSETS_REPO=owner/repo
GITHUB_ASSETS_BRANCH=main
GITHUB_ASSETS_BASE_DIR=pages
GITHUB_MANIFESTS_DIR=manifests
```

### Why GitHub is recommended

- Telegraph's upload endpoint is unofficial and unstable
- jsDelivr gives permanent CDN URLs for published pages
- assets can be grouped per Telegraph page
- cleanup becomes deterministic via manifest files

### Minimal setup

1. Create a **public** GitHub repo for Telegraph assets
   Example: `yourname/telegraph-assets`
2. Open GitHub -> Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens
3. Click `Generate new token`
4. In `Resource owner`, choose the user or organization that owns the assets repo
5. In `Repository access`, choose `Only select repositories`
6. Select only that one assets repo
7. In permissions, set:
   - `Contents`: `Read and write`
8. Create the token and save it immediately
9. Save repo and token to `config/.env`
10. Upload media through `github_upload.sh`

Recommended token type:
- fine-grained PAT
- repo scope limited to the assets repo
- permission: `Contents` = `Read and write`

### Why a separate repo + separate token

- if the token leaks, the blast radius is limited to media files only
- no access to your main code repositories
- cleanup scripts can freely create/update/delete manifests and assets without touching application code
- the repo stays easy to inspect: only page assets and manifests live there

### Suggested repo contents

The media repo should contain only:
- `pages/<telegraph_path>/...` assets
- `manifests/<telegraph_path>.json` manifests

Avoid storing anything else there.

### Manifest-driven cleanup

Each Telegraph page should have a manifest:

```text
manifests/<telegraph_path>.json
```

The manifest stores uploaded asset paths and SHAs. Later cleanup should delete assets by manifest, not by title guessing.

Recommended lifecycle:
1. create or obtain final Telegraph `path`
2. upload assets under `pages/<telegraph_path>/...`
3. publish page with jsDelivr URLs
4. when page is removed, run `github_delete_page_assets.sh --page-path <telegraph_path>`

## Using an Existing Telegraph Account

If you already have a Telegraph account in the browser, the simplest way is to extract the token from cookies (see above).

Alternatively, create a new API account:
1. `sh scripts/create_account.sh --name "Your Name"`
2. Save the token to `config/.env`
3. Open `auth_url` in the browser to log into this new account
   **Warning**: This replaces your current browser session, not merges with it.

### Extracting token from browser

If you already have a Telegraph account in the browser, you can extract the API token:

1. Open any of your Telegraph pages in **Chrome**
2. DevTools (F12) → **Application** → **Cookies** → `https://telegra.ph`
3. Find cookie `tph_token` — its value IS your `access_token`

**Note**: This cookie is httpOnly, so `document.cookie` won't show it. You must use the Application tab in DevTools. Safari may not display httpOnly cookies in its inspector.

## Account Ownership Model

Telegraph has a specific ownership model that differs from most publishing platforms:

### How it works

1. **`createAccount`** generates a new Telegraph account with a unique `access_token`. This account is API-only — it has no password, no email, no login.

2. **`auth_url`** is a one-time link (valid 5 minutes) that binds the API account to your browser session. After opening it:
   - Pages you created via API become visible in your browser at telegra.ph
   - You can edit pages both via browser and via API
   - Your browser Telegraph history merges with the API account

3. **Page ownership**: A page belongs to the account whose `access_token` was used in `createPage`. Only that account can edit the page via API (`can_edit: true`).

4. **If you already use Telegraph in browser**: Opening `auth_url` will link your existing browser pages to the API account. Use `revokeAccessToken` (via `create_account.sh --revoke`) to get a fresh `auth_url` if the previous one expired.

### Viewing your pages

- **Via API**: `sh scripts/list_pages.sh` — shows all pages owned by the account
- **Via browser**: Open `auth_url` first, then visit telegra.ph — your pages appear in the sidebar

### Security

- Anyone with your `access_token` can create/edit pages under your account
- Use `create_account.sh --revoke` to rotate the token if compromised (old token becomes invalid)
- Store `config/.env` securely, never commit it to git
