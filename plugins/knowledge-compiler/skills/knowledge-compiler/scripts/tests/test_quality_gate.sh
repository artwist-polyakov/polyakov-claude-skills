#!/bin/sh

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

OUT="$TMP_DIR/sample-skill"
mkdir -p "$OUT/references"

cat > "$OUT/SKILL.md" <<'EOF'
---
name: sample-skill
description: Личная карта источника для проверки.
---

# Sample Skill

Используй навык для выбора управленческого или маркетингового действия по карте источника.
EOF

cat > "$OUT/references/concepts.md" <<'EOF'
# Concepts

## diagnosis-before-action

**Смысл:** решение начинается с проверки ситуации.
**Источник:** seg-001.
EOF

cat > "$OUT/references/decision-rules.md" <<'EOF'
# Decision Rules

## choose-after-intent

Если задача про спрос, сначала проверь намерение, затем выбирай канал.
EOF

cat > "$OUT/references/playbooks.md" <<'EOF'
# Playbooks

## diagnose-task

1. Назови наблюдаемый факт.
2. Сформулируй гипотезу.
3. Определи проверку.
EOF

cat > "$OUT/references/anti-patterns.md" <<'EOF'
# Anti-patterns

## similar-is-target

Не считай похожий запрос целевым без проверки намерения.
EOF

cat > "$OUT/references/glossary.md" <<'EOF'
# Glossary

**Намерение:** то, что человек хочет сделать в ситуации выбора.
EOF

cat > "$OUT/references/source-map.json" <<'EOF'
{
  "source_id": "sha256:test",
  "segments": [],
  "claims": []
}
EOF

cat > "$OUT/references/knowledge-manifest.json" <<'EOF'
{
  "title": "Sample",
  "scope": "individual",
  "source_sha256": "sha256:test"
}
EOF

uv run --script "$SKILL_DIR/scripts/quality_gate.py" \
    --source "$TEST_DIR/fixtures/sample-source.txt" \
    --skill-dir "$OUT" \
    >/dev/null
