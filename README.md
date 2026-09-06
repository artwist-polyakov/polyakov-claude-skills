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
  - [yandex-webmaster](#yandex-webmaster) — управление сайтами в Яндекс.Вебмастере
  - [zoomkit](#zoomkit) — баланс, счета, отчёты и настройки Яндекс.Директа через ZoomKit API
  - [telegraph-publisher](#telegraph-publisher) — публикация в Telegraph
  - [crawl4ai-seo](#crawl4ai-seo) — SEO-краулер сайтов
  - [telegram-channel-parser](#telegram-channel-parser) — парсинг Telegram-каналов
  - [x-research](#x-research) — рисерч X/Twitter через xAI Grok API
  - [github-pages-publisher](#github-pages-publisher) — публикация на GitHub Pages
  - [sourcecraft-publisher](#sourcecraft-publisher) — публикация на SourceCraft Sites
  - [reddit-skill](#reddit-skill) — Reddit API: пользователи, сабреддиты, поиск, посты
  - [knowledge-compiler](#knowledge-compiler) — компиляция книг и длинных источников в личные скиллы
  - [perplexity-search](#perplexity-search) — поиск и ресёрч через Perplexity API
- [Структура репозитория](#структура-репозитория)
- [Лицензия](#лицензия)

## Установка

### Через маркетплейс (рекомендуется)

```bash
# Добавить маркетплейс
/plugin marketplace add artwist-polyakov/polyakov-claude-skills

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
/plugin install yandex-webmaster
/plugin install zoomkit
/plugin install telegraph-publisher
/plugin install crawl4ai-seo
/plugin install telegram-channel-parser
/plugin install x-research
/plugin install github-pages-publisher
/plugin install sourcecraft-publisher
/plugin install reddit-skill
/plugin install knowledge-compiler
/plugin install perplexity-search
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

SSH подключение к удалённым серверам по ключу или паролю.

- Выполнение команд на удалённом сервере
- Agent forwarding (`-A`) для использования локальных SSH ключей
- Подключение по логину/паролю через `SSH_PASSWORD` при наличии `sshpass` или `expect`
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

Анализ поискового спроса через Wordstat API в Yandex Cloud Search API. [Настройка доступа](plugins/yandex-wordstat/skills/yandex-wordstat/config/README.md) — через ключ сервисного аккаунта.

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
- Локальный журнал ревью в `.codex-review/<ветка>/notes/`
- Анти-рекурсия через env guard `CODEX_REVIEWER`

**Триггеры (RU):**
- "кодекс ревью"

**Триггеры (EN):**
- "with codex review"
- "codex review workflow"
- "start codex review"

---

### [fal-ai-image](plugins/fal-ai-image/skills/fal-ai-image)

Генерация изображений через fal.ai с переключением модели из конфига.

- Google Nano Banana Pro по умолчанию для обратной совместимости
- OpenAI GPT Image 2 через тот же `FAL_KEY`
- Генерация из текста и редактирование по референсам
- Совместимость старых `--aspect-ratio` / `--resolution` сценариев после переключения на GPT
- Разовый форс модели через `--model gpt|openai|nano-banana|google|gemini`

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

### [yandex-webmaster](plugins/yandex-webmaster/skills/yandex-webmaster)

Управление сайтами через Yandex Webmaster API v4.

- Индексация: история, сэмплы, важные URL, экспорт архива
- Поисковые запросы: топ запросов, история, расширенная аналитика с фильтрами
- Переобход страниц: отправка URL, статус, квоты
- Ссылки: битые внутренние, внешние (сэмплы + история)
- Сайтмапы: список, добавление, приоритетный переобход
- Диагностика, SQI, фиды, PRO SERP экспорт
- 24-часовой TTL кеш для session-данных

**Триггеры (RU):**
- "проверь индексацию сайта"
- "покажи поисковые запросы"
- "отправь на переобход"

**Триггеры (EN):**
- "yandex webmaster"
- "check site indexing"
- "recrawl url"

---

### [zoomkit](plugins/zoomkit/skills/zoomkit)

Знакомство с ZoomKit, подключение и работа с официальным API: расчёты, отчёты статистики и настройки Яндекс.Директа.

- Объяснение пользы, опубликованных тарифов и условий, при которых сервис оправдывает расходы
- Проверка ключа и предупреждение о скором окончании срока
- Баланс сервиса и список уже выставленных счетов с отбором по состоянию
- Двухшаговые отчёты рекламной статистики с ожиданием готовности
- Правила ставок и подробные результаты проверки ссылок Яндекс.Директа
- Честная граница аудита: API не перечисляет кампании и не отключает кабинеты от списаний
- Предварительный просмотр и обязательный `--confirm` для изменений
- Понятная инструкция по подключению при отсутствии ключа
- Явная защита от несуществующих методов: API 1.8.0 не умеет выставлять счета и создавать ссылки на оплату

**Триггеры (RU):**
- "проверь баланс ZoomKit"
- "покажи счета ZoomKit"
- "сделай отчёт ZoomKit"
- "что умеет ZoomKit и сколько стоит"
- "найди кампании без проверки ссылок"
- "проверь правила ставок ZoomKit"

**Триггеры (EN):**
- "zoomkit api"
- "zoomkit balance"
- "zoomkit invoices"
- "zoomkit pricing"

---

### [telegraph-publisher](plugins/telegraph-publisher/skills/telegraph-publisher)

Публикация страниц в Telegraph с поддержкой медиа.

- Создание/редактирование страниц через API
- Поддержка изображений по URL, YouTube embed
- Для постоянных картинок и диаграмм предпочитает GitHub + jsDelivr вместо нестабильного Telegraph upload
- Для GitHub рекомендует отдельный public media repo и отдельный fine-grained PAT только с `Contents: Read and write` на этот repo
- Хранит связь `Telegraph path -> assets` через manifest для последующего cleanup
- Auto-split длинных материалов на серию страниц
- Управление аккаунтом (создание, привязка к браузеру)

**Триггеры (RU):**
- "опубликуй в Telegraph"
- "создай страницу в Telegraph"
- "telegraph публикация"

**Триггеры (EN):**
- "publish to Telegraph"
- "create Telegraph page"
- "telegraph publish"

---

### [crawl4ai-seo](plugins/crawl4ai-seo/skills/crawl4ai-seo)

SEO-краулер сайтов на базе Crawl4AI.

- Инвентаризация сайта: URL, status, title, H1, meta, canonical, word count
- On-page аудит: дубли заголовков, пустые meta, битые canonical, thin content
- Анализ перелинковки: orphan pages, слабо связанные страницы, граф ссылок
- Навигационный аудит: breadcrumbs, menu consistency, weak hubs
- Сравнение лендингов и анализ конкурентов
- Связка с yandex-search-api, yandex-metrika, yandex-webmaster

**Триггеры (RU):**
- "аудит сайта"
- "проверь перелинковку"
- "навигационный аудит"

**Триггеры (EN):**
- "site audit"
- "internal linking audit"
- "seo crawl"

---

### [telegram-channel-parser](plugins/telegram-channel-parser/skills/telegram-channel-parser)

Парсинг публичных Telegram-каналов через веб-превью (t.me/s/).

- Посты канала с метриками (просмотры, реакции, пересылки)
- Дайджест по нескольким каналам за период
- Топ постов (шер-парад), поиск, расписание публикаций
- Сравнительная таблица каналов
- Cache-first подход, zero config, без API-ключей

**Триггеры (RU):**
- "парсинг телеграм канала"
- "дайджест каналов"
- "анализ канала"

**Триггеры (EN):**
- "telegram channel"
- "telegram digest"
- "telegram analytics"

---

### [x-research](plugins/x-research/skills/x-research)

Рисерч X/Twitter через xAI Grok API с инструментом x_search.

- Дайджест подписок по категориям аккаунтов
- Анализ постов, тредов, дискуссий (тон, аргументы, реакция комьюнити)
- Трендовые темы по интересам с оценкой сентимента
- Произвольный поиск с идеями для постов в Telegram
- Session artifact store (live-first, immutable snapshots)

**Триггеры (RU):**
- "дайджест твиттера"
- "что в твиттере по [теме]"
- "анализ твита"

**Триггеры (EN):**
- "x research" / "twitter research"
- "x digest" / "trending x"
- "analyze tweet"

---

### [github-pages-publisher](plugins/github-pages-publisher/skills/github-pages-publisher)

Публикация статических артифактов на GitHub Pages.

- Деплой в структуру `YYYY/YYYY-MM/page-slug/`
- Viewport-валидация перед публикацией (desktop 1440px, mobile 375px)
- Slugify заголовков, автоматический `index.html` entrypoint
- Fine-grained GitHub token для безопасного пуша

**Триггеры (RU):**
- "опубликуй на GitHub Pages"
- "задеплой страницу"
- "залей артифакт"

**Триггеры (EN):**
- "publish to GitHub Pages"
- "deploy static page"
- "push artifact to Pages"

---

### [sourcecraft-publisher](plugins/sourcecraft-publisher/skills/sourcecraft-publisher)

Публикация статических артифактов на SourceCraft Sites (Yandex, работает в России).

- Деплой в структуру `YYYY/YYYY-MM/page-slug/`
- Автоматическое создание `.sourcecraft/sites.yaml`
- OAuth2 токен для push в SourceCraft
- Зеркало `github-pages-publisher` для российской инфраструктуры

**Триггеры (RU):**
- "опубликуй на SourceCraft"
- "задеплой на сорскрафт"
- "залей в российское зеркало"

**Триггеры (EN):**
- "publish to SourceCraft"
- "deploy to SourceCraft Sites"

---

### [reddit-skill](plugins/reddit-skill/skills/reddit-skill)

Reddit API на shell-скриптах: пользователи, сабреддиты, посты, комментарии, поиск.

- Прямые вызовы Reddit OAuth2 API через curl (без PRAW и Python-зависимостей)
- Авто-выбор режима: app-only (`client_credentials`) для read, user (`password`) для write/me
- Cache-first: токены, юзеры, сабреддиты, листинги
- Двойной предохранитель для write: `REDDIT_ENABLE_WRITE=1` + `--confirm` (без флага — dry-run)
- Rate-limit по заголовкам (`x-ratelimit-*`, `Retry-After`)
- 14 операций: профили, посты/комментарии юзеров, top/popular сабреддитов, search, submission по URL/id, post_create, comment_reply, subscribe/unsubscribe

**Триггеры (RU):**
- "посты reddit"
- "комментарии reddit"
- "парсинг reddit"

**Триггеры (EN):**
- "reddit api"
- "reddit subreddit"
- "reddit user"

---

### [knowledge-compiler](plugins/knowledge-compiler/skills/knowledge-compiler)

Компиляция книг, PDF/EPUB/TXT/Markdown и длинных прикладных источников в личные Claude Code скиллы.

- Собирает не пересказ, а карту применимых идей: понятия, решающие правила, плейбуки, анти-паттерны
- Подходит для технических, управленческих, маркетинговых, продуктовых, учебных и внутренних материалов
- Делает `source-map.json` и `knowledge-manifest.json` для связи тезисов с сегментами источника
- Для EPUB читает OPF-метаданные и `toc.ncx`/`nav.xhtml`, затем использует точное оглавление как источник сегментации
- Разведывает структуру через малый `outline-scout.json`, первые/последние выдержки и строки-кандидаты без загрузки всего текста
- Python-скрипты запускаются через `uv run --script`, зависимости объявлены в самих скриптах
- Кеширует извлечённый текст и сегменты в `cache/jobs/<job-id>/`; готовый скилл собирает в `dist/<skill-name>/`
- Проверяет результат через `quality_gate.py`: обязательные файлы, длина `SKILL.md`, JSON и длинные дословные совпадения
- OCR, публикационный профиль и Go-ускоритель вынесены в бэклог

**Триггеры (RU):**
- "сделай скилл из книги"
- "преврати PDF в навык"
- "собери карту знаний"

**Триггеры (EN):**
- "book to skill"
- "compile knowledge"
- "create skill from PDF"

---

### [perplexity-search](plugins/perplexity-search/skills/perplexity-search)

Поиск и ресёрч через Perplexity API на POSIX-shell.

- Search API — сырая ранжированная выдача со сниппетами, до 5 запросов за один оплаченный вызов
- Agent API — ответ с цитатами, выбор пресета и модели (Sonar, GPT, Claude, Gemini, Grok)
- Deep research в background mode с поллингом и `--resume` по response id
- Извлечение содержимого конкретных URL через инструмент `fetch_url`
- Структурированный вывод по JSON Schema
- Cache-first: ключ кеша — само тело запроса; крупные результаты уходят в `cache/` и читаются грепом
- Профили источников в `.env`: домены + свежесть + глубина извлечения одним флагом
- Оффлайн-тесты: разбор `.env`, сборка тел запросов, рендер ответов, CLI-контракт, HTTP-слой на loopback-моке

**Триггеры (RU):**
- "найди в интернете"
- "что пишут про [тему]"
- "глубокое исследование"

**Триггеры (EN):**
- "perplexity search"
- "web search with sources"
- "deep research"

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
│   ├── yandex-metrika/       # Плагин для аналитики Yandex Metrika
│   ├── yandex-webmaster/     # Плагин для Yandex Webmaster API
│   ├── zoomkit/               # Плагин для ZoomKit API
│   ├── telegraph-publisher/  # Плагин для публикации в Telegraph
│   ├── crawl4ai-seo/         # Плагин для SEO-краулинга
│   ├── telegram-channel-parser/ # Плагин для парсинга Telegram-каналов
│   ├── x-research/              # Плагин для рисерча X/Twitter
│   ├── github-pages-publisher/  # Плагин для публикации на GitHub Pages
│   ├── sourcecraft-publisher/   # Плагин для публикации на SourceCraft Sites
│   ├── reddit-skill/            # Плагин для Reddit API
│   ├── knowledge-compiler/      # Плагин для компиляции источников в скиллы
│   └── perplexity-search/       # Плагин для поиска и ресёрча через Perplexity API
└── README.md
```

---

## Лицензия

MIT
