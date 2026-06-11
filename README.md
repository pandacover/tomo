# Tomo

Tomo is a v1 project chat using Python, uv, LangGraph, LangChain xAI, prompt_toolkit, and SuperGrok OAuth.

## Setup

```bash
uv sync
uv run tomo login
uv run tomo chat
```

## Desktop

Launch the background tray chat app:

```bash
uv run tomo desktop start
```

Stop or restart it without relying on Ctrl+C:

```bash
uv run tomo desktop stop
uv run tomo desktop restart
```

The background desktop app writes its PID to `.tomo/desktop.pid` and logs to `.tomo/desktop.log`.
`uv run tomo desktop` still runs the app in the foreground for debugging.

On native Windows, the desktop app opens from the Windows tray. Closing the chat window hides it; use `uv run tomo desktop stop`
or the tray Quit action to exit explicitly. In WSL, Tomo uses the Qt webview backend and opens the chat window directly; tray
integration is best-effort because WSLg does not always expose a system tray to Linux apps.

The desktop app supports text chat only.
Voice activation, wake-word listening, global hotkeys, packaging, and login autostart are intentionally out of scope for
this first slice.

## Telegram

Create a bot with BotFather, then run the gateway:

```bash
uv run tomo telegram-config set --bot-token 123456:telegram-token --chat-ids 123456789
uv run tomo telegram start
```

`--chat-ids` is optional, but recommended. Use a comma-separated list to allow multiple chats.
Saved Telegram config is stored in `.tomo/telegram.json` and ignored by git. `TOMO_TELEGRAM_BOT_TOKEN`
and `TOMO_TELEGRAM_ALLOWED_CHAT_IDS` still work as environment variable overrides.
When Tomo needs approval for a terminal command, reply `/approve` or `/deny` in Telegram.
The background gateway writes its PID to `.tomo/telegram.pid` and logs to `.tomo/telegram.log`.

## Commands

```bash
uv run tomo login
uv run tomo logout
uv run tomo auth-status
uv run tomo chat
uv run tomo desktop
uv run tomo desktop start
uv run tomo desktop stop
uv run tomo desktop restart
uv run tomo telegram start
uv run tomo telegram stop
uv run tomo telegram restart
uv run tomo telegram-config set --bot-token 123456:telegram-token --chat-ids 123456789
uv run tomo telegram-config show
uv run tomo telegram-config delete
```

Local OAuth tokens are stored in `.tomo/auth.json` and ignored by git.

Set `TOMO_MODEL` to override the default model, `grok-4.3`.
