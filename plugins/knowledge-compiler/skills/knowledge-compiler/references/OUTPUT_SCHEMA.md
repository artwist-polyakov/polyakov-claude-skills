# Output Schema

Готовый навык создаётся как обычный Claude Code skill:

```text
dist/<skill-name>/
├── SKILL.md
└── references/
    ├── concepts.md
    ├── decision-rules.md
    ├── playbooks.md
    ├── anti-patterns.md
    ├── glossary.md
    ├── source-map.json
    └── knowledge-manifest.json
```

## SKILL.md

Главный файл должен быть коротким рабочим протоколом:

- для каких задач использовать навык;
- как агент должен думать с материалом;
- какие reference-файлы читать для разных вопросов;
- ограничения и предупреждения;
- не больше 2000 слов.

Не превращай `SKILL.md` в пересказ книги. Главные детали живут в `references/`.

## concepts.md

Карточки понятий:

```markdown
## concept-id

**Смысл:** одно-два предложения.
**Когда применять:** ситуации.
**Как распознать:** признаки в задаче пользователя.
**Связано:** другие concept-id.
**Источник:** segment_id, заголовок, строки.
```

## decision-rules.md

Правила принятия решений:

```markdown
## rule-id

Если <условие>, то <действие>, потому что <принцип>.

- Проверь: контрольные вопросы.
- Не делай: типовая ошибка.
- Источник: segment_id.
```

## playbooks.md

Плейбук — прикладной сценарий, который можно выполнить:

```markdown
## playbook-id

**Задача:** где помогает.
**Шаги:** 3-7 действий.
**Выход:** что должно получиться.
**Контроль качества:** как понять, что сработало.
**Источник:** segment_id.
```

## source-map.json

Минимальная схема:

```json
{
  "source_id": "sha256:<hash>",
  "segments": [
    {
      "segment_id": "seg-001",
      "title": "Глава 1",
      "char_start": 0,
      "char_end": 12000,
      "line_start": 1,
      "line_end": 340,
      "confidence": 0.82
    }
  ],
  "claims": [
    {
      "claim_id": "concept-event-sourcing",
      "artifact": "references/concepts.md",
      "source_segments": ["seg-011"],
      "extraction_type": "synthesized",
      "verbatim_quote_words": 0,
      "confidence": 0.86
    }
  ]
}
```

`extraction_type`:

- `synthesized` — переработанная идея;
- `named-framework` — сохранено авторское название метода;
- `short-quote` — короткая цитата, если она действительно нужна;
- `inferred` — вывод агента на основе нескольких мест источника.

## knowledge-manifest.json

Фиксирует происхождение:

```json
{
  "title": "Название",
  "author": "Автор",
  "scope": "individual",
  "source_file": "/path/to/file.pdf",
  "source_sha256": "sha256:...",
  "extraction_method": "pdftotext",
  "epub_title": "Название из OPF, если есть",
  "epub_author": "Автор из OPF, если есть",
  "epub_toc_source": "ncx",
  "created_at": "2026-05-07T12:00:00Z",
  "limitations": ["OCR not performed"]
}
```
