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
    ├── source-index.md
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

## Тезисы и ссылки

Каждый тезис из `source-map.json` имеет уникальный `claim_id`: строчные латинские буквы и цифры, слова разделены одиночными дефисами, например `concept-event-sourcing`. В файле, указанном в `artifact`, должен быть ровно один заголовок второго уровня `## <claim_id>` вне блоков кода и HTML-комментариев. Другие заголовки не должны давать тот же якорь: например, `# Concept Event Sourcing` конфликтует с `## concept-event-sourcing`. Отдельные поля для заголовка и якоря не нужны.

В файлах непосредственно внутри `references/` оформляй источник ссылкой: `[seg-001](source-index.md#seg-001)`. Из `SKILL.md` путь будет `references/source-index.md#seg-001`. Для вложенных файлов подстрой относительный путь. Номер сегмента должен существовать в карте источников.

## concepts.md

Карточки понятий:

```markdown
## concept-id

**Смысл:** одно-два предложения.
**Когда применять:** ситуации.
**Как распознать:** признаки в задаче пользователя.
**Связано:** другие concept-id.
**Источник:** [seg-001](source-index.md#seg-001).
```

## decision-rules.md

Правила принятия решений:

```markdown
## rule-id

Если <условие>, то <действие>, потому что <принцип>.

- Проверь: контрольные вопросы.
- Не делай: типовая ошибка.
- Источник: [seg-001](source-index.md#seg-001).
```

## playbooks.md

Плейбук — прикладной сценарий, который можно выполнить:

```markdown
## playbook-id

**Задача:** где помогает.
**Шаги:** 3-7 действий.
**Выход:** что должно получиться.
**Контроль качества:** как понять, что сработало.
**Источник:** [seg-001](source-index.md#seg-001).
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
      "source_segments": ["seg-001"],
      "extraction_type": "synthesized",
      "verbatim_quote_words": 0,
      "confidence": 0.86
    }
  ]
}
```

`segments`, `claims` и `source_segments` — непустые списки. `segment_id` имеет вид `seg-<число>` и уникален. `source_segments` содержит ссылки на существующие сегменты. `artifact` — относительный путь к существующему Markdown-файлу внутри `references/`, кроме производного `source-index.md`. Для примера выше в `references/concepts.md` нужен заголовок `## concept-event-sourcing`.

У сегмента обязательны непустое название, целочисленные координаты и `confidence` от 0 до 1. Символы считаются с 0, диапазон `[char_start, char_end)` непустой; строки — с 1, обе границы включены. Уверенность сегмента описывает разметку, а не достоверность связанных тезисов.

`extraction_type`:

- `synthesized` — переработанная идея;
- `named-framework` — сохранено авторское название метода;
- `short-quote` — короткая цитата, если она действительно нужна;
- `inferred` — вывод агента на основе нескольких мест источника.

## source-index.md

Указатель генерирует `quality_gate.py --write-source-index` из заполненного `source-map.json` после успешных проверок. Заготовка навыка его не создаёт; вручную указатель не редактируй.

Для каждого сегмента он содержит якорь `seg-*`, название, диапазоны строк и символов, уверенность и относительные ссылки на связанные тезисы в готовом навыке. Координаты относятся к извлечённому `source.txt`, а не к страницам PDF. Исходный текст, фрагменты источника и абсолютные локальные пути в указатель не входят; сам источник остаётся в кеше сборки.

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
