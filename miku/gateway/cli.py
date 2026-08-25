"""The terminal gateway.

It moves text and nothing else: no prompts assembled here, no models called, no
tools run, no memory read. It picks a thread and prints what the session
reports. That constraint is what makes a second gateway cheap to add later.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from miku.runtime.config import load_settings
from miku.runtime.providers import ProviderError
from miku.runtime.session import open_session

EXIT_WORDS = {"exit", "quit", ":q"}

BANNER = """miku - type a message, or 'exit' to leave.
thread: {thread_id}"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="miku", description="Talk to Miku in the terminal.")
    parser.add_argument(
        "--thread",
        dest="thread_id",
        default=None,
        help="Resume a named conversation. Defaults to a new one.",
    )
    return parser.parse_args(argv)


def new_thread_id() -> str:
    return uuid.uuid4().hex[:8]


def print_tool_activity(kind: str, payload: dict) -> None:
    """Show what the agent is doing while it does it."""
    if kind == "tool_call":
        print(f"  > {payload['tool']}({_short(payload['args'])})")


def _short(args: dict, limit: int = 60) -> str:
    text = ", ".join(f"{key}={value!r}" for key, value in args.items())
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def chat(thread_id: str) -> int:
    settings = load_settings()

    async with open_session(settings) as session:
        print(BANNER.format(thread_id=thread_id))
        while True:
            try:
                message = input("\nyou> ").strip()
            except EOFError:
                print()
                return 0

            if not message:
                continue
            if message.lower() in EXIT_WORDS:
                return 0

            result = await session.run_turn(
                message, thread_id=thread_id, on_event=print_tool_activity
            )
            print(f"\nmiku> {result.reply}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thread_id = args.thread_id or new_thread_id()

    try:
        return asyncio.run(chat(thread_id))
    except ProviderError as error:
        # Configuration is the one failure we want loud and early — but as a
        # sentence, not a traceback.
        print(f"miku: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 0
