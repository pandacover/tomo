from __future__ import annotations

import argparse
import time

from .oauth import get_valid_tokens, login
from .token_store import delete_tokens, load_tokens
from .telegram import run_telegram
from .tui import run_chat


def main() -> None:
    parser = argparse.ArgumentParser(prog="butler")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("login", help="Sign in with SuperGrok OAuth")
    subparsers.add_parser("logout", help="Delete local OAuth tokens")
    subparsers.add_parser("auth-status", help="Show local auth status")
    subparsers.add_parser("chat", help="Launch the prompt_toolkit chat UI")
    subparsers.add_parser("telegram", help="Run the Telegram chat gateway")
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


if __name__ == "__main__":
    main()
