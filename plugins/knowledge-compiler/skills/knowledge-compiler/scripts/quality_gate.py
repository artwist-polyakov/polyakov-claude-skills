#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Quality checks for a generated knowledge skill."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED = [
    "SKILL.md",
    "references/concepts.md",
    "references/decision-rules.md",
    "references/playbooks.md",
    "references/anti-patterns.md",
    "references/glossary.md",
    "references/source-map.json",
    "references/knowledge-manifest.json",
]

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|PLACEHOLDER)\b|ЗАПОЛНИ|ЗАМЕНИ", re.IGNORECASE)
WORD_RE = re.compile(r"[\wА-Яа-яЁё]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [m.group(0).lower() for m in WORD_RE.finditer(text)]


def markdown_and_json_text(skill_dir: Path) -> str:
    parts = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".json"}:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(parts)


def longest_ngram_run(source_words: list[str], result_words: list[str], n: int) -> int:
    if len(source_words) < n or len(result_words) < n:
        return 0
    source_ngrams = {tuple(source_words[i : i + n]) for i in range(0, len(source_words) - n + 1)}
    current = 0
    best = 0
    for i in range(0, len(result_words) - n + 1):
        if tuple(result_words[i : i + n]) in source_ngrams:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return 0 if best == 0 else best + n - 1


def check_json(path: Path, errors: list[str]) -> None:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid JSON: {path} ({exc})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--max-skill-words", type=int, default=2000)
    parser.add_argument("--ngram-words", type=int, default=18)
    parser.add_argument("--max-exact-overlap-words", type=int, default=90)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    source_path = Path(args.source)
    skill_dir = Path(args.skill_dir)
    errors: list[str] = []
    warnings: list[str] = []

    if not source_path.exists():
        errors.append(f"source not found: {source_path}")
    if not skill_dir.exists():
        errors.append(f"skill dir not found: {skill_dir}")

    for rel in REQUIRED:
        path = skill_dir / rel
        if not path.exists():
            errors.append(f"missing required file: {rel}")

    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        count = len(words(skill_md.read_text(encoding="utf-8", errors="replace")))
        if count > args.max_skill_words:
            errors.append(f"SKILL.md too long: {count} words > {args.max_skill_words}")

    for rel in ["references/source-map.json", "references/knowledge-manifest.json"]:
        path = skill_dir / rel
        if path.exists():
            check_json(path, errors)

    if skill_dir.exists():
        result_text = markdown_and_json_text(skill_dir)
        if PLACEHOLDER_RE.search(result_text):
            errors.append("placeholder marker found in generated skill")
        if source_path.exists():
            overlap = longest_ngram_run(
                words(source_path.read_text(encoding="utf-8", errors="replace")),
                words(result_text),
                args.ngram_words,
            )
            limit = 60 if args.strict else args.max_exact_overlap_words
            if overlap > limit:
                errors.append(f"long exact overlap with source: about {overlap} words > {limit}")
            elif overlap > max(45, limit // 2):
                warnings.append(f"noticeable exact overlap with source: about {overlap} words")

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
