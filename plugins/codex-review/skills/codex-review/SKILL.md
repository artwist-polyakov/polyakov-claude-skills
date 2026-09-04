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

## CRITICAL: Sandbox

Codex CLI использует macOS system API (SCDynamicStore), которые блокируются sandbox Claude Code. **Все вызовы codex-review.sh и codex-state.sh ОБЯЗАНЫ выполняться с `dangerouslyDisableSandbox: true`** в Bash tool. Без этого codex крашится с паникой Rust.

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

Передай путь к файлу плана через `--plan-file`. НЕ вставляй содержимое плана в аргумент командной строки — скрипт сам читает файл и отдаёт содержимое Codex на stdin, поэтому размер плана ничем не ограничен.

#### С plan mode

Если используешь plan mode — отправь план на ревью **перед** `ExitPlanMode`:
1. Написал план → CC сохраняет его в `~/.claude/plans/<slug>.md` (автоматически)
2. Передай этот путь в `--plan-file`:
   ```bash
   bash scripts/codex-review.sh plan --plan-file ~/.claude/plans/<slug>.md
   ```
3. `CHANGES_REQUESTED` → скорректируй план в файле, отправь снова (см. «Accept or Argue»)
4. `APPROVED` → обработай `## Non-blocking` и `## Pre-existing` (см. «Разделы ответа ревьюера»), затем вызови `ExitPlanMode` для одобрения пользователем

Таким образом план проходит два ревью: техническое (Codex) и бизнес-приоритетное (пользователь).

#### Без plan mode

Если план написан в отдельный файл внутри проекта:
```bash
bash scripts/codex-review.sh plan --plan-file docs/plan.md
```

#### Шаблон плана (рекомендуемая структура файла)

```
What: [problem being solved]
Approach: [chosen approach and why]
Alternatives considered: [what was rejected and why]
Files to change: [list]
Addressed concerns: [if resubmit — point-by-point from previous review]
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

#### Описание из файла

Если в описании есть обратные кавычки (обычное дело — так пишут имена функций и полей), `$` или `$(...)` — передавай текст файлом, а не аргументом: шелл выполняет такие фрагменты внутри двойных кавычек, и до Codex доходит искажённый текст либо вызов падает с `command not found`.

```bash
bash scripts/codex-review.sh code --description-file /path/to/description.md
```

Опция работает для `init`, `plan` и `code`; на `plan` она называет файл плана — то же, что `--plan-file`. Одновременно с `--plan-file` или с описанием в аргументе — ошибка. Файл читается до последнего байта: пустые строки в конце и отсутствие перевода строки в конце сохраняются.

Отправленный текст сохраняется рядом с логом попытки байт в байт: `codex-<phase>-<N>.request.md` для ревью, `codex-init.request.md` для сессии.

#### Название задачи для `init`

`state.json` хранит одну строку — название задачи, которое видно в `STATUS.md` и в сводке архива. Название даёшь ты сам:

```bash
bash scripts/codex-review.sh init --description-file task.md --task-label "JWT auth: middleware + refresh endpoint"
```

Правила: одна строка, без двойных кавычек, обратных слешей и управляющих символов, без пробелов в начале и в конце, до 200 символов. Название сохраняется ровно таким, каким передано: скрипт ничего не переписывает и не подчищает, а отклоняет с объяснением. Пустое значение в `--task-label` — ошибка. Если описание задачи занимает больше одной строки, а `--task-label` не передан — запуск завершается ошибкой, ничего не создаётся: назови задачу сам, а не рассчитывай, что скрипт угадает. При однострочном описании название берётся из него.

Полный текст задачи в `state.json` не попадает — он целиком уходит в Codex и лежит в `codex-init.request.md`.

### 5. Управление состоянием

```bash
bash scripts/codex-state.sh show              # Текущее состояние
bash scripts/codex-state.sh dir               # Путь к state-каталогу текущей ветки
bash scripts/codex-state.sh reset             # Сброс итераций (session сохраняется)
bash scripts/codex-state.sh reset --full      # Полный сброс
bash scripts/codex-state.sh get session_id    # Получить поле
bash scripts/codex-state.sh set session_id <val>  # Установить вручную
bash scripts/codex-state.sh set phase implementing  # Обновить фазу
bash scripts/codex-state.sh set iteration 2       # Откатить счётчик кругов
```

`get` возвращает значение поля как оно записано: пустое строковое поле — пустой
строкой, счётчик — числом. Незнакомое имя поля — ошибка с кодом возврата 1.
Особые имена: `session_id` (приоритет у `config.env`) и `verdict` (из
`verdict.txt`).

`set` принимает поля `session_id`, `phase`, `iteration`, `max_iterations`,
`reviews_completed`, `last_review_status`, `last_review_timestamp`,
`task_description`. Неизвестное имя поля, нецелое значение счётчика и значение
с кавычкой, обратным слэшем, переводом строки или табуляцией — ошибка с кодом
возврата 1, состояние не меняется.

Для чтения файлов ревью (notes, STATUS.md и пр.) используй `codex-state.sh dir` — он вернёт абсолютный путь к каталогу текущей ветки.

## Обработка exit-кодов

| Exit | Status | Действие |
|------|--------|----------|
| 0 | APPROVED | Обработай `## Non-blocking` и `## Pre-existing` (см. «Разделы ответа ревьюера»), затем продолжай работу |
| 0 | CHANGES_REQUESTED | Скорректируй и отправь снова (см. «Accept or Argue») |
| 1 | ERROR | Сообщи об ошибке, предложи проверить session_id |
| 2 | ESCALATE | Оповести пользователя, выведи краткое резюме, предложи варианты (см. «Обработка ESCALATE») |
| 3 | NO_SESSION | Спроси: создать сессию через `init`? |

При `ERROR` круг не состоялся: итерация не израсходована, тот же запрос
можно отправить повторно. Итерацию расходует прогон, который вернул вердикт,
— даже если сам запуск завершился ошибкой после этого.

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

Codex пишет свой вердикт в `verdict.txt` внутри state-каталога ветки (одно слово: `APPROVED` или `CHANGES_REQUESTED`). **Для чтения вердикта используй `bash scripts/codex-state.sh get verdict`** — helper возвращает `APPROVED`, `CHANGES_REQUESTED` или пустую строку (нет/невалидно). Файл очищается перед каждым запросом ревью, поэтому слово в нём написано текущим кругом. Вердикт берётся только отсюда: текст ответа на решение не влияет. Если после прогона в файле нет `APPROVED` или `CHANGES_REQUESTED` — круг не состоялся: скрипт возвращает `ERROR` (exit 1), итерация не расходуется, ответ ревьюера сохраняется рядом с логом попытки как `codex-<фаза>-<N>.reply.md`. Рядом с вердиктом лежит `verdict.phase` — одно слово, `plan` или `code`:
фаза, которая его получила. Оба файла удаляются вместе.

Плагинный хук `ExitPlanMode` связывает вердикт с текущей Claude-сессией через
`current_session.txt` в том же каталоге — verdict, пришедший из другой сессии,
удаляется. Хук пропускает `ExitPlanMode` только при `APPROVED` с пометкой
`plan`. Вердикт с пометкой `code` или без пометки хук удаляет и отвечает
отказом с указанием запустить ревью плана.

## Разделы ответа ревьюера

Ответ ревьюера разложен на три раздела. Что делать с каждым:

| Раздел | Действие |
|---|---|
| `## Blocking` | исправь или оспорь каждый пункт — см. «Accept or Argue» |
| `## Non-blocking` | не исправляй по своей инициативе; вынеси список пользователю через `AskUserQuestion` (взять в работу / отдельной задачей / не делать) |
| `## Pre-existing` | вынеси каждый пункт уровня `critical` пользователю через `AskUserQuestion` (чинить сейчас / отдельной задачей / не чинить) до того, как объявишь ревью законченным; остальные пункты — вместе с `## Non-blocking` |

При `APPROVED` с непустым `## Non-blocking` работа считается принятой: отправлять новый круг ревью из-за этих пунктов не нужно.

## Правила

- НИКОГДА не вызывай `codex exec` напрямую — только через скрипты `codex-review.sh` и `codex-state.sh`. Скрипты сами знают модель, конфиг и session_id
- Описывай ЧТО ты сделал и ПОЧЕМУ, какие решения принимал — используй шаблоны описания
- НЕ передавай git diff — Codex сам посмотрит, он в той же директории
- APPROVED → обработай `## Non-blocking` и `## Pre-existing`, затем продолжай работу
- Перед реализацией вызови `codex-state.sh set phase implementing`
- Есть заказчик (пользователь) — уточняй у него неоднозначные вопросы
- Опция `--max-iter N` позволяет изменить лимит итераций

### Worktree & Branch Isolation

Состояние ревью изолировано по ветке. Скрипты автоматически определяют основной репозиторий и текущую ветку. Параллельная работа на нескольких ветках/worktrees безопасна. `config.env` — общий (в корне `.codex-review/`). Для получения пути к state-каталогу текущей ветки используй `codex-state.sh dir`.

### Auto-Workflow (AUTO_REVIEW=true)

When `AUTO_REVIEW=true` in `.codex-review/config.env`, the entire review cycle runs automatically. A plugin hook blocks `ExitPlanMode` until Codex approves the plan.

#### Plan phase

1. Write the plan in plan mode as usual
2. Before calling `ExitPlanMode`, run review:
   ```bash
   bash scripts/codex-review.sh init "task description"   # ALWAYS init for a new plan — archives previous session
   bash scripts/codex-review.sh plan --plan-file ~/.claude/plans/<slug>.md
   ```
   **IMPORTANT**: Always run `init` before the first `plan` review in a conversation. Even if `codex-state.sh show` reports an existing session, it may be stale (from a previous conversation). The `init` command safely archives the old session and creates a fresh one. Only skip `init` when re-submitting after `CHANGES_REQUESTED` within the same review cycle.
3. **Formal verdict check** — run `bash scripts/codex-state.sh get verdict`. Proceed ONLY if it outputs the exact string `APPROVED`. Do NOT interpret review text — only the helper output matters.
4. `CHANGES_REQUESTED` → fix the plan, resubmit (follow «Accept or Argue» rules). Iterate automatically up to the iteration limit.
5. `APPROVED` → handle `## Non-blocking` and `## Pre-existing` first (see «Разделы ответа ревьюера»), then call `ExitPlanMode` (the hook auto-approves it)

#### Implementation phase

6. Implement as usual. Set phase: `bash scripts/codex-state.sh set phase implementing`

#### Code phase

7. After implementation, run code review:
   ```bash
   bash scripts/codex-review.sh code "code description"
   # or, when the description contains backticks / `$` / `$(...)`:
   bash scripts/codex-review.sh code --description-file /path/to/description.md
   ```
8. **Formal verdict check** — same as step 3: run `bash scripts/codex-state.sh get verdict` and check for exact string `APPROVED`.
9. `CHANGES_REQUESTED` → fix code, resubmit automatically.
10. `APPROVED` → handle `## Non-blocking` and `## Pre-existing` (see «Разделы ответа ревьюера»), then report to user.

#### ESCALATE handling in auto mode

Same as standard ESCALATE handling — present summary and ask user via `AskUserQuestion`.

### Accept or Argue

При получении CHANGES_REQUESTED:

1. Прочитай предыдущую review note из `$(bash scripts/codex-state.sh dir)/notes/{phase}-review-{N}.md`
2. Критически оцени каждый пункт раздела `## Blocking`. В описании к повторной отправке адресуй каждый из них поточечно:
   - **Исправлено**: [что именно исправил и как]
   - **Не согласен**: [контраргумент с обоснованием — Codex видит историю и может принять или настоять]
   - **Отложено**: [причина — только с согласия пользователя через AskUserQuestion]
3. Запиши в то же описание решение пользователя по пунктам `## Non-blocking` и `## Pre-existing`
4. Если один и тот же пункт `## Blocking` повторяется 2+ раза без нового содержания (Codex настаивает, ты уже аргументировал) — эскалируй пользователю через AskUserQuestion: покажи замечание, свои аргументы, и спроси решение
5. Если два круга подряд весь `## Blocking` состоит из дефектов в коде, который породили правки прошлых кругов, — останови круги и спроси пользователя через AskUserQuestion: ещё круг правок или откат механизма к состоянию до ревью
6. При исчерпании лимита итераций — следуй процедуре «Обработка ESCALATE»
