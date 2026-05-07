#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Build compact outline hint artifacts for an agent or subagent."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


MARKER_RE = re.compile(
    r"(оглавлен|содержан|глава|часть|раздел|chapter|contents|table of contents|part|section|index|references|bibliography|литератур)",
    re.IGNORECASE,
)

HEADING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("markdown", re.compile(r"^\s{0,3}#{1,6}\s+\S")),
    ("ru-chapter", re.compile(r"^\s*(глава|часть|раздел)\s+([0-9ivxlcdm]+|[а-яёa-z]+)\b", re.IGNORECASE)),
    ("en-chapter", re.compile(r"^\s*(chapter|part|section)\s+([0-9ivxlcdm]+|[a-z]+)\b", re.IGNORECASE)),
    ("numbered", re.compile(r"^\s*[0-9]{1,2}(\.[0-9]{1,2}){0,3}\s+.{3,140}$")),
    ("roman", re.compile(r"^\s*[IVXLCDM]{1,8}\.\s+.{3,140}$")),
]


def line_offsets(text: str) -> list[int]:
    offsets = []
    pos = 0
    for line in text.splitlines(keepends=True):
        offsets.append(pos)
        pos += len(line)
    return offsets


def normalize_line(line: str) -> str:
    line = line.replace("\u00a0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", line).strip()


def load_neighbor_metadata(source: Path) -> dict[str, object]:
    meta_path = source.parent / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def candidate_headings(lines: list[str], offsets: list[int], limit: int) -> list[dict[str, object]]:
    candidates = []
    seen = set()
    for idx, line in enumerate(lines, start=1):
        clean = normalize_line(line)
        if not (3 <= len(clean) <= 160):
            continue
        for reason, pattern in HEADING_PATTERNS:
            if pattern.search(clean):
                key = (idx, clean)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "line": idx,
                        "char": offsets[idx - 1],
                        "text": clean,
                        "reason": reason,
                    }
                )
                break
        if len(candidates) >= limit:
            break
    return candidates


def marker_hits(lines: list[str], offsets: list[int], limit: int) -> list[dict[str, object]]:
    hits = []
    for idx, line in enumerate(lines, start=1):
        clean = normalize_line(line)
        if not clean or len(clean) > 220:
            continue
        match = MARKER_RE.search(clean)
        if not match:
            continue
        hits.append(
            {
                "line": idx,
                "char": offsets[idx - 1],
                "marker": match.group(0),
                "text": clean,
            }
        )
        if len(hits) >= limit:
            break
    return hits


def sample_window(text: str, start: int, size: int) -> str:
    start = max(0, min(start, len(text)))
    return text[start : start + size]


def labels_from_hits(hits: list[dict[str, object]]) -> list[str]:
    labels = []
    seen = set()
    for hit in hits:
        marker = str(hit["marker"]).lower()
        if marker in seen:
            continue
        seen.add(marker)
        labels.append(str(hit["marker"]))
    return labels[:20]


def patterns_seen(candidates: list[dict[str, object]]) -> list[str]:
    values = []
    seen = set()
    for cand in candidates:
        reason = str(cand["reason"])
        if reason in seen:
            continue
        seen.add(reason)
        values.append(reason)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sample-chars", type=int, default=12000)
    parser.add_argument("--candidate-limit", type=int, default=160)
    parser.add_argument("--marker-limit", type=int, default=80)
    args = parser.parse_args()

    source = Path(args.input)
    text = source.read_text(encoding="utf-8", errors="replace")
    metadata = load_neighbor_metadata(source)
    lines = text.splitlines()
    offsets = line_offsets(text)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    head_path = out.with_name("outline-head.txt")
    tail_path = out.with_name("outline-tail.txt")
    candidates_path = out.with_name("outline-candidates.txt")
    windows_path = out.with_name("outline-marker-windows.txt")

    markers = marker_hits(lines, offsets, args.marker_limit)
    candidates = candidate_headings(lines, offsets, args.candidate_limit)

    head_path.write_text(text[: args.sample_chars], encoding="utf-8")
    tail_path.write_text(text[-args.sample_chars :] if text else "", encoding="utf-8")
    candidates_path.write_text(
        "\n".join(
            f"{item['line']}\t{item['char']}\t{item['reason']}\t{item['text']}"
            for item in candidates
        )
        + ("\n" if candidates else ""),
        encoding="utf-8",
    )

    window_lines = []
    for hit in markers[:12]:
        start = max(0, int(hit["char"]) - 1000)
        window_lines.append(
            f"--- near line {hit['line']} / marker {hit['marker']} / char {start} ---\n"
            + sample_window(text, start, 2200)
        )
    windows_path.write_text("\n\n".join(window_lines), encoding="utf-8")

    artifact = {
        "version": 1,
        "source_file": str(source.resolve()),
        "source_stats": {
            "chars": len(text),
            "words": len(text.split()),
            "lines": len(lines),
            "format": metadata.get("format", ""),
            "extraction_method": metadata.get("extraction_method", ""),
        },
        "sample_policy": {
            "head_chars": args.sample_chars,
            "tail_chars": args.sample_chars,
            "middle_omitted_chars": max(0, len(text) - args.sample_chars * 2),
        },
        "agent_task": (
            "Infer how this source marks the table of contents, parts, chapters, "
            "sections, appendices, and end matter. Return concise JSON with marker "
            "rules and segmentation advice. Do not summarize the source."
        ),
        "samples": {
            "head_path": str(head_path),
            "tail_path": str(tail_path),
            "candidates_path": str(candidates_path),
            "marker_windows_path": str(windows_path),
        },
        "script_observations": {
            "toc_label_candidates": labels_from_hits(markers),
            "heading_patterns_seen": patterns_seen(candidates),
            "heading_candidates_count": len(candidates),
            "marker_hits_count": len(markers),
            "epub_title": metadata.get("epub_title", ""),
            "epub_author": metadata.get("epub_author", ""),
            "epub_toc_source": metadata.get("epub_toc_source", ""),
            "epub_toc_items": metadata.get("epub_toc", [])[:200],
        },
        "marker_hits": markers,
        "candidate_headings": candidates[:40],
        "expected_agent_output": {
            "language": "ru|en|mixed|unknown",
            "domain": "technical|management|marketing|sales|product|education|legal|finance|medical|mixed|unknown",
            "structure_strategy": "toc_then_body_headings|body_headings|word_windows|mixed",
            "toc_markers": ["strings or regex-like descriptions"],
            "toc_items": [{"label": "chapter title from OPF/NCX/nav", "href": "chapter.xhtml"}],
            "chapter_markers": ["strings or regex-like descriptions"],
            "part_markers": ["strings or regex-like descriptions"],
            "chapter_patterns": ["^Глава\\s+\\d+"],
            "section_patterns": ["^\\d+\\.\\d+\\s+"],
            "exclude_patterns": ["^Библиография$"],
            "content_start_hint": {"char_start": 0, "confidence": 0.0},
            "content_end_hint": {"char_end": len(text), "confidence": 0.0},
            "confidence": 0.0,
            "notes": "short segmentation advice",
        },
    }

    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Outline hints: {out}")
    print(f"Head sample: {head_path}")
    print(f"Tail sample: {tail_path}")
    print(f"Candidates: {candidates_path}")
    print(f"Candidate headings: {len(artifact['candidate_headings'])}")
    print(f"Marker hits: {len(markers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
