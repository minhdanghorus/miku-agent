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
    # Optional on purpose: a bare `miku` still opens a conversation, which is
    # what every existing invocation does.
    commands = parser.add_subparsers(dest="command")
    tidy = commands.add_parser(
        "consolidate",
        help="Resolve contradictions and duplicates in long-term memory.",
    )
    tidy.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without it, the plan is printed and nothing changes.",
    )
    commands.add_parser("threads", help="List held conversations.")
    # Deliberately no removal flag. The listing is what the two gateways share;
    # the write is not, and a destructive terminal flag deserves its own
    # argument rather than a ride on a read.
    return parser.parse_args(argv)


def new_thread_id() -> str:
    return uuid.uuid4().hex[:8]


def print_tool_activity(kind: str, payload: dict) -> None:
    """Show what the agent is doing while it does it.

    Fed from two places: a `tool_call` event before a tool runs (which carries
    the arguments), and the turn's trace records. A fan-out lives entirely in a
    subgraph inside a tool, so without the trace the terminal would go quiet for
    six model calls and then print an answer.

    Branches arrive in whatever order they finish. Each line names its branch, so
    interleaving reads as interleaving rather than as confusion.
    """
    node = payload.get("node", "")

    if kind == "tool_call":
        print(f"  > {payload['tool']}({_short(payload['args'])})")
    elif kind == "clamp":
        print(f"    ... narrowed to {payload['using']} of {payload['asked']} "
              f"({payload['reason']})")
    elif kind == "budget":
        print("    ... request budget spent")
    elif node == "plan_angles":
        print(f"    ... exploring {payload.get('branches', 0)} options in parallel")
    elif node == "generate":
        branch = payload.get("branch", "?")
        if payload.get("ok"):
            print(f"    [{branch}] {payload.get('angle', '')}: "
                  f"{payload.get('day', '')} {payload.get('start_time', '')}")
        else:
            print(f"    [{branch}] no usable slot")
    elif node == "select_best" and payload.get("candidates"):
        of = payload["candidates"]
        how = "judged" if payload.get("judged") else "only option"
        print(f"    ... picked option {payload.get('chosen', 0)} of {of} ({how})")


def print_consolidation_activity(kind: str, payload: dict) -> None:
    """Progress for a consolidation run, fed from the same trace listener.

    A pass is one model call over a possibly long list, so without this the
    terminal would sit silent and then print a verdict.
    """
    if kind == "budget":
        print("  ... request budget spent")
        return
    if kind != "consolidate":
        return

    node = payload.get("node", "")
    if node == "read":
        print(f"  ... {payload.get('facts', 0)} live facts")
    elif node == "plan" and not payload.get("ok", True):
        print(f"  ... could not plan: {payload.get('error', 'unknown error')}")
    elif node == "plan":
        print(f"  ... proposed {payload.get('proposed', 0)}, "
              f"{payload.get('applicable', 0)} usable")


def _quote(text: str, limit: int = 58) -> str:
    """One fact on one line, ASCII, never wide enough to wrap a console."""
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[: limit - 3] + "..."
    return f'"{flat}"'


def _fact_at(result, index: int) -> str:
    """Render a 1-based plan index as its fact, or as the number if it is bogus."""
    if 1 <= index <= len(result.facts):
        return _quote(result.facts[index - 1].fact)
    return f"#{index} (no such fact)"


def print_consolidation_report(result) -> None:
    """The whole outcome of a run, in ASCII. Windows consoles mangle the rest."""
    for operation in result.applicable:
        print(f"  [{operation.kind}]")
        for index in operation.stale:
            print(f"      retire {_fact_at(result, index)}")
        if operation.winner is not None:
            print(f"      keep   {_fact_at(result, operation.winner)}")
        # Only a merge writes text. Models routinely fill `fact` on the other
        # three kinds anyway -- measured on gemma, 3 of 3 operations -- and the
        # pass ignores it. Printing it would promise a rewrite that never
        # happens, and this report is what someone reads before typing --apply.
        if operation.fact and operation.kind == "merge":
            print(f"      write  {_quote(operation.fact)}")

    for drop in result.dropped:
        print(f"  [dropped: {drop.reason}] {drop.operation.kind}")

    for operation in result.skipped:
        print(f"  [skipped] {operation.kind} - facts moved during the run")

    if result.error:
        print(f"  could not consolidate: {result.error}")
    elif result.budget_exhausted:
        print("  request budget spent before the pass could run")
    elif not result.applicable:
        print("  nothing to consolidate")
    elif result.dry_run:
        print(f"\n  dry run - nothing written. {result.live_before} facts still live.")
        print("  run 'miku consolidate --apply' to apply this.")
    else:
        print(f"\n  applied {len(result.applied)}. "
              f"{result.live_before} facts live before, {result.live_after} after.")


def print_thread_listing(views) -> None:
    """Held conversations, newest first, in ASCII.

    Two columns are load-bearing rather than decorative. The identifier is what
    `--thread` takes, so a listing that omitted it would be a listing you could
    not act on. The message count is the cost of resuming: every stored message
    is re-sent on every turn, with no trimming and no prompt caching, so a long
    conversation is an expensive one and this is where that becomes visible.
    """
    if not views:
        print("  no conversations yet.")
        return

    for view in views:
        when = view.updated_at[:16].replace("T", " ")
        count = f"{view.message_count} msg" + ("" if view.message_count == 1 else "s")
        print(f"  {view.thread_id:10} {when:16} {count:>8}  {view.title or '(no title yet)'}")
    print("")
    print(f"  resume one with: miku --thread {views[0].thread_id}")


async def list_threads() -> int:
    """Print what conversations exist. Reads through the inspection surface.

    It opens a checkpointer rather than a whole session on purpose: a listing
    that demanded provider credentials to print eight identifiers would be
    charging for a read. Opening the handle is not reading the source -- the
    reading is `inspect.thread_list`, the same call the web gateway makes, which
    is the second time the peer-gateway constraint has paid out.
    """
    from miku.memory.checkpointer import open_checkpointer
    from miku.runtime.inspect import thread_list

    settings = load_settings()
    async with open_checkpointer(settings) as checkpointer:
        views = await thread_list(checkpointer)
        print(f"miku - {len(views)} conversation" + ("" if len(views) == 1 else "s"))
        print_thread_listing(views)
        return 0


async def consolidate_memory(apply: bool) -> int:
    """Open the pass, run it once, print what it did. No logic beyond that."""
    from miku.memory.consolidate import open_consolidation

    settings = load_settings()
    async with open_consolidation(settings, on_event=print_consolidation_activity) as run:
        print("miku - tidying long-term memory")
        result = await run(apply=apply)
        print_consolidation_report(result)
        return 0


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
        if args.command == "consolidate":
            return asyncio.run(consolidate_memory(apply=args.apply))
        if args.command == "threads":
            return asyncio.run(list_threads())
        return asyncio.run(chat(thread_id))
    except ProviderError as error:
        # Configuration is the one failure we want loud and early — but as a
        # sentence, not a traceback.
        print(f"miku: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 0
