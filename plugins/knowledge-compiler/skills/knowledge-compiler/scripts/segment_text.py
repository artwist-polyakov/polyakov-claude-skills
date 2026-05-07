#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Split extracted source text into segments with source pointers."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HEADING_RE = re.compile(
    r"^\s{0,3}("
    r"#{1,6}\s+\S.*|"
    r"(глава|часть|раздел)\s+([0-9ivxlcdm]+|[а-яёa-z]+)\b.*|"
    r"(chapter|part|section)\s+([0-9ivxlcdm]+|[a-z]+)\b.*|"
    r"[0-9]{1,2}(\.[0-9]{1,2}){0,3}\s+.{3,140}|"
    r"[IVXLCDM]{1,8}\.\s+.{3,140}"
    r")\s*$",
    re.IGNORECASE,
)


@dataclass
class Line:
    number: int
    start: int
    end: int
    text: str


def build_lines(text: str) -> list[Line]:
    lines = []
    pos = 0
    for idx, raw in enumerate(text.splitlines(keepends=True), start=1):
        end = pos + len(raw)
        lines.append(Line(idx, pos, end, raw.rstrip("\n\r")))
        pos = end
    if not lines:
        lines.append(Line(1, 0, 0, ""))
    return lines


def clean_title(text: str) -> str:
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()[:140] or "Untitled segment"


def normalize_space(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def load_decision(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("model_decision", data)


def toc_items_from_decision(decision: dict[str, object]) -> list[dict[str, object]]:
    items = decision.get("toc_items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict) and item.get("label")]
    observations = decision.get("script_observations")
    if isinstance(observations, dict):
        observed = observations.get("epub_toc_items")
        if isinstance(observed, list):
            return [item for item in observed if isinstance(item, dict) and item.get("label")]
    return []


def compile_patterns(values: object) -> list[re.Pattern[str]]:
    if not isinstance(values, list):
        return []
    patterns = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            patterns.append(re.compile(value, re.IGNORECASE))
        except re.error:
            escaped = re.escape(value.strip())
            patterns.append(re.compile(escaped, re.IGNORECASE))
    return patterns


def find_headings(lines: list[Line], decision: dict[str, object]) -> list[Line]:
    configured = compile_patterns(decision.get("chapter_patterns")) + compile_patterns(
        decision.get("section_patterns")
    )
    exclude = compile_patterns(decision.get("exclude_patterns"))
    headings = []
    for line in lines:
        stripped = line.text.strip()
        if not (3 <= len(stripped) <= 160):
            continue
        if any(pattern.search(stripped) for pattern in exclude):
            continue
        if configured:
            matched = any(pattern.search(stripped) for pattern in configured)
        else:
            matched = bool(DEFAULT_HEADING_RE.match(stripped))
        if matched:
            headings.append(line)
    return headings


def split_by_headings(text: str, lines: list[Line], headings: list[Line]) -> list[dict[str, object]]:
    points = []
    if headings and headings[0].start > 0:
        points.append((0, "Front matter", 1))
    for heading in headings:
        points.append((heading.start, clean_title(heading.text), heading.number))

    segments = []
    for idx, (start, title, line_start) in enumerate(points):
        end = points[idx + 1][0] if idx + 1 < len(points) else len(text)
        if end <= start:
            continue
        line_end = line_for_char(lines, max(start, end - 1))
        body = text[start:end].strip()
        if len(body.split()) < 40 and idx + 1 < len(points):
            continue
        segments.append(
            {
                "title": title,
                "char_start": start,
                "char_end": end,
                "line_start": line_start,
                "line_end": line_end,
                "text": body,
                "confidence": 0.78,
                "method": "heading",
            }
        )
    return segments


def normalized_index(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    mapping: list[int] = []
    prev_space = False
    for idx, char in enumerate(text):
        if char in {"\u00a0", "\u202f"} or char.isspace():
            if not prev_space:
                chars.append(" ")
                mapping.append(idx)
                prev_space = True
            continue
        chars.append(char.lower())
        mapping.append(idx)
        prev_space = False
    return "".join(chars), mapping


def normalize_for_match(value: str) -> str:
    normalized, _ = normalized_index(normalize_space(value))
    normalized = re.sub(r"\s+[0-9ivxlcdm]+$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized


def anchor_variants(label: str) -> list[str]:
    full = normalize_for_match(label)
    if not full:
        return []
    variants = [full]
    words = full.split()
    for count in (14, 10, 7, 5):
        if len(words) > count:
            variants.append(" ".join(words[:count]))
    numbered = re.match(r"^([0-9]+(?:\.[0-9]+)*\.?)\s+(.+)$", full)
    if numbered:
        rest = numbered.group(2).split()
        if rest:
            variants.append(f"{numbered.group(1)} {' '.join(rest[:5])}")
            variants.append(numbered.group(1))
    result = []
    seen = set()
    for variant in variants:
        variant = variant.strip()
        if len(variant) < 3 or variant in seen:
            continue
        seen.add(variant)
        result.append(variant)
    return result


def split_by_toc(text: str, lines: list[Line], toc_items: list[dict[str, object]]) -> list[dict[str, object]]:
    norm_text, mapping = normalized_index(text)
    points: list[tuple[int, str, int, float]] = []
    search_from = 0
    for item in toc_items:
        label = normalize_space(str(item.get("label", "")))
        if not label:
            continue
        found_pos = -1
        found_variant = ""
        for variant in anchor_variants(label):
            pos = norm_text.find(variant, search_from)
            if pos < 0 and points:
                pos = norm_text.find(variant, mapping_to_norm_pos(norm_text, mapping, points[-1][0]))
            if pos >= 0:
                found_pos = pos
                found_variant = variant
                break
        if found_pos < 0:
            continue
        char_start = mapping[found_pos]
        if points and char_start <= points[-1][0] + 20:
            continue
        confidence = 0.92 if found_variant == normalize_for_match(label) else 0.78
        level = int(item.get("level", 1) or 1)
        points.append((char_start, label, level, confidence))
        search_from = min(len(norm_text), found_pos + max(1, len(found_variant)))

    if len(points) < 2:
        return []

    segments = []
    for idx, (start, title, level, confidence) in enumerate(points):
        end = points[idx + 1][0] if idx + 1 < len(points) else len(text)
        if end <= start:
            continue
        body = text[start:end].strip()
        if not body:
            continue
        segments.append(
            {
                "title": title,
                "char_start": start,
                "char_end": end,
                "line_start": line_for_char(lines, start),
                "line_end": line_for_char(lines, max(start, end - 1)),
                "text": body,
                "confidence": confidence,
                "method": "toc",
                "toc_level": level,
            }
        )
    return segments


def mapping_to_norm_pos(norm_text: str, mapping: list[int], char_pos: int) -> int:
    lo = 0
    hi = len(mapping) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if mapping[mid] < char_pos:
            lo = mid + 1
        else:
            hi = mid - 1
    return min(lo, len(norm_text))


def line_for_char(lines: list[Line], char_pos: int) -> int:
    lo = 0
    hi = len(lines) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        line = lines[mid]
        if line.start <= char_pos < line.end:
            return line.number
        if char_pos < line.start:
            hi = mid - 1
        else:
            lo = mid + 1
    return lines[-1].number


def split_by_words(text: str, lines: list[Line], max_words: int, overlap_words: int) -> list[dict[str, object]]:
    matches = list(re.finditer(r"\S+", text))
    if not matches:
        return []
    segments = []
    idx = 0
    while idx < len(matches):
        end_idx = min(len(matches), idx + max_words)
        start_char = matches[idx].start()
        end_char = matches[end_idx - 1].end()
        seg_no = len(segments) + 1
        segments.append(
            {
                "title": f"Segment {seg_no}",
                "char_start": start_char,
                "char_end": end_char,
                "line_start": line_for_char(lines, start_char),
                "line_end": line_for_char(lines, end_char - 1),
                "text": text[start_char:end_char].strip(),
                "confidence": 0.55,
                "method": "word-window",
            }
        )
        if end_idx >= len(matches):
            break
        idx = max(end_idx - overlap_words, idx + 1)
    return segments


def slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value, flags=re.IGNORECASE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:48] or "segment"


def apply_content_hints(text: str, decision: dict[str, object]) -> tuple[str, int]:
    start = 0
    end = len(text)
    start_hint = decision.get("content_start_hint")
    end_hint = decision.get("content_end_hint")
    if isinstance(start_hint, dict):
        try:
            start = max(0, min(len(text), int(start_hint.get("char_start", 0))))
        except (TypeError, ValueError):
            start = 0
    if isinstance(end_hint, dict):
        try:
            end = max(start, min(len(text), int(end_hint.get("char_end", len(text)))))
        except (TypeError, ValueError):
            end = len(text)
    return text[start:end], start


def shift_segments(segments: list[dict[str, object]], char_offset: int, lines: list[Line]) -> None:
    if char_offset == 0:
        return
    for seg in segments:
        seg["char_start"] = int(seg["char_start"]) + char_offset
        seg["char_end"] = int(seg["char_end"]) + char_offset
        seg["line_start"] = line_for_char(lines, int(seg["char_start"]))
        seg["line_end"] = line_for_char(lines, max(int(seg["char_start"]), int(seg["char_end"]) - 1))


def write_segments(out_dir: Path, source_file: Path, segments: list[dict[str, object]], decision: dict[str, object]) -> None:
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    outline_segments = []

    for idx, seg in enumerate(segments, start=1):
        seg_id = f"seg-{idx:03d}"
        filename = f"{seg_id}-{slug(str(seg['title']))}.md"
        path = chunks_dir / filename
        header = {
            "segment_id": seg_id,
            "title": seg["title"],
            "char_start": seg["char_start"],
            "char_end": seg["char_end"],
            "line_start": seg["line_start"],
            "line_end": seg["line_end"],
            "confidence": seg["confidence"],
            "method": seg["method"],
            "source_file": str(source_file.resolve()),
        }
        if "toc_level" in seg:
            header["toc_level"] = seg["toc_level"]
        path.write_text(
            "<!-- segment-metadata\n"
            + json.dumps(header, ensure_ascii=False, indent=2)
            + "\n-->\n\n"
            + str(seg["text"])
            + "\n",
            encoding="utf-8",
        )
        outline_segments.append({**header, "file": str(path)})

    (out_dir / "outline.json").write_text(
        json.dumps(
            {
                "source_file": str(source_file.resolve()),
                "segment_count": len(outline_segments),
                "model_decision": decision,
                "segments": outline_segments,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-words", type=int, default=1800)
    parser.add_argument("--overlap-words", type=int, default=80)
    parser.add_argument("--min-headings", type=int, default=3)
    parser.add_argument("--decision")
    args = parser.parse_args()

    source = Path(args.input)
    text = source.read_text(encoding="utf-8", errors="replace")
    original_lines = build_lines(text)
    decision = load_decision(args.decision)
    scoped_text, char_offset = apply_content_hints(text, decision)
    lines = build_lines(scoped_text)
    toc_items = toc_items_from_decision(decision)
    segments = split_by_toc(scoped_text, lines, toc_items) if toc_items else []
    if segments:
        method = "toc"
    else:
        headings = find_headings(lines, decision)
        configured_patterns = bool(decision.get("chapter_patterns") or decision.get("section_patterns"))
        min_headings = 1 if configured_patterns else args.min_headings
        if len(headings) >= min_headings:
            segments = split_by_headings(scoped_text, lines, headings)
            method = "headings"
        else:
            segments = split_by_words(scoped_text, lines, args.max_words, args.overlap_words)
            method = "word windows"
    shift_segments(segments, char_offset, original_lines)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_segments(out_dir, source, segments, decision)
    print(f"Segments: {len(segments)} ({method})")
    print(f"Outline: {out_dir / 'outline.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
