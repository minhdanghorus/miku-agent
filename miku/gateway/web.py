"""The web gateway — a peer of the terminal, not a feature of it.

Held to the same constraint `cli.py` is held to: it moves data and nothing else.
No prompts are assembled here, no model is called, no tool is run, and no store
is queried. It picks the thread, hands the message to the session, and forwards
what comes back. Anything a rendered view needs to *read* comes from
`runtime/inspect.py`, which is why a memory tab does not make this module a
place memory is read.

There is deliberately no import edge to `cli.py`. That absence is the
measurement Phase 3b exists for: Phase 1 shaped the terminal gateway so a second
gateway would be cheap, and until now nothing had tried to be one.

Handles arrive by name -- `session.tools`, `session.store`, `session.checkpointer`
-- and no longer by reaching through `session.deps`. Phase 3b left that reach at
two on purpose, as the price of the phase; a conversation screen needed a third,
and a count nobody chose is worse than either. The measurement is spent.

Removal is the one write here besides a turn, and it goes the same way a turn
does: `session.delete_conversation(...)`. The rule this module lives under is
that it reads no source directly, which calling a session method does not touch.

Progress reaches the browser through the same `on_event` seam the terminal uses.
The one real mismatch is direction -- `run_turn` pushes synchronously, SSE pulls
asynchronously -- and an `asyncio.Queue` is the whole adapter:

    tracer.event() -> listener(record) -> queue.put_nowait(record)
                                                 |
                          async for <- generator -+-> "data: {...}"

`put_nowait` on an unbounded queue never blocks and never awaits, so a slow or
vanished browser cannot stall a turn. The turn's own request allowance bounds
how much can pile up.
"""

# Deliberately without `from __future__ import annotations`, which every other
# module in this repo has. FastAPI reads a route's annotations to decide what to
# inject, and postponed annotations turn them into strings it resolves against
# module globals. FastAPI is imported lazily here -- so that a missing extra is
# a sentence rather than an ImportError -- which makes it a local name, not a
# global one. The symptom is not an error but a downgrade: `request:
# fastapi.Request` becomes an unresolvable type, FastAPI falls back to treating
# it as a query parameter, and every endpoint answers 422.

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from miku.runtime import inspect as introspect
from miku.runtime.config import Settings, load_settings
from miku.runtime.providers import ProviderError
from miku.runtime.session import Session, open_session

STATIC_DIR = Path(__file__).parent / "static"

MISSING_EXTRA = (
    "miku-web needs the web extra. Install it with: uv sync --extra web"
)

# Marks the end of one turn's stream. A sentinel rather than closing the queue,
# because the generator must still drain whatever is already in it.
DONE = object()


def _require_fastapi():
    """Import FastAPI, or fail as a sentence.

    Configuration problems are the one class of failure this project makes loud,
    and a missing optional dependency is one. It reaches the user as a line
    telling them what to type, never as an ImportError traceback.
    """
    try:
        import fastapi
        import fastapi.responses
        import fastapi.staticfiles
    except ImportError as error:  # pragma: no cover - exercised by the message test
        raise ProviderError(MISSING_EXTRA) from error
    return fastapi


def new_thread_id() -> str:
    return uuid.uuid4().hex[:8]


def sse(payload: dict) -> str:
    """One server-sent event. `default=str` so an odd payload cannot kill a turn."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


async def stream_turn(session: Session, message: str, thread_id: str) -> AsyncIterator[str]:
    """Run one turn, yielding its events as they happen, then its reply.

    The turn runs as its own task so that events can be forwarded while it is
    still going. Everything the browser sees has already passed the trace sink,
    which is what makes it safe to forward verbatim.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def watch(_kind: str, record: dict) -> None:
        queue.put_nowait(record)

    async def run():
        try:
            return await session.run_turn(message, thread_id=thread_id, on_event=watch)
        finally:
            queue.put_nowait(DONE)

    turn = asyncio.create_task(run())

    while True:
        record = await queue.get()
        if record is DONE:
            break
        yield sse(record)

    try:
        result = await turn
    except Exception as error:  # noqa: BLE001 - after the headers, a failure is an event
        # SSE has no status code once the response has begun, so the only place
        # left to report this is the stream itself.
        yield sse({"kind": "error", "error": f"{type(error).__name__}: {error}"})
        return

    yield sse(
        {
            "kind": "reply",
            "reply": result.reply,
            "turn_id": result.turn_id,
            "requests": result.requests,
            "iterations": result.iterations,
            "thread_id": thread_id,
        }
    )


def create_app(session: Session | None = None, settings: Settings | None = None):
    """Build the ASGI app.

    `session` is injectable for the same reason `open_session` takes a model:
    the eval suite drives the real app in-process with a stubbed model and a
    frozen clock, binding no port and needing no credentials. Left out, the
    lifespan opens one the way the CLI does.
    """
    fastapi = _require_fastapi()
    from fastapi.responses import StreamingResponse
    from fastapi.staticfiles import StaticFiles

    settings = settings or (session.settings if session else load_settings())

    @asynccontextmanager
    async def lifespan(app):
        if session is not None:
            app.state.session = session
            yield
            return
        async with open_session(settings) as opened:
            app.state.session = opened
            yield

    app = fastapi.FastAPI(title="miku cockpit", lifespan=lifespan)
    if session is not None:
        # Set here as well as in the lifespan, because an injected session is
        # already open and its owner decides when it closes. It also means the
        # app is usable by an ASGI transport that never runs a lifespan, which
        # is exactly how the evals drive it.
        app.state.session = session

    def current(request) -> Session:
        return request.app.state.session

    @app.post("/api/turn")
    async def turn(request: fastapi.Request):
        body = await request.json()
        message = str(body.get("message", "")).strip()
        if not message:
            raise fastapi.HTTPException(status_code=400, detail="message is required")
        thread_id = str(body.get("thread_id") or new_thread_id())

        return StreamingResponse(
            stream_turn(current(request), message, thread_id),
            media_type="text/event-stream",
            # Without this a proxy may buffer the whole turn and deliver it as
            # one lump, which defeats the entire point of streaming it.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/config")
    async def config(request: fastapi.Request):
        view = introspect.config_view(current(request).settings)
        return asdict(view)

    @app.get("/api/tools")
    async def tools(request: fastapi.Request):
        session_now = current(request)
        return [asdict(tool) for tool in introspect.tools_view(session_now.tools)]

    @app.get("/api/memory")
    async def memory(request: fastapi.Request):
        session_now = current(request)
        facts = await introspect.memory_view(session_now.store, session_now.settings)
        return [asdict(fact) for fact in facts]

    @app.get("/api/threads")
    async def threads(request: fastapi.Request):
        views = await introspect.thread_list(current(request).checkpointer)
        return [asdict(view) for view in views]

    @app.get("/api/threads/{thread_id}")
    async def thread(request: fastapi.Request, thread_id: str):
        exchanges = await introspect.conversation_view(current(request).checkpointer, thread_id)
        return [asdict(exchange) for exchange in exchanges]

    @app.delete("/api/threads/{thread_id}")
    async def remove_thread(request: fastapi.Request, thread_id: str):
        # Through the session, which is where writes have always gone. The
        # inspection surface is read-only and this is not routed through it.
        # Removing a conversation that is not there succeeds: absence is the
        # state the caller asked for.
        await current(request).delete_conversation(thread_id)
        return {"removed": thread_id}

    @app.get("/api/traces")
    async def traces(request: fastapi.Request, day: str | None = None):
        current_settings = current(request).settings
        return {
            "dates": introspect.trace_dates(current_settings),
            "turns": introspect.turn_ids_on(current_settings, day=day),
        }

    @app.get("/api/traces/{turn_id}")
    async def trace(request: fastapi.Request, turn_id: str, day: str | None = None):
        roots = introspect.turn_view(current(request).settings, turn_id, day=day)
        return [_as_json(root) for root in roots]

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="cockpit")

    return app


def _as_json(node) -> dict:
    """A trace node as nested JSON. The browser renders the same shape live."""
    return {"record": node.record, "children": [_as_json(child) for child in node.children]}


def run(argv: list[str] | None = None) -> int:
    """Serve the cockpit on loopback. One local user, no authentication."""
    import argparse

    parser = argparse.ArgumentParser(prog="miku-web", description="Watch Miku think.")
    parser.add_argument("--host", default="127.0.0.1", help="Default: loopback only.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    try:
        import uvicorn

        app = create_app()
    except ProviderError as error:
        print(f"miku-web: {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"miku-web: {MISSING_EXTRA} ({error})", file=sys.stderr)
        return 2

    print(f"miku cockpit on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
