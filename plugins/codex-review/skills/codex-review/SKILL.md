---
name: codex-review
description: |
  Workflow кросс-агентного ревью с Codex.
  Triggers (RU): "кодекс ревью".
  Triggers (EN): "with codex review", "codex review workflow",
  "start codex review".
  ВАЖНО: при срабатывании триггера прочитай SKILL.md до любых других шагов.
---

# Codex Review Workflow

Кросс-агентное ревью: Claude реализует, Codex (GPT) ревьюит. Codex работает в той же директории и может самостоятельно смотреть код.

## Расположение скриптов

Скрипты лежат в `scripts/` рядом с этим SKILL.md. Определи полный путь:
- Этот файл: путь из которого ты прочитал SKILL.md
- Скрипты: замени `SKILL.md` на `scripts/codex-review.sh` (и `scripts/codex-state.sh`)

Все команды ниже используют относительный `scripts/` — подставь полный путь при вызове.

## Workflow

### 1. Инициализация сессии

Создай сессию Codex с описанием задачи.

```bash
bash scripts/codex-review.sh init "Implement JWT authentication for API"
```

Сессия может быть также задана вручную в `.codex-review/config.env`: `CODEX_SESSION_ID=sess_...`

Если сессии нет (exit 3 — NO_SESSION), спроси пользователя:
- Есть ли уже живая сессия с Codex? → пусть впишет id в config.env
- Или создать новую через `init`?

### 2. Ревью плана

Опиши ЧТО собираешься делать, КАКОЙ подход выбрал и ПОЧЕМУ. Включи **полное содержимое плана** в аргумент — Codex НЕ имеет доступа к файлам вне папки проекта.

Если используешь plan mode — отправь план на ревью **перед** `ExitPlanMode`:
1. Написал план в файл
2. Отправь содержимое плана целиком через `codex-review.sh plan`
3. `CHANGES_REQUESTED` → скорректируй план, отправь снова (см. «Accept or Argue»)
4. `APPROVED` → вызови `ExitPlanMode` для одобрения пользователем

Таким образом план проходит два ревью: техническое (Codex) и бизнес-приоритетное (пользователь).

#### Шаблон описания плана

```
What: [problem being solved]
Approach: [chosen approach and why]
Alternatives considered: [what was rejected and why]
Files to change: [list]
Addressed concerns: [if resubmit — point-by-point from previous review]
```

```bash
bash scripts/codex-review.sh plan "What: JWT authentication for API. Approach: middleware validates token, refresh via separate endpoint. Alternatives: session-based rejected (API is stateless). Files: auth/jwt.py, api/auth.py, tests/test_auth.py"
```

### 3. Реализация

Перед началом реализации обнови фазу:

```bash
bash scripts/codex-state.sh set phase implementing
```

Имплементируй по утвержденному плану.

### 4. Ревью кода

Опиши ЧТО сделал, КАКИЕ решения принимал. НЕ передавай git diff — Codex сам посмотрит.

#### Шаблон описания кода

```
What changed: [summary of changes]
Key decisions: [non-obvious decisions made during implementation]
Files modified: [list with brief description per file]
Tests: [what tests were added/run, results]
Addressed concerns: [if resubmit — point-by-point from previous review]
```

```bash
bash scripts/codex-review.sh code "What changed: JWT auth middleware + refresh endpoint. Key decisions: RS256 over HS256 for key rotation. Files: auth/jwt.py (middleware), api/auth.py (refresh endpoint). Tests: 3 new tests (expired/invalid/valid tokens), all pass."
```

### 5. Управление состоянием

```bash
bash scripts/codex-state.sh show              # Текущее состояние
bash scripts/codex-state.sh dir               # Путь к state-каталогу текущей ветки
bash scripts/codex-state.sh reset             # Сброс итераций (session сохраняется)
bash scripts/codex-state.sh reset --full      # Полный сброс
bash scripts/codex-state.sh get session_id    # Получить поле
bash scripts/codex-state.sh set session_id <val>  # Установить вручную
bash scripts/codex-state.sh set phase implementing  # Обновить фазу
```

Для чтения файлов ревью (notes, STATUS.md и пр.) используй `codex-state.sh dir` — он вернёт абсолютный путь к каталогу текущей ветки.

## Обработка exit-кодов

| Exit | Status | Действие |
|------|--------|----------|
| 0 | APPROVED | Продолжай работу |
| 0 | CHANGES_REQUESTED | Скорректируй и отправь снова (см. «Accept or Argue») |
| 1 | ERROR | Сообщи об ошибке, предложи проверить session_id |
| 2 | ESCALATE | Оповести пользователя, выведи краткое резюме, предложи варианты (см. «Обработка ESCALATE») |
| 3 | NO_SESSION | Спроси: создать сессию через `init`? |

### Обработка ESCALATE (exit 2)

Когда лимит итераций исчерпан:

1. Получи путь: `STATE_DIR=$(bash scripts/codex-state.sh dir)`. Прочитай заметки ревью из `$STATE_DIR/notes/` (файлы `{phase}-review-{N}.md`)
2. Выведи пользователю краткое резюме:
   - Какой этап (plan/code), сколько итераций прошло
   - Ключевые замечания и статусы по каждой итерации (1-2 строки на итерацию)
3. Используй `AskUserQuestion` с тремя вариантами:
   - **Ещё одна итерация** — разово расширить лимит на 1
   - **Снять лимит** — убрать ограничение для этой сессии
   - **Прекратить ревью** — вывести финальное резюме и остановиться
   (Вариант «Свой вариант» добавляется автоматически)

Обработка ответа:
- «Ещё одна итерация» → повтори вызов `codex-review.sh {phase} "..." --max-iter $((текущий_лимит + 1))`
- «Снять лимит» → повтори вызов `codex-review.sh {phase} "..." --max-iter 999`
- «Прекратить ревью» → выведи финальное резюме и заверши процесс ревью
- Свой вариант → следуй инструкции пользователя

## STATUS.md

Файл `STATUS.md` в state-каталоге ветки (путь: `codex-state.sh dir`) создаётся и обновляется автоматически скриптами. Не редактируй его вручную.

- Файл **появляется** при `init` и обновляется при каждом `plan`/`code` и `codex-state.sh set`
- Файл **удаляется** при финальном APPROVED на этапе `code` и при `reset --full`
- Наличие файла = активное ревью, отсутствие = ревью не идёт

## Verdict

Codex пишет свой вердикт в `verdict.txt` внутри state-каталога ветки (одно слово: `APPROVED` или `CHANGES_REQUESTED`). Файл очищается перед каждым запросом ревью. Если Codex не создал файл — скрипт парсит вердикт из текста ответа (fallback).

## Правила

- НИКОГДА не вызывай `codex exec` напрямую — только через скрипты `codex-review.sh` и `codex-state.sh`. Скрипты сами знают модель, конфиг и session_id
- Описывай ЧТО ты сделал и ПОЧЕМУ, какие решения принимал — используй шаблоны описания
- НЕ передавай git diff — Codex сам посмотрит, он в той же директории
- APPROVED → продолжай работу
- Перед реализацией вызови `codex-state.sh set phase implementing`
- Есть заказчик (пользователь) — уточняй у него неоднозначные вопросы
- Опция `--max-iter N` позволяет изменить лимит итераций

### Worktree & Branch Isolation

Состояние ревью изолировано по ветке. Скрипты автоматически определяют основной репозиторий и текущую ветку. Параллельная работа на нескольких ветках/worktrees безопасна. `config.env` — общий (в корне `.codex-review/`). Для получения пути к state-каталогу текущей ветки используй `codex-state.sh dir`.

### Accept or Argue

При получении CHANGES_REQUESTED:

1. Прочитай предыдущую review note из `$(bash scripts/codex-state.sh dir)/notes/{phase}-review-{N}.md`
2. Критически оцени каждое замечание. В описании к повторной отправке ОБЯЗАТЕЛЬНО адресуй каждое замечание поточечно:
   - **Исправлено**: [что именно исправил и как]
   - **Не согласен**: [контраргумент с обоснованием — Codex видит историю и может принять или настоять]
   - **Отложено**: [причина — только с согласия пользователя через AskUserQuestion]
3. Если одно и то же замечание повторяется 2+ раза без нового содержания (Codex настаивает, ты уже аргументировал) — эскалируй пользователю через AskUserQuestion: покажи замечание, свои аргументы, и спроси решение
4. При исчерпании лимита итераций — следуй процедуре «Обработка ESCALATE»
