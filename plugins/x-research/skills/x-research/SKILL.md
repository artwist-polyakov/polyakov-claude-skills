---
name: x-research
description: |
  X/Twitter research via xAI Grok API with x_search tool.
  Digests, thread analysis, trending topics, custom search.
  Designed for finding Telegram post ideas from X discussions.
  Triggers: x research, twitter research, x digest, twitter digest,
  что в твиттере, дайджест твиттера, тренды x, trending x,
  analyze tweet, анализ твита, x search, поиск в твиттере.
---

# x-research

Research X/Twitter via xAI Grok API. Grok's `x_search` tool searches X in real-time and returns synthesized analysis with citations.

## Перед запуском

Перед любым запуском скриптов создай рабочую копию скилла в `/home/claude/x-research` и работай уже из неё:

```bash
sh scripts/prepare_runtime.sh
cd /home/claude/x-research
```

`prepare_runtime.sh` копирует скилл и, если есть `config/.env`, нормализует его через `sed -i`: некавыченные значения с пробелами оборачиваются в кавычки в рабочей копии. Не выводи содержимое `.env` в ответ; при ошибке формата подскажи пользователю, какие строки нужно заключить в кавычки.

## Config

Get an xAI API key at [console.x.ai](https://console.x.ai/), then:
```bash
cp config/.env.example config/.env
```

Edit `config/.env`: paste your key into `XAI_API_KEY`, configure accounts and topics.
Quote any value that contains spaces so the file stays shell-compatible. Шаг подготовки также чинит некавыченные значения с пробелами в скопированном `.env`.

**Without `.env`:** skill works with explicit `--accounts`, `--query`, `--topics`.

**With `.env`:** digests and trending ready out of the box.

**Category registry** (same pattern as telegram-channel-parser):
```bash
X_CATEGORIES=ai,crypto         # available categories
X_DEFAULT_CATEGORY=ai           # default for "digest" without specifying

X_ACCOUNTS_AI_LABEL="AI & ML"
X_ACCOUNTS_AI=elonmusk,sama,AndrewYNg

X_TOPICS_AI=AI,LLM,GPT,Claude,agents,AGI
```

**Agent algorithm for digest/trending:**
1. Read `config/.env`
2. Parse `X_CATEGORIES` to get available categories
3. For each: `X_ACCOUNTS_<ID>` = handles, `X_TOPICS_<ID>` = topics, `X_ACCOUNTS_<ID>_LABEL` = name
4. Match user request to category label or use `X_DEFAULT_CATEGORY`
5. Pass to script via `--accounts` / `--topics`

**Priority:** `--accounts`/`--topics` explicit > category from `.env` > agent asks.

Details: [config/README.md](config/README.md).

## Philosophy

1. **Live-first** — every call makes a fresh API request. Real-time data, never stale.
2. **Artifact store** — each run saves an immutable snapshot in `cache/runs/`. Use `--prefer-cache` to reuse.
3. **Structured output** — digest/trending/search return JSON with individual items for machine consumption.
4. **Context hygiene** — stdout limited to 30 lines. Full data in artifact file.
5. **Config-driven model** — model set in `.env`, auto-fallback if unavailable.

## Workflow

### Digest of subscribed accounts

```bash
# Default category from .env
bash scripts/digest.sh --period today

# Specific category
bash scripts/digest.sh --category crypto --period week

# Explicit accounts (no .env needed)
bash scripts/digest.sh --accounts "elonmusk,sama" --period today
```

Returns: structured items (author, date, summary, engagement, URL, topic) + Telegram post ideas.

### Analyze a post/thread/topic

```bash
# By description
bash scripts/analyze.sh --query "Elon Musk's thread about open source AI"

# By URL
bash scripts/analyze.sh --url "https://x.com/elonmusk/status/123456" --query "context about the post"

# With time period
bash scripts/analyze.sh --query "debate about AI regulation" --period week
```

Returns: main thesis, key arguments, community reaction, sentiment, interesting findings, Telegram post angles.

### Trending topics

```bash
# Default topics from .env
bash scripts/trending.sh --period today

# Specific topics
bash scripts/trending.sh --topics "Bitcoin,Ethereum,DeFi" --period today

# Category
bash scripts/trending.sh --category ai --period week
```

Returns: structured items (topic, summary, sentiment, key voices, engagement) + Telegram post ideas.

### Custom search

```bash
# Free search
bash scripts/search.sh --query "Claude 4 reactions" --period week

# Search within specific accounts
bash scripts/search.sh --query "AI safety" --accounts "sama,ylecun" --period today
```

Returns: structured items + narrative summary + Telegram post ideas.

### Reuse previous results

```bash
# Read last digest without making an API call
bash scripts/digest.sh --category ai --period today --prefer-cache

# Find artifact manually
bash scripts/find_latest.sh --script digest --category ai --period today
```

## Scripts

```bash
bash scripts/<script>.sh [params]
```

| Script | Description | Key params |
|--------|-------------|------------|
| `digest.sh` | Digest of subscribed accounts | `--category`, `--accounts`, `--period` |
| `analyze.sh` | Deep analysis of post/thread/topic | `--query`, `--url`, `--period` |
| `trending.sh` | Trending topics by interests | `--category`, `--topics`, `--period` |
| `search.sh` | Custom search query | `--query`, `--accounts`, `--period` |
| `find_latest.sh` | Find latest cached artifact | `--script`, `--category`, `--query`, `--period` |

## Common parameters

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `--accounts` | no | from .env | X handles, comma-separated (without @) |
| `--category` | no | from .env | Category ID from X_CATEGORIES |
| `--period` | no | today | Time range: `1h`, `today`, `yesterday`, `week`, `Nd` |
| `--query` | varies | — | Search query or post description |
| `--topics` | no | from .env | Topics for trending, comma-separated |
| `--url` | no | — | X post URL for analyze |
| `--limit` | no | 30 | Max output lines |
| `--prefer-cache` | no | — | Use latest cached artifact if available |
| `--refresh` | no | — | Force live request (default behavior) |

## Artifact store

Each run saves a JSON artifact in `cache/runs/` with full metadata:

```json
{
  "meta": {
    "script": "digest",
    "category": "ai",
    "handles": ["elonmusk", "sama"],
    "period": "today",
    "from_date": "2026-04-01",
    "to_date": "2026-04-01",
    "model": "grok-4-1-fast-reasoning",
    "created_at": "2026-04-01T14:30:22Z",
    "run_id": "a1b2c3"
  },
  "items": [...],
  "ideas": [...],
  "summary": "...",
  "text": "...",
  "citations": [...]
}
```

Index: `cache/index.jsonl` — one JSON line per run for fast lookup.

## API limits

- **x_search**: max 10 handles per `allowed_x_handles` (auto-batched if more)
- **Rate limits**: tier-based, see [docs.x.ai/developers/rate-limits](https://docs.x.ai/developers/rate-limits)
- **Tool pricing**: x_search invocations billed separately from tokens
- **Model fallback**: if primary model unavailable (HTTP 422/400), auto-retry with `grok-4.20-reasoning`

## Limitations

- Only **public** posts (x_search does not access private/protected accounts)
- x_search is a **server-side** Grok tool — we get synthesized analysis, not raw post data
- Structured JSON output depends on model compliance; fallback to plain text if parsing fails
- No DM access, no analytics data (only public engagement metrics)

Advanced scenarios: [references/API_REFERENCE.md](references/API_REFERENCE.md)
