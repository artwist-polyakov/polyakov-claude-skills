#!/bin/sh
# Create a portable skill skeleton in dist/<skill-name>.

set -e

NAME=""
TITLE=""
AUTHOR=""
JOB_DIR=""
OUTPUT_DIR="dist"
UV_BIN="${UV:-uv}"

usage() {
    cat <<'EOF'
Usage:
  sh scripts/build_skill_skeleton.sh --name <skill-name> --title <title> --author <author> --job-dir <cache/jobs/id> [--output-dir dist]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --name) NAME="${2:-}"; shift 2 ;;
        --title) TITLE="${2:-}"; shift 2 ;;
        --author) AUTHOR="${2:-}"; shift 2 ;;
        --job-dir) JOB_DIR="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-dist}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$NAME" ] || [ -z "$TITLE" ] || [ -z "$JOB_DIR" ]; then
    echo "Missing required arguments" >&2
    usage >&2
    exit 2
fi

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    echo "Error: uv not found. Install uv to run Python helpers." >&2
    exit 1
fi

case "$NAME" in
    *[!a-z0-9_-]*|"")
        echo "Invalid --name. Use lowercase letters, numbers, '-' or '_'." >&2
        exit 2
        ;;
esac

DEST="$OUTPUT_DIR/$NAME"
if [ -e "$DEST" ]; then
    echo "Output already exists: $DEST" >&2
    exit 1
fi

mkdir -p "$DEST/references"

cat > "$DEST/SKILL.md" <<EOF
---
name: $NAME
description: |
  Личный навык по источнику "$TITLE".
  Используй, когда нужно применять идеи, понятия, решающие правила
  и плейбуки из этого материала к рабочей задаче пользователя.
---

# $TITLE

Это личная прикладная карта источника${AUTHOR:+: $AUTHOR}. Используй её как советника по материалу, а не как замену исходного текста.

## Как работать

1. Для терминов и основных идей читай \`references/concepts.md\` и \`references/glossary.md\`.
2. Для выбора действия читай \`references/decision-rules.md\`.
3. Для пошагового применения читай \`references/playbooks.md\`.
4. Для рисков и ошибок читай \`references/anti-patterns.md\`.
5. Для проверки происхождения тезисов используй \`references/source-map.json\`.

## Правило ответа

Отвечай применимо к задаче пользователя: сначала диагноз ситуации, затем правило или плейбук, затем проверка результата. Если источник не покрывает вопрос, скажи это явно и отдели собственный вывод от материала источника.

## Ограничения

Навык содержит переработанную карту знаний для индивидуального использования. Не цитируй длинные фрагменты источника и не воспроизводи таблицы, схемы или листинги без явного основания.
EOF

cat > "$DEST/references/concepts.md" <<'EOF'
# Concepts

Заполни карточками ключевых понятий в формате:

## concept-id

**Смысл:** кратко.
**Когда применять:** ситуация.
**Как распознать:** признаки.
**Связано:** другие concept-id.
**Источник:** segment_id.
EOF

cat > "$DEST/references/decision-rules.md" <<'EOF'
# Decision Rules

Заполни правилами вида: если условие, то действие, потому что принцип источника.
EOF

cat > "$DEST/references/playbooks.md" <<'EOF'
# Playbooks

Заполни прикладными сценариями: задача, шаги, выход, контроль качества, источник.
EOF

cat > "$DEST/references/anti-patterns.md" <<'EOF'
# Anti-patterns

Заполни типовыми ошибками, признаками риска и корректирующими действиями.
EOF

cat > "$DEST/references/glossary.md" <<'EOF'
# Glossary

Заполни важными терминами и короткими определениями со ссылкой на segment_id.
EOF

"$UV_BIN" run python - "$DEST" "$JOB_DIR" "$TITLE" "$AUTHOR" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

dest = Path(sys.argv[1])
job_dir = Path(sys.argv[2])
title = sys.argv[3]
author = sys.argv[4]
meta_path = job_dir / "metadata.json"
outline_path = job_dir / "segments" / "outline.json"
meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
outline = json.loads(outline_path.read_text(encoding="utf-8")) if outline_path.exists() else {"segments": []}

manifest = {
    "title": title,
    "author": author,
    "scope": "individual",
    "source_file": meta.get("source_file"),
    "source_sha256": meta.get("source_sha256"),
    "extraction_method": meta.get("extraction_method"),
    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "limitations": meta.get("warnings", []),
}
(dest / "references" / "knowledge-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

source_map = {
    "source_id": meta.get("source_sha256"),
    "segments": [
        {
            "segment_id": seg.get("segment_id"),
            "title": seg.get("title"),
            "char_start": seg.get("char_start"),
            "char_end": seg.get("char_end"),
            "line_start": seg.get("line_start"),
            "line_end": seg.get("line_end"),
            "confidence": seg.get("confidence"),
        }
        for seg in outline.get("segments", [])
    ],
    "claims": [],
}
(dest / "references" / "source-map.json").write_text(
    json.dumps(source_map, ensure_ascii=False, indent=2), encoding="utf-8"
)
PY

echo "Skill skeleton: $DEST"
