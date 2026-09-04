#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Quality checks for a generated knowledge skill."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import quote


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
SEGMENT_RE = re.compile(r"(?<![\w-])seg-[0-9]+(?![\w-])")
CLAIM_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SOURCE_INDEX = "references/source-index.md"


def words(text: str) -> list[str]:
    return [m.group(0).lower() for m in WORD_RE.finditer(text)]


def markdown_and_json_text(skill_dir: Path) -> str:
    parts = []
    for path in sorted(skill_dir.rglob("*")):
        if path == skill_dir / SOURCE_INDEX:
            continue  # Check the freshly rendered index, not its possibly stale copy.
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


def check_json(path: Path, errors: list[str]) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"invalid JSON: {path} ({exc})")
        return None


def markdown_lines(text: str):
    """Yield numbered lines outside fenced code blocks and HTML comments."""
    marker = ""
    length = 0
    in_comment = False
    for number, line in enumerate(text.splitlines(), start=1):
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence and not in_comment:
            run, tail = fence.groups()
            if not marker:
                marker, length = run[0], len(run)
            elif run[0] == marker and len(run) >= length and not tail.strip():
                marker = ""
            continue
        if marker:
            continue
        if in_comment:
            _, closed, line = line.partition("-->")
            if not closed:
                continue
        line = re.sub(r"<!--.*?-->", "", line)
        line, opened, _ = line.partition("<!--")
        in_comment = bool(opened)
        yield number, line


def heading_key(title: str) -> str:
    """Detect collisions with our plain kebab-case IDs, including display titles."""
    title = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", title)
    title = re.sub(r"<[^>]+>", "", title)
    title = html.unescape(title).lower().replace("_", "")
    return re.sub(r"[^\w -]", "", title).replace(" ", "-")


def check_source_map(source_map: object, skill_dir: Path, errors: list[str]) -> None:
    if not isinstance(source_map, dict):
        errors.append("source-map must be a JSON object")
        return
    for key in ("segments", "claims"):
        if not isinstance(source_map.get(key), list) or not source_map[key]:
            errors.append(f"source-map.{key} must be a non-empty list")
    if errors:
        return

    segments = {}
    for segment in source_map["segments"]:
        if not isinstance(segment, dict):
            errors.append("source-map segment must be an object")
            continue
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not SEGMENT_RE.fullmatch(segment_id):
            errors.append("invalid segment_id in source-map")
            continue
        if segment_id in segments:
            errors.append(f"duplicate segment_id: {segment_id}")
        segments[segment_id] = segment
        if not isinstance(segment.get("title"), str) or not segment["title"].strip():
            errors.append(f"source-map {segment_id}: title must be non-empty text")
        for prefix, minimum in (("char", 0), ("line", 1)):
            start, end = segment.get(f"{prefix}_start"), segment.get(f"{prefix}_end")
            if (type(start) is not int or type(end) is not int
                    or start < minimum or end < start or (prefix == "char" and end == start)):
                errors.append(f"source-map {segment_id}: invalid {prefix} range")
        confidence = segment.get("confidence")
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            errors.append(f"source-map {segment_id}: confidence must be between 0 and 1")

    # Reuse the same Markdown scan for source references and claim headings.
    root = skill_dir.resolve()
    headings = {}
    for path in sorted(skill_dir.rglob("*.md")):
        if path == skill_dir / SOURCE_INDEX or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            errors.append(f"Markdown file outside skill: {path.relative_to(skill_dir)}")
            continue
        file_headings = []
        for number, line in markdown_lines(path.read_text(encoding="utf-8")):
            heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$", line)
            if heading:
                level, title = heading.groups()
                title = re.sub(r"[ \t]+#+$", "", title)
                file_headings.append((len(level), title))
            for segment_id in sorted(set(SEGMENT_RE.findall(line))):
                if segment_id not in segments:
                    errors.append(f"unknown segment {segment_id}: {path.relative_to(skill_dir)}:{number}")
        headings[path.resolve()] = file_headings

    claim_ids = set()
    for claim in source_map["claims"]:
        if not isinstance(claim, dict):
            errors.append("source-map claim must be an object")
            continue
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not CLAIM_ID_RE.fullmatch(claim_id):
            errors.append("invalid claim_id in source-map: use lowercase letters, digits and single hyphens")
            continue
        if claim_id in claim_ids:
            errors.append(f"duplicate claim_id: {claim_id}")
        claim_ids.add(claim_id)

        source_segments = claim.get("source_segments")
        if not isinstance(source_segments, list) or not source_segments:
            errors.append(f"source-map {claim_id}: source_segments must be a non-empty list")
        else:
            for segment_id in source_segments:
                if not isinstance(segment_id, str) or segment_id not in segments:
                    errors.append(f"unknown segment {segment_id!r}: claim {claim_id}")

        artifact = claim.get("artifact")
        if not isinstance(artifact, str):
            errors.append(f"invalid artifact: claim {claim_id}")
            continue
        relative = Path(artifact)
        path = (skill_dir / relative).resolve()
        if (relative.is_absolute() or ".." in relative.parts
                or not relative.parts or relative.parts[0] != "references"
                or relative.suffix != ".md" or relative.as_posix() == SOURCE_INDEX
                or not path.is_relative_to(root / "references")):
            errors.append(f"invalid artifact {artifact!r}: expected a Markdown file inside references")
            continue
        if not path.is_file():
            errors.append(f"missing artifact {artifact}: claim {claim_id}")
            continue
        file_headings = headings.get(path, [])
        matches = [level for level, title in file_headings if title == claim_id]
        anchor_count = sum(heading_key(title) == claim_id for _, title in file_headings)
        if matches != [2] or anchor_count != 1:
            errors.append(f"claim heading {claim_id}: expected exactly one '## {claim_id}' in {artifact}")


def render_source_index(source_map: dict) -> str:
    related = {segment["segment_id"]: [] for segment in source_map["segments"]}
    for claim in sorted(source_map["claims"], key=lambda item: item["claim_id"]):
        for segment_id in dict.fromkeys(claim["source_segments"]):
            related[segment_id].append(claim)
    lines = [
        "# Указатель источников", "",
        "Создан из [source-map.json](source-map.json). Не редактируйте вручную.", "",
        "Строки (с 1, включительно) и символы (с 0, конец не включён) относятся к извлечённому "
        "`source.txt`, а не страницам PDF. Исходный текст остаётся в локальном кеше компилятора; "
        "без него нельзя проверить сам фрагмент. Уверенность разметки не доказывает достоверность тезисов.",
    ]
    for segment in source_map["segments"]:
        segment_id = segment["segment_id"]
        title = html.escape(" ".join(segment["title"].split()), quote=False)
        title = re.sub(r"([\\`*_\[\]])", r"\\\1", title)
        lines.extend([
            "", f"## {segment_id}", "", f"**Раздел:** {title}", "",
            f"**Место:** строки {segment['line_start']}–{segment['line_end']}; "
            f"символы [{segment['char_start']}, {segment['char_end']}).", "",
            f"**Уверенность разметки:** {segment['confidence']}", "",
            "**Связанные тезисы:**", "",
        ])
        for claim in related[segment_id]:
            artifact = Path(claim["artifact"]).relative_to("references").as_posix()
            lines.append(f"- [{claim['claim_id']}]({quote(artifact)}#{claim['claim_id']})")
        if not related[segment_id]:
            lines.append("Связанных тезисов нет.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--max-skill-words", type=int, default=2000)
    parser.add_argument("--ngram-words", type=int, default=18)
    parser.add_argument("--max-exact-overlap-words", type=int, default=90)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--write-source-index", action="store_true",
                        help="Create/update source-index.md after successful checks")
    args = parser.parse_args()

    source_path = Path(args.source)
    skill_dir = Path(args.skill_dir)
    errors: list[str] = []
    warnings: list[str] = []

    if not source_path.is_file():
        errors.append(f"source not found: {source_path}")
    if not skill_dir.is_dir():
        errors.append(f"skill dir not found: {skill_dir}")

    for rel in REQUIRED:
        path = skill_dir / rel
        if not path.is_file():
            errors.append(f"missing required file: {rel}")

    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        count = len(words(skill_md.read_text(encoding="utf-8", errors="replace")))
        if count > args.max_skill_words:
            errors.append(f"SKILL.md too long: {count} words > {args.max_skill_words}")

    source_map = None
    for rel in ["references/source-map.json", "references/knowledge-manifest.json"]:
        path = skill_dir / rel
        if path.is_file():
            value = check_json(path, errors)
            if rel == "references/source-map.json":
                source_map = value

    index_path = skill_dir / SOURCE_INDEX
    index_text = None
    if not errors:
        check_source_map(source_map, skill_dir, errors)
        if not errors:
            index_text = render_source_index(source_map)
    if index_path.is_symlink() or not index_path.resolve().is_relative_to(skill_dir.resolve()):
        errors.append("source-index.md must be a regular file inside the skill")
    elif index_path.exists() and not index_path.is_file():
        errors.append("source-index.md must be a regular file")
    elif not args.write_source_index:
        if not index_path.is_file():
            errors.append("missing source-index.md: run with --write-source-index")
        elif index_text is not None and index_path.read_text(encoding="utf-8") != index_text:
            errors.append("stale source-index.md: run with --write-source-index")

    if skill_dir.is_dir():
        result_text = markdown_and_json_text(skill_dir)
        if index_text is not None:
            result_text += "\n\n" + index_text
        if PLACEHOLDER_RE.search(result_text):
            errors.append("placeholder marker found in generated skill")
        if source_path.is_file():
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

    if args.write_source_index:
        try:
            index_path.write_text(index_text, encoding="utf-8")
        except OSError as exc:
            print(f"FAIL\n- cannot write source-index.md: {exc}")
            return 1
        print(f"Source index: {index_path}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
