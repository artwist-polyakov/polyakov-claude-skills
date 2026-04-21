---
name: agent-deck
description: "Creates, monitors, and retrieves output from AI agent sessions using the agent-deck CLI. Launches sub-agent Claude sessions, sends tasks, checks status, collects results, and manages MCP server connections. Use when the user wants to run a sub-agent, launch a parallel Claude session, check agent status, or retrieve agent output. Triggers (RU): запусти агента, запусти саб-агента, создай сессию, проверь сессию, проверь статус, покажи вывод агента, что агент ответил. Triggers (EN): launch sub-agent, create sub-agent, start session, check session, show agent output, agent-deck."
---

# Agent Deck CLI

Менеджер терминальных сессий для AI агентов. Позволяет запускать, контролировать и получать результаты от дочерних Claude сессий.

## Запуск суб-агента

**Триггеры:** "запусти агента", "запусти саб-агента", "launch sub-agent"

### Простой запуск (CLI команды)

```bash
# Создать сессию
agent-deck add -t "Название" -c claude /path/to/workdir

# Создать как дочернюю сессию текущего агента
agent-deck add -t "Название" --parent "Родитель" -c claude /path/to/workdir

# Запустить
agent-deck session start "Название"

# Отправить задачу
agent-deck session send "Название" "Твоя задача..."
```

### Автоматический запуск (скрипт)

```bash
scripts/launch-subagent.sh "Название" "Промпт" [--mcp exa] [--wait]
```

Скрипт автоматически:
- Определяет текущую сессию и профиль
- Создаёт дочернюю сессию
- Ждёт инициализации Claude
- Отправляет промпт

### Режимы получения результата

| Режим | Команда | Когда использовать |
|-------|---------|-------------------|
| **Fire & forget** | (без --wait) | По умолчанию. Скажи: "Спроси меня когда будет готово" |
| **On-demand** | `agent-deck session output "Название"` | Когда пользователь спрашивает |
| **Blocking** | `--wait` | Нужен немедленный результат |

---

## Проверка статуса

**Триггеры:** "проверь сессию", "проверь статус", "check session"

```bash
agent-deck status                      # Все сессии (сводка)
agent-deck session show "Название"     # Детали конкретной сессии
agent-deck session show -json "Название"  # JSON формат
agent-deck session current             # Текущая сессия (в которой работаем)
agent-deck session current --json      # Текущая сессия в JSON
```

**Статусы:**
- `●` работает (running)
- `◐` ждёт ввода (waiting)
- `○` простаивает (idle)
- `✕` ошибка (error)

---

## Получение результата

**Триггеры:** "покажи вывод агента", "что агент ответил", "show agent output"

```bash
agent-deck session output "Название"
```

---

## MCP подключение

```bash
agent-deck mcp list                        # Доступные MCP серверы
agent-deck mcp attach "Название" exa       # Подключить MCP к сессии
agent-deck session restart "Название"      # ОБЯЗАТЕЛЬНО после подключения!
```

### Рекомендуемые MCP

| Задача | MCP серверы |
|--------|-------------|
| Веб-поиск | `exa`, `firecrawl` |
| Документация кода | `context7` |
| Сложные рассуждения | `sequential-thinking` |

---

## Управление сессиями

```bash
# Жизненный цикл
agent-deck session start "Название"
agent-deck session stop "Название"
agent-deck session restart "Название"

# Список всех сессий
agent-deck ls
agent-deck ls -json

# Удалить сессию
agent-deck rm "Название"
```

---

## Важные правила

1. **Флаги перед аргументами:** `session show -json name` (не `session show name -json`)
2. **После mcp attach** обязательно `session restart` для применения изменений
3. **Избегать polling** результатов из других агентов — это может мешать целевой сессии
4. **Идентификация сессии:** можно использовать название, ID (≥6 символов) или путь

---

## Примеры использования

### Запуск исследовательского агента

```bash
# Создать агента для веб-исследования
agent-deck add -t "Researcher" -c claude --mcp exa /tmp/research
agent-deck session start "Researcher"
agent-deck session send "Researcher" "Найди информацию о последних трендах в AI"
```

### Проверка готовности

```bash
# Проверить статус
agent-deck session show "Researcher"

# Если статус ◐ (waiting) — агент закончил, получить результат:
agent-deck session output "Researcher"
```

### Проверка подключения после создания

```bash
# Убедиться что сессия запущена (статус ● running)
agent-deck session show "Название"
# Если статус ✕ (error) — проверь путь и конфигурацию, пересоздай сессию
```
