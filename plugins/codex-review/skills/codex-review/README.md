# Codex Review Plugin

Кросс-агентное ревью: Claude Code реализует, Codex (GPT) ревьюит.

> **ВАЖНО!** Скрипты плагина хранят всё состояние (сессию, конфиг, журнал ревью) в директории `.codex-review/` **в корне вашего проекта**, а не рядом с собой. Директория `config/` внутри плагина — это только шаблон. Не редактируйте файлы в директории установки плагина (`~/.claude/plugins/...`) — они перезапишутся при обновлении.

## Установка

### Вариант A: через marketplace (рекомендуется)

1. Добавь репозиторий как marketplace (один раз):

```bash
# Из локальной директории
claude plugin marketplace add /path/to/polyakov-claude-skills

# Или из GitHub
claude plugin marketplace add github:artwist-polyakov/polyakov-claude-skills
```

2. Установи плагин:

```bash
claude plugin install codex-review@polyakov-claude-skills
```

### Вариант B: для одной сессии

```bash
claude --plugin-dir /path/to/polyakov-claude-skills/plugins/codex-review
```

### Зависимости

Убедись, что `codex` CLI установлен:

```bash
npm install -g @openai/codex
```

## Настройка проекта

### .gitignore

Добавь в `.gitignore` (или `.git/info/exclude`) проекта:

```
.codex-review/
```

Весь каталог, включая `notes/`, содержит локальное состояние ревью и не должен попадать в Git.

Если проект следовал прежней рекомендации и уже отслеживает `notes/`, после обновления `.gitignore` убери каталог только из индекса Git:

```bash
git rm -r --cached --ignore-unmatch .codex-review/
```

Локальные файлы сохранятся. Закоммить подготовленные удаления вместе с новым правилом `.gitignore`.

### AGENTS.md (для Codex)

Добавь в `AGENTS.md` проекта секцию:

```markdown
## Review Protocol

Если ты выступаешь ревьювером (запущен через codex-review workflow):
- Давай конкретный actionable фидбек
- Можешь смотреть код/diff самостоятельно
- Не запускай скрипты из skills/codex-review/ — ты ревьюер
- Не заглядывай в .codex-review/archive/ — там артефакты прошлых сессий
- После ревью запиши вердикт по пути, указанному в промпте ревью (одно слово: APPROVED или CHANGES_REQUESTED)
```

### settings.local.json

Добавь разрешения в `.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(bash */codex-review.sh:*)",
      "Bash(bash */codex-state.sh:*)",
      "Bash(codex exec:*)"
    ]
  }
}
```

### Конфигурация (опционально)

Создай `.codex-review/config.env` в корне проекта:

```bash
# Существующая сессия Codex (или используй init для создания новой)
# CODEX_SESSION_ID=sess_your_session_id

CODEX_MODEL=gpt-5.2
CODEX_REASONING_EFFORT=high
CODEX_MAX_ITERATIONS=5
CODEX_YOLO=true

# Auto-review mode: block ExitPlanMode until Codex approves the plan,
# auto-run code review after implementation.
# AUTO_REVIEW=true

# Custom init procedure (optional, controls what Codex does during init)
# Reviewer role is always set automatically — this only adds init instructions.
# Example: make Codex explore the codebase before reviews begin:
# CODEX_REVIEWER_PROMPT="Explore the codebase areas relevant to the task. Understand the architecture, patterns, and conventions so you are prepared to review."

# Additional guidance for plan review phase (optional, appended to built-in focus areas)
# CODEX_PLAN_GUIDE="Verify backward compatibility with API v1 clients"

# Additional guidance for code review phase (optional, appended to built-in focus areas)
# CODEX_CODE_GUIDE="Check that all DB queries use parameterized statements"

# Severity calibration (default: true) — see «Severity и вердикт» below.
# CODEX_SEVERITY_CALIBRATION=false
```

### Severity и вердикт

Ревьюер оценивает каждую находку как `critical`, `important` или `minor` и раскладывает находки по трём разделам ответа: `## Blocking` (`critical` и `important` в текущей работе), `## Non-blocking` (`minor` в текущей работе) и `## Pre-existing` (дефекты затронутого, но не изменённого кода — по той же шкале, с сохранением уровня).

`CHANGES_REQUESTED` приходит только при непустом `## Blocking`. `APPROVED` со списком в `## Non-blocking` — нормальный вердикт.

С третьего круга ревьюер отчитывается по блокерам прошлых кругов и открывает новую тему только уровня `critical`.

`CODEX_SEVERITY_CALIBRATION=false` возвращает прежний промпт, где вердикт может удержать любое замечание.

## Использование

### Подключение существующей сессии Codex

Если у вас уже есть живая сессия с Codex (например, вы обсуждали архитектуру), впишите её id в `.codex-review/config.env`:

```bash
CODEX_SESSION_ID=sess_ваш_id
```

Альтернативно — через CLI: `bash scripts/codex-state.sh set session_id sess_ваш_id`

После этого команды `plan` и `code` будут отправлять ревью в эту сессию через `resume`.

### Создание новой сессии

```
"Используем workflow с codex ревьювером. Задачи: #23, #10"
```

Claude вызывает `init` — создаётся сессия Codex. По умолчанию init лёгкий (Codex подтверждает готовность). С `CODEX_REVIEWER_PROMPT` в config.env init выполняет кастомную процедуру (например, исследование кодовой базы). Роль ревьюера задаётся автоматически. Затем `plan` и `code` отправляют ревью в эту сессию через `resume`.

### Workflow

1. **Init** — Claude создаёт сессию Codex (`init`)
2. **Plan Review** — Claude описывает план, Codex ревьюит (`plan`)
3. **Implementation** — Claude реализует по одобренному плану
4. **Code Review** — Claude описывает изменения, Codex ревьюит (`code`)
5. **Done** — результат пользователю

### Управление состоянием

```bash
bash scripts/codex-state.sh show          # Текущее состояние
bash scripts/codex-state.sh dir           # Путь к state-каталогу текущей ветки
bash scripts/codex-state.sh reset         # Сброс итераций
bash scripts/codex-state.sh reset --full  # Полный сброс
bash scripts/codex-state.sh set session_id <value>  # Ручная установка
bash scripts/codex-state.sh set phase implementing  # Обновить фазу
bash scripts/codex-state.sh set iteration 2        # Откатить счётчик кругов
```

Записываемые поля: `session_id`, `phase`, `iteration`, `max_iterations`,
`reviews_completed`, `last_review_status`, `last_review_timestamp`,
`task_description`. Неизвестное имя поля, нецелое значение счётчика и значение
с кавычкой, обратным слэшем, переводом строки или табуляцией — ошибка с кодом
возврата 1, состояние не меняется.

## Структура .codex-review/

В корне основного репо (не worktree) создается директория с per-branch изоляцией. Всё её содержимое локальное:

```
.codex-review/
├── config.env                  # общие локальные настройки проекта
├── .gitkeep
├── archive/                    # локальный архив всех сессий
│   └── {timestamp}/            # артефакты одной сессии (branch в summary.json)
├── feat-auth/                  # per-branch state (имя ветки, / → -)
│   ├── state.json              # транзиентное состояние
│   ├── STATUS.md               # автогенерируемый статус для Claude
│   ├── verdict.txt             # последний вердикт от Codex
│   ├── last_response.txt       # последний ответ Codex
│   ├── current_session.txt     # привязка вердикта к сессии Claude
│   ├── codex-init.log          # лог инициализации сессии
│   ├── codex-init.request.md   # текст задачи, отправленный в сессию
│   ├── codex-init.prompt.md    # промпт, который получил Codex
│   ├── codex-{phase}-{N}.log   # логи итераций ревью
│   ├── codex-{phase}-{N}.request.md  # текст, отправленный на итерацию
│   ├── codex-{phase}-{N}.prompt.md   # промпт, который получил Codex
│   ├── plan.md                 # копия последнего плана, ушедшего на ревью
│   └── notes/                  # локальный журнал текущего ревью
│       ├── .gitkeep
│       ├── plan-review-1.md
│       └── code-review-1.md
└── feat-ui/                    # другая ветка — полная изоляция
    └── ...
```

## CLAUDE.md

Добавь в CLAUDE.md проекта (одноразовая настройка):

```markdown
## Codex Review
Check for `.codex-review/*/STATUS.md` — if a STATUS.md exists for the current branch, read it before starting work (an active review is in progress).
```

`STATUS.md` создаётся и обновляется автоматически скриптами плагина в state-каталоге ветки (путь: `codex-state.sh dir`). Наличие файла означает активное ревью, отсутствие — ревью не идёт или завершено.

## Git Worktree Support

The plugin works transparently from git worktrees:

- `.codex-review/` is always resolved to the main repository root via `git rev-parse --git-common-dir`
- Review state is isolated per branch — each branch gets its own subdirectory (e.g. `.codex-review/feat-auth/`)
- Multiple worktrees on different branches can run reviews in parallel without conflicts
- `config.env` is shared across all branches (project-level settings)
- No additional setup required

## Auto-Review Mode

When `AUTO_REVIEW=true` in `.codex-review/config.env`, the plugin enforces automated review:

- **Plan phase**: a plugin hook blocks `ExitPlanMode` until Codex approves the plan **AND** the approval was issued in the current Claude session. The hook binds each plan review to the Claude session that ran it, so a stale verdict from a previous task or session cannot silently auto-approve a new plan. If the verdict is missing, stale, not approved, or from a different session, the hook denies the exit and instructs Claude to load the codex-review skill and run plan review first.
- **Code phase**: after implementation, Claude automatically sends code for review and iterates until approved.

No additional configuration needed — the hook is declared in `plugin.json` and auto-registered when the plugin is enabled.

## Анти-рекурсия

Плагин защищен от рекурсивного вызова на 3 уровнях:

1. **Env guard** — `CODEX_REVIEWER=1` при вызове codex exec; если скрипт вызван с этой переменной — exit 1
2. **Промпт-контекст** — путь к скиллу в промпте для ориентации
3. **AGENTS.md** — инструкция для Codex о роли ревьюера
