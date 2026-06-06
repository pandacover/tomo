# Tomo

Tomo is a v1 DeepAgents project chat using Python, uv, LangChain xAI, prompt_toolkit, and SuperGrok OAuth.

## Setup

```bash
uv sync
uv run tomo login
uv run tomo chat
```

## Telegram

Create a bot with BotFather, then run the gateway:

```bash
uv run tomo telegram-config set --bot-token 123456:telegram-token --chat-ids 123456789
uv run tomo telegram
```

`--chat-ids` is optional, but recommended. Use a comma-separated list to allow multiple chats.
Saved Telegram config is stored in `.tomo/telegram.json` and ignored by git. `TOMO_TELEGRAM_BOT_TOKEN`
and `TOMO_TELEGRAM_ALLOWED_CHAT_IDS` still work as environment variable overrides.
When Tomo needs approval for a terminal command, reply `/approve` or `/deny` in Telegram.

## Commands

```bash
uv run tomo login
uv run tomo logout
uv run tomo auth-status
uv run tomo chat
uv run tomo telegram
uv run tomo telegram-config set --bot-token 123456:telegram-token --chat-ids 123456789
uv run tomo telegram-config show
uv run tomo telegram-config delete
```

Local OAuth tokens are stored in `.tomo/auth.json` and ignored by git.

Set `TOMO_MODEL` to override the default model, `grok-4.3`.
