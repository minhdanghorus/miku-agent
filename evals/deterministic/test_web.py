"""The web gateway, driven in-process.

No port is bound, no credentials are needed, and no live model is called: the
real ASGI app runs over `httpx.ASGITransport` against a session holding the same
stub model and frozen clock the rest of the suite uses.

Assertions are on event shape and stored state, never on rendered wording --
the same rule the deterministic evaluators follow. A cockpit that renders the
tree differently is a UI change; a stream that loses a parent link is a defect.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from evals.helpers import (
    WEB_SKIP_REASON,
    PromptModel,
    StubModel,
    has_web_extra,
    says,
    wants,
)
from miku.ops.traceview import read_records
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.calendar_store import events_on
from miku.tools.clock import Clock

pytestmark = pytest.mark.skipif(not has_web_extra(), reason=WEB_SKIP_REASON)

FIXED_CLOCK = Clock.fixed("2026-08-25")  # a Tuesday


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester", max_iterations=3)


def booking_model() -> StubModel:
    return StubModel(
        [
            wants("create_event", {"title": "Tennis", "day": "2026-08-29", "start_time": "08:00"}),
            says("Booked Tennis for Saturday at 08:00."),
        ]
    )


async def client_for(session):
    """An httpx client speaking to the app in-process."""
    import httpx

    from miku.gateway.web import create_app

    app = create_app(session=session)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://cockpit")


def events_from(body: str) -> list[dict]:
    """Every `data:` payload in an SSE body, in order."""
    return [
        json.loads(line[len("data:") :].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


async def post_turn(client, message: str, thread_id: str = "tab-a") -> list[dict]:
    response = await client.post(
        "/api/turn", json={"message": message, "thread_id": thread_id}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return events_from(response.text)


# --- Streaming a turn -------------------------------------------------------


async def test_a_turn_streams_progress_then_a_reply(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "book tennis saturday 8am")

    assert len(events) > 1, "nothing but the reply reached the client"
    assert events[-1]["kind"] == "reply"
    assert events[-1]["reply"].startswith("Booked Tennis")
    assert events[-1]["requests"] == 2
    assert events[-1]["turn_id"]
    assert all(event["kind"] != "reply" for event in events[:-1])


async def test_the_stream_carries_the_tool_call_with_its_arguments(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "book tennis saturday 8am")

    requested = [event for event in events if event["kind"] == "tool_call"]
    assert len(requested) == 1
    assert requested[0]["tool"] == "create_event"
    assert requested[0]["args"]["title"] == "Tennis"


async def test_streamed_events_keep_their_causal_links(settings):
    """The cockpit builds a tree from `parent`. A broken link is an orphan node."""
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "book tennis saturday 8am")

    records = [event for event in events if event["kind"] != "reply"]
    spans = {record["span"] for record in records}

    assert len(records) >= 2
    for record in records:
        assert record.get("parent") in spans or record.get("parent") is None


async def test_one_post_runs_exactly_one_turn(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "book tennis saturday 8am")
        trace_path = session.deps.tracer.path

    turn_ids = {
        record["turn_id"] for record in read_records(trace_path) if record.get("turn_id")
    }
    assert len(turn_ids) == 1
    assert events[-1]["turn_id"] in turn_ids
    assert len(await events_on(settings.db_path, "2026-08-29")) == 1


async def test_an_empty_message_is_refused(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            response = await client.post("/api/turn", json={"message": "   "})

    assert response.status_code == 400


async def test_a_turn_that_fails_reports_the_failure_as_an_event(settings):
    """SSE has no status code after the headers, so a failure must be an event."""

    class Exploding(StubModel):
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("the provider fell over")

    async with open_session(
        settings, model=Exploding([says("never reached")]), clock=FIXED_CLOCK
    ) as session:
        client = await client_for(session)
        async with client:
            response = await client.post("/api/turn", json={"message": "hello"})
            events = events_from(response.text)

    assert response.status_code == 200
    assert events[-1]["kind"] == "error"
    assert "the provider fell over" in events[-1]["error"]


# --- Delegated work ---------------------------------------------------------


async def test_a_fanout_streams_one_record_per_branch_under_one_parent(settings):
    """The claim Phase 2's tracing was built for, finally read back over HTTP."""
    from evals.deterministic.test_fanout import responder

    async with open_session(
        settings, model=PromptModel(respond=responder()), clock=FIXED_CLOCK
    ) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "when can I fit a run in next week?")

    branches = [event for event in events if event.get("node") == "generate"]

    assert len(branches) == settings.fanout_branches
    assert len({branch["parent"] for branch in branches}) == 1
    assert len({branch["branch"] for branch in branches}) == settings.fanout_branches


# --- Concurrency ------------------------------------------------------------


async def test_two_concurrent_posts_do_not_interfere(settings):
    def respond(text: str):
        if "Created:" in text:
            return says("Booked Tennis for Saturday at 08:00.")
        if "tennis" in text.lower():
            return wants(
                "create_event",
                {"title": "Tennis", "day": "2026-08-29", "start_time": "08:00"},
            )
        return says("Hei, nothing to book.")

    async with open_session(
        settings, model=PromptModel(respond=respond), clock=FIXED_CLOCK
    ) as session:
        client = await client_for(session)
        async with client:
            chat, booking = await asyncio.gather(
                post_turn(client, "just saying hello", thread_id="tab-a"),
                post_turn(client, "book tennis saturday 8am", thread_id="tab-b"),
            )

    assert chat[-1]["requests"] == 1
    assert booking[-1]["requests"] == 2
    assert chat[-1]["turn_id"] != booking[-1]["turn_id"]

    for stream in (chat, booking):
        turn_id = stream[-1]["turn_id"]
        recorded = {event["turn_id"] for event in stream if event["kind"] != "reply"}
        assert recorded == {turn_id}


# --- The read endpoints -----------------------------------------------------


async def test_the_read_endpoints_report_the_running_system(settings):
    from miku.memory.store import remember_fact

    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        await remember_fact(session.deps.store, settings, "I train on Thursdays")
        client = await client_for(session)
        async with client:
            config = (await client.get("/api/config")).json()
            tools = (await client.get("/api/tools")).json()
            memory = (await client.get("/api/memory")).json()

    assert config["provider"]
    assert {role["role"] for role in config["roles"]} >= {"main", "judge", "select"}
    assert "propose_slots" in {tool["name"] for tool in tools}
    assert [fact["fact"] for fact in memory] == ["I train on Thursdays"]


async def test_a_recorded_turn_is_readable_back_as_a_tree(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "book tennis saturday 8am")
            turn_id = events[-1]["turn_id"]

            listing = (await client.get("/api/traces")).json()
            tree = (await client.get(f"/api/traces/{turn_id}")).json()

    assert turn_id in listing["turns"]
    assert len(tree) == 1
    assert tree[0]["children"]


async def test_an_unknown_turn_reads_back_empty_rather_than_failing(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            response = await client.get("/api/traces/no-such-turn")

    assert response.status_code == 200
    assert response.json() == []


# --- The constraint under test ----------------------------------------------


def test_importing_the_web_gateway_does_not_import_the_terminal_gateway():
    """The Phase 1 claim, asserted rather than assumed.

    If a web gateway cannot be built without dragging in the terminal one, the
    seam was never where Phase 1 said it was.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import miku.gateway.web; "
        "print('miku.gateway.cli' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", result.stdout


def test_the_web_gateway_holds_no_agent_logic():
    """It moves data. Prompt assembly, model construction and tool execution
    all live behind the session, and this is what keeps them there."""
    import io
    import pathlib
    import tokenize

    import miku.gateway.web as web

    source = pathlib.Path(web.__file__).read_text(encoding="utf-8")
    code = " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.COMMENT, tokenize.STRING)
    )

    for forbidden in ("chat_model", "build_system_prompt", "SystemMessage", "bind_tools"):
        assert forbidden not in code, f"the web gateway must not reference {forbidden}"
    # It may hold a store handle to hand to the inspection surface, but it must
    # never query one itself.
    for forbidden in ("asearch", "aget", "aput"):
        assert forbidden not in code, f"the web gateway must not call {forbidden}"


# --- A client that goes away -------------------------------------------------


async def test_a_client_disconnecting_mid_turn_does_not_abort_the_turn(settings):
    """Observability is not a correctness dependency, and neither is an audience.

    Driven against `stream_turn` directly rather than through the transport,
    because closing the generator is exactly what starlette does when a client
    drops -- and doing it explicitly makes the moment of the disconnect
    deterministic instead of a race.
    """
    from miku.gateway.web import stream_turn

    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        stream = stream_turn(session, "book tennis saturday 8am", "tab-a")

        first = await stream.__anext__()
        assert first.startswith("data:")

        await stream.aclose()

        # The turn kept running with nobody listening; give it the loop back.
        for _ in range(200):
            if await events_on(settings.db_path, "2026-08-29"):
                break
            await asyncio.sleep(0.01)

    booked = await events_on(settings.db_path, "2026-08-29")
    assert len(booked) == 1, "the turn was abandoned when its listener left"


# --- The optional extra ------------------------------------------------------


def test_a_missing_extra_is_reported_as_a_sentence(monkeypatch):
    """Not an ImportError traceback. The one loud failure class is configuration,
    and it is loud as a line telling you what to type."""
    import builtins

    import miku.gateway.web as web
    from miku.runtime.providers import ProviderError

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("fastapi"):
            raise ImportError("No module named 'fastapi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(ProviderError) as excinfo:
        web.create_app()

    assert "uv sync --extra web" in str(excinfo.value)
