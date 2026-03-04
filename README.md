# polyakov-claude-skills

Набор скиллов для Claude Code.

## Содержание

- [Установка](#установка)
  - [Через маркетплейс (рекомендуется)](#через-маркетплейс-рекомендуется)
  - [Ручная установка (без маркетплейса)](#ручная-установка-без-маркетплейса)
  - [Локальное тестирование](#локальное-тестирование)
- [Доступные скиллы](#доступные-скиллы)
  - [docx-contracts](#docx-contracts) — заполнение Word шаблонов
  - [scrapedo-web-scraper](#scrapedo-web-scraper) — веб-скрапинг через Scrape.do
  - [agent-deck](#agent-deck) — управление сессиями AI агентов
  - [genome-analizer](#genome-analizer) — анализ генетических данных
  - [ssh-remote-connection](#ssh-remote-connection) — SSH подключение к серверам
  - [yandex-wordstat](#yandex-wordstat) — анализ поискового спроса
  - [codex-review](#codex-review) — кросс-агентное ревью
  - [fal-ai-image](#fal-ai-image) — генерация изображений
  - [yandex-search-api](#yandex-search-api) — парсинг выдачи Яндекса
  - [yandex-metrika](#yandex-metrika) — аналитика Yandex Metrika
- [Структура репозитория](#структура-репозитория)
- [Лицензия](#лицензия)

## Установка

### Через маркетплейс (рекомендуется)

```bash
# Добавить маркетплейс
/plugin marketplace add polyakov/polyakov-claude-skills

# Установить нужные плагины
/plugin install docx-contracts
/plugin install scrapedo-web-scraper
/plugin install agent-deck
/plugin install genome-analizer
/plugin install ssh-remote-connection
/plugin install yandex-wordstat
/plugin install yandex-search-api
/plugin install yandex-metrika
/plugin install codex-review
/plugin install fal-ai-image
```

### Ручная установка (без маркетплейса)

Если вы не хотите использовать маркетплейс, скопируйте папку скилла в директорию `.claude/skills/`:

**Глобально (для всех проектов):**
```bash
# Создать директорию если не существует
mkdir -p ~/.claude/skills

# Скопировать нужный скилл
cp -r plugins/agent-deck/skills/agent-deck ~/.claude/skills/
```

**Для конкретного проекта:**
```bash
# В корне проекта
mkdir -p .claude/skills

# Скопировать скилл
cp -r plugins/genome-analizer/skills/genome-analizer .claude/skills/
```

После копирования Claude Code автоматически подхватит скилл при следующем запуске.

### Локальное тестирование

```bash
claude --plugin-dir ./plugins/agent-deck
```

---

## Доступные скиллы

### [docx-contracts](plugins/docx-contracts/skills/docx-contracts)

Заполнение Word шаблонов (договоры, формы) по данным из контекста.

- Подставляет значения в плейсхолдеры `{{VARIABLE}}`
- Извлекает схему из шаблона
- Спрашивает недостающие данные

**Триггеры:** загрузка .docx файла с плейсхолдерами

---

### [scrapedo-web-scraper](plugins/scrapedo-web-scraper/skills/scrapedo-web-scraper)

Веб-скрапинг через Scrape.do с обходом защит и JavaScript рендерингом.

- Обход блокировок и CAPTCHA
- Поддержка JavaScript-рендеринга
- Извлечение текста из HTML

**Триггеры:** когда обычный fetch не работает

---

### [agent-deck](plugins/agent-deck/skills/agent-deck)

Управление сессиями AI агентов через agent-deck CLI.

- Создание и запуск дочерних сессий Claude
- Отслеживание статуса и получение результатов
- Подключение MCP серверов
- Иерархия parent-child сессий

**Триггеры (RU):**
- "запусти агента" / "запусти саб-агента"
- "проверь сессию" / "проверь статус"
- "покажи вывод агента"

**Триггеры (EN):**
- "launch sub-agent" / "create sub-agent"
- "check session" / "show agent output"

---

### [genome-analizer](plugins/genome-analizer/skills/genome-analizer)

Анализ генетических данных из VCF файла.

- Поиск SNP по теме вопроса (GWAS Catalog, SNPedia)
- Интерпретация генотипов
- Генерация персонализированных отчётов с рекомендациями

**Триггеры (RU):**
- "проанализируй мой геном"
- "что у меня с генетикой по [теме]"
- "мой генотип для [признака]"

**Триггеры (EN):**
- "analyze my genome"
- "what's my genetics for [topic]"

---

### [ssh-remote-connection](plugins/ssh-remote-connection/skills/ssh-remote-connection)

SSH подключение к удалённым серверам с agent forwarding.

- Выполнение команд на удалённом сервере
- Agent forwarding (`-A`) для использования локальных SSH ключей
- Управление Docker контейнерами, просмотр логов

**Триггеры (RU):**
- "выполни на сервере"
- "проверь логи на сервере"
- "перезапусти сервис"

**Триггеры (EN):**
- "run on server"
- "check server logs"
- "restart service"

---

### [yandex-wordstat](plugins/yandex-wordstat/skills/yandex-wordstat)

Анализ поискового спроса через Yandex Wordstat API.

- Топ поисковых запросов по фразе
- Динамика спроса по месяцам
- Региональная статистика
- Проверка интента через веб-поиск

**Триггеры (RU):**
- "проанализируй спрос на"
- "найди запросы для рекламы"
- "какой спрос на [тему]"

**Триггеры (EN):**
- "analyze search demand"
- "find keywords for"

---

### [codex-review](plugins/codex-review/skills/codex-review)

Кросс-агентное ревью: Claude реализует, Codex (GPT-5.2) ревьюит.

- Workflow: init session → plan review → implementation → code review
- Журнал ревью в `.codex-review/notes/` (коммитится в git)
- Анти-рекурсия через env guard `CODEX_REVIEWER`

**Триггеры (RU):**
- "кодекс ревью"

**Триггеры (EN):**
- "with codex review"
- "codex review workflow"
- "start codex review"

---

### [fal-ai-image](plugins/fal-ai-image/skills/fal-ai-image)

Генерация изображений через fal.ai nano-banana-pro (Gemini 3 Pro Image).

- Генерация из текстового промпта (text-to-image)
- Редактирование с референсными изображениями (image-to-image)
- Поддержка разрешений 1K / 2K / 4K

**Триггеры (RU):**
- "сгенерируй изображение"
- "нарисуй картинку"
- "создай инфографику"

**Триггеры (EN):**
- "generate image"
- "create infographic"
- "draw a picture"

---

### [yandex-search-api](plugins/yandex-search-api/skills/yandex-search-api)

Парсинг выдачи Яндекса через Yandex Cloud Search API v2.

- Синхронный и асинхронный режимы поиска
- Авторизация через IAM token (JWT PS256 из Service Account Key)
- Парсинг SERP: позиция, заголовок, URL, сниппет
- Кэширование результатов и резюмируемый async

**Триггеры (RU):**
- "поиск в яндексе"
- "выдача яндекса по запросу"
- "парсинг выдачи"

**Триггеры (EN):**
- "yandex search api"
- "parse yandex serp"

---

### [yandex-metrika](plugins/yandex-metrika/skills/yandex-metrika)

Аналитика Yandex Metrika: трафик, конверсии, UTM, поисковые системы.

- Cache-first стратегия с TSV-индексами для grep
- Отчёты: трафик по источникам, конверсии по целям, UTM-разметка, поисковые системы
- Фильтры: устройство, источник, модель атрибуции, без роботов по умолчанию
- Автоматический пропуск кеша для текущей даты

**Триггеры (RU):**
- "покажи трафик по счётчику"
- "конверсии за период"
- "аналитика метрики"

**Триггеры (EN):**
- "yandex metrika analytics"
- "show traffic sources"
- "conversion report"

---

## Структура репозитория

```
polyakov-claude-skills/
├── .claude-plugin/
│   └── marketplace.json      # Маркетплейс конфигурация
├── plugins/
│   ├── docx-contracts/       # Плагин для .docx
│   ├── scrapedo-web-scraper/ # Плагин для скрапинга
│   ├── agent-deck/           # Плагин для агентов
│   ├── genome-analizer/      # Плагин для анализа генома
│   ├── ssh-remote-connection/# Плагин для SSH
│   ├── yandex-wordstat/      # Плагин для Wordstat API
│   ├── codex-review/         # Плагин для кросс-агентного ревью
│   ├── fal-ai-image/         # Плагин для генерации изображений
│   ├── yandex-search-api/    # Плагин для Yandex Search API
│   └── yandex-metrika/       # Плагин для аналитики Yandex Metrika
└── README.md
```

---

## Лицензия

MIT
