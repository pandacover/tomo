from __future__ import annotations

import argparse
import time

from .oauth import get_valid_tokens, login
from .telegram_config import (
    TelegramConfig,
    delete_telegram_config,
    load_telegram_config,
    parse_allowed_chat_ids,
    save_telegram_config,
)
from .token_store import delete_tokens, load_tokens
from .telegram import run_telegram
from .tui import run_chat


def main() -> None:
    parser = argparse.ArgumentParser(prog="tomo")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("login", help="Sign in with SuperGrok OAuth")
    subparsers.add_parser("logout", help="Delete local OAuth tokens")
    subparsers.add_parser("auth-status", help="Show local auth status")
    subparsers.add_parser("chat", help="Launch the prompt_toolkit chat UI")
    subparsers.add_parser("telegram", help="Run the Telegram chat gateway")
    telegram_config_parser = subparsers.add_parser("telegram-config", help="Manage saved Telegram gateway config")
    telegram_config_subparsers = telegram_config_parser.add_subparsers(dest="telegram_config_command")
    telegram_config_set = telegram_config_subparsers.add_parser("set", help="Add or update Telegram gateway config")
    telegram_config_set.add_argument("--bot-token", required=True, help="Telegram bot token from BotFather")
    telegram_config_set.add_argument(
        "--chat-ids",
        default="",
        help="Comma-separated Telegram chat IDs allowed to use the gateway",
    )
    telegram_config_subparsers.add_parser("show", help="Show saved Telegram gateway config")
    telegram_config_subparsers.add_parser("delete", help="Delete saved Telegram gateway config")
    args = parser.parse_args()

    match args.command:
        case "login":
            login()
            print("Logged in.")
        case "logout":
            delete_tokens()
            print("Logged out.")
        case "auth-status":
            auth_status()
        case "chat":
            run_chat()
        case "telegram":
            run_telegram()
        case "telegram-config":
            telegram_config(args)
        case _:
            parser.print_help()


def auth_status() -> None:
    tokens = load_tokens()
    if tokens is None:
        print("Not logged in.")
        return
    print("Refresh token: present")
    print(f"Access token expired: {tokens.expired}")
    print(f"Expires in: {int(tokens.expires_at - time.time())}s")
    if tokens.expired:
        get_valid_tokens()
        print("Access token refreshed.")


def telegram_config(args: argparse.Namespace) -> None:
    match args.telegram_config_command:
        case "set":
            save_telegram_config(
                TelegramConfig(
                    bot_token=args.bot_token,
                    allowed_chat_ids=parse_allowed_chat_ids(args.chat_ids),
                )
            )
            print("Telegram config saved.")
        case "show":
            config = load_telegram_config()
            if config is None:
                print("No saved Telegram config.")
                return
            print("Bot token: present")
            print(f"Allowed chat IDs: {','.join(str(chat_id) for chat_id in config.allowed_chat_ids) or '(none)'}")
        case "delete":
            delete_telegram_config()
            print("Telegram config deleted.")
        case _:
            print("Choose a telegram-config command: set, show, or delete.")


if __name__ == "__main__":
    main()
