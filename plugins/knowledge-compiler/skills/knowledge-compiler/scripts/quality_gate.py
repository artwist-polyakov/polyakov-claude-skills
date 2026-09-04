#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown-it-py==4.2.0"]
# ///
"""Quality checks for a generated knowledge skill."""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from segment_text import build_lines, line_for_char


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


class MarkdownContent(HTMLParser):
    """Read rendered text and link targets without executing HTML."""

    TEXT_BREAKS = set(
        "address article aside blockquote br dd details dialog div dl dt fieldset figcaption "
        "figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section summary "
        "table tbody td tfoot th thead tr ul".split()
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.source_links = []
        self.link = None
        self.headings = []
        self.explicit_anchors = set()
        self.heading = None
        self.pre_depth = 0

    def handle_starttag(self, tag, attrs):
        self.explicit_anchors.update(
            value for key, value in attrs
            if value and (key == "id" or (tag == "a" and key == "name"))
        )
        if tag in self.TEXT_BREAKS:
            self.text.append("\n")
        if tag == "pre":
            self.pre_depth += 1
        if self.pre_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading = []
        if tag == "a":
            self.link = (dict(attrs).get("href") or "", len(self.text))
        if tag == "img":
            self.text.append(dict(attrs).get("alt") or "")
        self.links.extend((key, value) for key, value in attrs if key in {"href", "src", "alt"} and value)

    def handle_endtag(self, tag):
        if tag in self.TEXT_BREAKS:
            self.text.append("\n")
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
        if not self.pre_depth and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if self.heading is not None:
                self.headings.append("".join(self.heading))
                self.heading = None
        if not self.pre_depth and tag == "a" and self.link is not None:
            target, start = self.link
            self.source_links.append((target, "".join(self.text[start:])))
            self.link = None

    def handle_data(self, data):
        if not self.pre_depth:
            self.text.append(data)
            if self.heading is not None:
                self.heading.append(data)


def heading_key(title: str) -> str:
    """GitHub-style anchor base from rendered text, preserving literal underscores."""
    return "".join(
        char for char in title.lower()
        if char in " -" or unicodedata.category(char)[0] in "LM"
        or unicodedata.category(char) in {"Nd", "Nl", "Pc"}
    ).replace(" ", "-")


def markdown_content(text: str):
    """Use CommonMark for containers, code blocks, headings and resolved links."""
    markdown = MarkdownIt("commonmark")
    tokens = markdown.parse(text)
    canonical = [
        (int(token.tag[1]), tokens[index + 1].content)
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.markup.startswith("#")
    ]
    content = MarkdownContent()
    content.feed(markdown.renderer.render(tokens, markdown.options, {}))
    content.close()
    anchors = []
    occupied = set()
    for title in content.headings:
        base = anchor = heading_key(title)
        suffix = 0
        while anchor in occupied:
            suffix += 1
            anchor = f"{base}-{suffix}"
        occupied.add(anchor)
        anchors.append((base, anchor))
    # Explicit HTML targets can collide with a claim but do not number headings.
    anchors.extend((target, target) for target in content.explicit_anchors)
    segments = set(SEGMENT_RE.findall("".join(content.text)))
    for attribute, target in content.links:
        if attribute != "alt":
            try:
                target = unquote(urlsplit(target).fragment)
            except ValueError:
                continue
        segments.update(SEGMENT_RE.findall(target))
    return canonical, anchors, segments, content.source_links


def check_confidence(item: dict, identifier: str, errors: list[str]) -> None:
    confidence = item.get("confidence")
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        errors.append(f"source-map {identifier}: confidence must be between 0 and 1")


def check_source_map(source_map: object, skill_dir: Path, source_text: str, errors: list[str]) -> None:
    if not isinstance(source_map, dict):
        errors.append("source-map must be a JSON object")
        return
    for key in ("segments", "claims"):
        if not isinstance(source_map.get(key), list) or not source_map[key]:
            errors.append(f"source-map.{key} must be a non-empty list")
    if errors:
        return

    source_lines = build_lines(source_text)
    limits = {"char": len(source_text), "line": len(source_lines)}
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
        ranges_valid = True
        for prefix, minimum in (("char", 0), ("line", 1)):
            start, end = segment.get(f"{prefix}_start"), segment.get(f"{prefix}_end")
            if (type(start) is not int or type(end) is not int
                    or start < minimum or end < start or end > limits[prefix]
                    or (prefix == "char" and end == start)):
                errors.append(f"source-map {segment_id}: invalid {prefix} range")
                ranges_valid = False
        if ranges_valid:
            expected_lines = (
                line_for_char(source_lines, segment["char_start"]),
                line_for_char(source_lines, segment["char_end"] - 1),
            )
            if (segment["line_start"], segment["line_end"]) != expected_lines:
                errors.append(f"source-map {segment_id}: line range does not match char range "
                              f"(expected {expected_lines[0]}–{expected_lines[1]})")
        check_confidence(segment, segment_id, errors)

    # Reuse the same Markdown scan for source references and claim headings.
    root = skill_dir.resolve()
    headings = {}
    for path in sorted(skill_dir.rglob("*.md")):
        if path == skill_dir / SOURCE_INDEX or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            errors.append(f"Markdown file outside skill: {path.relative_to(skill_dir)}")
            continue
        canonical, anchors, references, links = markdown_content(path.read_text(encoding="utf-8"))
        for segment_id in sorted(references - segments.keys()):
            errors.append(f"unknown segment {segment_id}: {path.relative_to(skill_dir)}")
        for target, label in links:
            link_segments = set(SEGMENT_RE.findall(label))
            try:
                url = urlsplit(target)
                segment_id = unquote(url.fragment)
                link_segments.update(SEGMENT_RE.findall(segment_id))
                if not link_segments:
                    continue  # Unrelated links are outside the source-map contract.
                relative = Path(unquote(url.path))
                valid = (
                    not url.scheme and not url.netloc and not url.query
                    and not re.search(r"%(?:2f|5c)", url.path, re.IGNORECASE)
                    and not relative.is_absolute()
                    and (path.parent / relative).resolve() == root / SOURCE_INDEX
                    and segment_id in segments and link_segments == {segment_id}
                )
            except (ValueError, OSError):
                if not link_segments:
                    continue
                valid = False
            if not valid:
                errors.append(f"invalid segment link {target!r}: {path.relative_to(skill_dir)}")
        headings[path.resolve()] = canonical, anchors

    claim_ids = set()
    for claim in source_map["claims"]:
        if not isinstance(claim, dict):
            errors.append("source-map claim must be an object")
            continue
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not CLAIM_ID_RE.fullmatch(claim_id):
            errors.append("invalid claim_id in source-map: use lowercase letters, digits and single hyphens")
            continue
        if SEGMENT_RE.fullmatch(claim_id):
            errors.append(f"invalid claim_id {claim_id}: seg-<number> is reserved for segments")
            continue
        if claim_id in claim_ids:
            errors.append(f"duplicate claim_id: {claim_id}")
        claim_ids.add(claim_id)

        if claim.get("extraction_type") not in (
            "synthesized", "named-framework", "short-quote", "inferred"
        ):
            errors.append(f"source-map {claim_id}: invalid extraction_type")
        quote_words = claim.get("verbatim_quote_words")
        if type(quote_words) is not int or quote_words < 0:
            errors.append(f"source-map {claim_id}: verbatim_quote_words must be a non-negative integer")
        check_confidence(claim, claim_id, errors)

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
        canonical, anchors = headings.get(path, ([], []))
        matches = [level for level, title in canonical if title == claim_id]
        targets = [anchor for base, anchor in anchors if base == claim_id]
        if matches != [2] or targets != [claim_id]:
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

    source_text = None
    if not source_path.is_file():
        errors.append(f"source not found: {source_path}")
    else:
        try:
            source_text = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"cannot read source: {source_path} ({exc})")
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
        check_source_map(source_map, skill_dir, source_text, errors)
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
        if source_text is not None:
            overlap = longest_ngram_run(
                words(source_text),
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
