# X Research — Config Setup

## 1. Get xAI API Key

1. Go to [console.x.ai](https://console.x.ai/)
2. Sign up / log in
3. Navigate to **API Keys**
4. Create a new key (starts with `xai-`)

## 2. Create .env

```bash
cp .env.example .env
```

Edit `.env` and paste your key into `XAI_API_KEY`.

Important: keep `.env` shell-compatible. If a value contains spaces, wrap the whole value in quotes.
Some runners use `. config/.env`, and unquoted values such as `Claude Code` will be treated as commands.

Перед запуском скриптов из установленного скилла сначала создайте рабочую копию:

```bash
sh scripts/prepare_runtime.sh
cd /home/claude/x-research
```

Подготовка также запускает `sed -i` для скопированного `config/.env` и заключает некавыченные значения с пробелами в кавычки. Не выводите содержимое `.env` при разборе ошибок настройки; указывайте пользователю только имена переменных, которые нужно исправить.

## 3. Configure accounts & topics

Edit `.env` to add X accounts you want to follow and topics you care about.

**Category system** (same as telegram-channel-parser):

```bash
# Register categories
X_CATEGORIES=ai,crypto

# Define each category
X_ACCOUNTS_AI_LABEL="AI & ML"
X_ACCOUNTS_AI=elonmusk,sama,AndrewYNg

X_ACCOUNTS_CRYPTO_LABEL="Crypto"
X_ACCOUNTS_CRYPTO=VitalikButerin,cz_binance

# Quote values with spaces
X_TOPICS_OPENAI="GPT,ChatGPT,OpenAI,Codex,gpt 5.4"
X_TOPICS_ANTHROPIC="Claude,Anthropic,ClaudeCode,Claude Code"
```

**Note:** `allowed_x_handles` API limit is 10 per request. Categories with >10 accounts are automatically batched.

## 4. Model selection

Default model: `grok-4-1-fast-reasoning` (fast & cheap).

If unavailable, auto-fallback to `grok-4.20-reasoning`.

Override in `.env`:
```bash
XAI_MODEL=grok-4.20-reasoning
```

Check available models: [docs.x.ai/developers/models](https://docs.x.ai/developers/models)
