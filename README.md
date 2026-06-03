# Butler

Butler is a v1 DeepAgents project chat using Python, uv, LangChain xAI, prompt_toolkit, and SuperGrok OAuth.

## Setup

```bash
uv sync
uv run butler login
uv run butler chat
```

## Telegram

Create a bot with BotFather, then run the gateway:

```bash
export BUTLER_TELEGRAM_BOT_TOKEN=123456:telegram-token
export BUTLER_TELEGRAM_ALLOWED_CHAT_IDS=123456789
uv run butler telegram
```

`BUTLER_TELEGRAM_ALLOWED_CHAT_IDS` is optional, but recommended. Use a comma-separated list to allow multiple chats.
When Butler needs approval for a write, edit, or shell command, reply `/approve` or `/deny` in Telegram.

## Commands

```bash
uv run butler login
uv run butler logout
uv run butler auth-status
uv run butler chat
uv run butler telegram
```

Local OAuth tokens are stored in `.butler/auth.json` and ignored by git.

Set `BUTLER_MODEL` to override the default model, `grok-4.3`.
