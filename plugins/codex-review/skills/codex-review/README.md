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
.codex-review/config.env
.codex-review/*/state.json
.codex-review/*/STATUS.md
.codex-review/*/verdict.txt
.codex-review/*/last_response.txt
.codex-review/*/codex-*.log
.codex-review/archive/
```

> `notes/` **НЕ** игнорируем — это журнал ревью для команды.

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

# Custom init procedure (optional, controls what Codex does during init)
# Reviewer role is always set automatically — this only adds init instructions.
# Example: make Codex explore the codebase before reviews begin:
# CODEX_REVIEWER_PROMPT="Explore the codebase areas relevant to the task. Understand the architecture, patterns, and conventions so you are prepared to review."

# Additional guidance for plan review phase (optional, appended to built-in focus areas)
# CODEX_PLAN_GUIDE="Verify backward compatibility with API v1 clients"

# Additional guidance for code review phase (optional, appended to built-in focus areas)
# CODEX_CODE_GUIDE="Check that all DB queries use parameterized statements"
```

## Использование

### Подключение существующей сессии Codex

Если у вас уже есть живая сессия с Codex (например, вы обсуждали архитектуру), впишите её id в `.codex-review/config.env`:

```bash
CODEX_SESSION_ID=sess_ваш_id
```

Узнать id: `codex session list`

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
```

## Структура .codex-review/

В корне основного репо (не worktree) создается директория с per-branch изоляцией:

```
.codex-review/
├── config.env                  # gitignore — общие настройки проекта
├── .gitkeep
├── archive/                    # gitignore — общий архив всех сессий
│   └── {timestamp}/            # артефакты одной сессии (branch в summary.json)
├── feat-auth/                  # per-branch state (имя ветки, / → -)
│   ├── state.json              # gitignore — транзиентное состояние
│   ├── STATUS.md               # gitignore — автогенерируемый статус для Claude
│   ├── verdict.txt             # gitignore — последний вердикт от Codex
│   ├── last_response.txt       # gitignore — последний ответ Codex
│   ├── codex-init.log          # gitignore — лог инициализации сессии
│   ├── codex-{phase}-{N}.log   # gitignore — логи итераций ревью
│   └── notes/                  # В GIT — журнал текущего ревью для команды
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

## Анти-рекурсия

Плагин защищен от рекурсивного вызова на 3 уровнях:

1. **Env guard** — `CODEX_REVIEWER=1` при вызове codex exec; если скрипт вызван с этой переменной — exit 1
2. **Промпт-контекст** — путь к скиллу в промпте для ориентации
3. **AGENTS.md** — инструкция для Codex о роли ревьюера
