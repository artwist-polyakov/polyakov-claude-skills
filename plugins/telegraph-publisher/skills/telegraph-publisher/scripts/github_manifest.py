#!/usr/bin/env python3
"""Helpers for Telegraph GitHub asset manifests.

Commands:
  merge <page_path> <asset_path> <asset_sha> <cdn_url> <commit_sha>
  list
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def read_manifest() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def cmd_merge(args: list[str]) -> int:
    if len(args) != 5:
        print(
            "Usage: github_manifest.py merge <page_path> <asset_path> <asset_sha> <cdn_url> <commit_sha>",
            file=sys.stderr,
        )
        return 1

    page_path, asset_path, asset_sha, cdn_url, commit_sha = args
    manifest = read_manifest() or {}
    assets = manifest.get("assets", [])

    updated_asset = {
        "path": asset_path,
        "sha": asset_sha,
        "cdn_url": cdn_url,
        "commit_sha": commit_sha,
    }

    replaced = False
    result_assets = []
    for asset in assets:
        if asset.get("path") == asset_path:
            result_assets.append(updated_asset)
            replaced = True
        else:
            result_assets.append(asset)
    if not replaced:
        result_assets.append(updated_asset)

    result_assets.sort(key=lambda item: item.get("path", ""))

    manifest = {
        "path": page_path,
        "assets": result_assets,
        "updated_at": utc_now(),
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_list() -> int:
    manifest = read_manifest()
    for asset in manifest.get("assets", []):
        print(
            "\t".join(
                [
                    asset.get("path", ""),
                    asset.get("sha", ""),
                    asset.get("cdn_url", ""),
                    asset.get("commit_sha", ""),
                ]
            )
        )
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: github_manifest.py <merge|list> ...", file=sys.stderr)
        return 1

    cmd = sys.argv[1]
    if cmd == "merge":
        return cmd_merge(sys.argv[2:])
    if cmd == "list":
        return cmd_list()

    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
