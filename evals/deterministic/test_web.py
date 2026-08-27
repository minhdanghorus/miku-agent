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


# --- Conversations -----------------------------------------------------------


def remembering_model() -> StubModel:
    return StubModel(
        [
            wants("remember", {"fact": "Dang likes Detective Conan"}),
            says("Got it. I've added that to your preferences."),
        ]
    )


async def test_conversations_are_listed_and_read_back_through_the_endpoints(settings):
    async with open_session(settings, model=remembering_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            await post_turn(client, "I also like Detective Conan", thread_id="anime")

            listing = (await client.get("/api/threads")).json()
            transcript = (await client.get("/api/threads/anime")).json()

    assert [thread["thread_id"] for thread in listing] == ["anime"]
    assert listing[0]["title"] == "I also like Detective Conan"
    # The filtering, through the endpoint rather than only under the surface:
    # user, the tool's own sentence, then the reply -- and no empty assistant
    # line for the stored `AIMessage` that carried the call.
    assert [entry["role"] for entry in transcript] == ["user", "tool", "assistant"]
    assert all(entry["text"] for entry in transcript)


async def test_an_unknown_conversation_is_served_as_empty(settings):
    async with open_session(settings, model=booking_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            response = await client.get("/api/threads/no-such-conversation")

    assert response.status_code == 200
    assert response.json() == []


async def test_two_turns_on_one_thread_read_back_as_four_exchanges_in_order(settings):
    """Continuing a conversation needs no new request shape.

    `POST /api/turn` already took a thread identifier before this phase, which
    is what a correctly placed seam looks like: the conversation screen is a
    read path, not a change to how a turn is started.
    """
    model = StubModel([says("first reply"), says("second reply")])
    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            await post_turn(client, "first", thread_id="ongoing")
            await post_turn(client, "second", thread_id="ongoing")
            transcript = (await client.get("/api/threads/ongoing")).json()

    assert [entry["text"] for entry in transcript] == [
        "first",
        "first reply",
        "second",
        "second reply",
    ]


# --- Removing a conversation --------------------------------------------------


async def test_a_removed_conversation_leaves_the_listing_and_reads_back_empty(settings):
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            await post_turn(client, "hello", thread_id="doomed")
            removal = await client.delete("/api/threads/doomed")

            listing = (await client.get("/api/threads")).json()
            transcript = (await client.get("/api/threads/doomed")).json()

    assert removal.status_code == 200
    assert listing == []
    assert transcript == []


async def test_removing_one_conversation_leaves_every_other_one_intact(settings):
    model = StubModel([says("a"), says("b")])
    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            await post_turn(client, "keep me", thread_id="kept")
            await post_turn(client, "not me", thread_id="dropped")
            await client.delete("/api/threads/dropped")

            listing = (await client.get("/api/threads")).json()
            kept = (await client.get("/api/threads/kept")).json()

    assert [thread["thread_id"] for thread in listing] == ["kept"]
    assert [entry["text"] for entry in kept] == ["keep me", "a"]


async def test_a_fact_remembered_during_a_removed_conversation_is_still_live(settings):
    """The asymmetry the interface's wording exists for, asserted rather than trusted.

    Facts are namespaced by user, not by thread, so nothing about a conversation
    identifier reaches them. The confirmation in the cockpit says so -- and copy
    drifts, while this does not. If someone ever re-namespaces the store per
    thread, this case turns red and the sentence stops being a lie in advance.
    """
    async with open_session(settings, model=remembering_model(), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            await post_turn(client, "I also like Detective Conan", thread_id="anime")
            await client.delete("/api/threads/anime")

            memory = (await client.get("/api/memory")).json()

    assert [fact["fact"] for fact in memory] == ["Dang likes Detective Conan"]


async def test_a_removed_conversations_turns_are_still_reportable_by_turn_id(settings):
    """Traces are keyed by turn and carry nothing that names a thread.

    The same missing link the trace routes run into from the other side: there
    is no route from a conversation to the turns that made it, in either
    direction.
    """
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            events = await post_turn(client, "hello", thread_id="doomed")
            turn_id = events[-1]["turn_id"]
            await client.delete("/api/threads/doomed")

            tree = (await client.get(f"/api/traces/{turn_id}")).json()

    assert tree


async def test_removing_a_conversation_that_does_not_exist_succeeds(settings):
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            response = await client.delete("/api/threads/never-existed")
            listing = (await client.get("/api/threads")).json()

    assert response.status_code == 200
    assert listing == []


async def test_removal_goes_through_the_session_not_the_checkpointer(settings):
    """The gateway causes a write by calling a session method, exactly as running
    a turn always has. The rule it must not break is that it reads no source
    directly, which a session call does not touch."""
    calls = []

    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        original = session.delete_conversation

        async def watched(thread_id):
            calls.append(thread_id)
            await original(thread_id)

        session.delete_conversation = watched
        client = await client_for(session)
        async with client:
            await client.delete("/api/threads/whatever")

    assert calls == ["whatever"]


async def test_the_terminal_and_the_browser_report_the_same_conversations(settings):
    """One listing, two gateways. This is the claim the peer-gateway constraint
    makes, checked rather than assumed -- and the reason `miku threads` cost a
    subcommand rather than a second implementation."""
    from miku.runtime.inspect import thread_list

    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        client = await client_for(session)
        async with client:
            await post_turn(client, "hello", thread_id="shared")
            served = (await client.get("/api/threads")).json()
            direct = await thread_list(session.checkpointer)

    assert [thread["thread_id"] for thread in served] == [view.thread_id for view in direct]
    assert [thread["title"] for thread in served] == [view.title for view in direct]
    assert [thread["message_count"] for thread in served] == [
        view.message_count for view in direct
    ]


# --- The handles a gateway is given -------------------------------------------


async def test_each_accessor_reports_the_handle_the_session_was_built_with(settings):
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        assert session.tools is session.deps.tools
        assert session.store is session.deps.store
        # The one the session did not hold before this phase. Everything that
        # reads a conversation goes through it.
        assert session.checkpointer is not None
        assert hasattr(session.checkpointer, "alist")


def test_the_web_gateway_reaches_for_no_session_internals():
    """Phase 3b left this reach at two on purpose and recorded the number.

    A conversation screen needed a third handle. Three is where a measurement
    stops being a measurement and starts being a habit, so the accessors were
    added and the count is spent -- which only holds if nothing reaches past
    them afterwards.
    """
    import pathlib as stdlib_pathlib

    import miku.gateway.web as web

    source = stdlib_pathlib.Path(web.__file__).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if ".deps." in line and not line.strip().startswith("#")
    ]
    assert not offenders, offenders


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
