"""Tools: storage round-trips, input validation, registry lookup. No model calls."""

from __future__ import annotations

import pytest

from miku.memory.store import open_store, recall_facts
from miku.runtime.config import load_settings
from miku.tools.calendar_store import Event, events_on, insert_event
from miku.tools.clock import Clock
from miku.tools.registry import UnknownToolError, build_tools, lookup
from miku.tools.scheduling import build_scheduling_tools


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester")


def tool_named(tools, name):
    return lookup(tools, name)


async def test_event_round_trips(settings):
    await insert_event(settings.db_path, Event("Tennis with Raj", "2026-08-29", "08:00"))
    found = await events_on(settings.db_path, "2026-08-29")
    assert [(e.title, e.start_time) for e in found] == [("Tennis with Raj", "08:00")]


async def test_two_events_on_one_day_both_persist_in_time_order(settings):
    await insert_event(settings.db_path, Event("Dentist", "2026-09-01", "09:00"))
    await insert_event(settings.db_path, Event("Gym", "2026-09-01", "07:00"))

    found = await events_on(settings.db_path, "2026-09-01")
    assert [e.title for e in found] == ["Gym", "Dentist"]


async def test_listing_an_empty_day_returns_nothing(settings):
    assert await events_on(settings.db_path, "2026-12-25") == []


async def test_events_survive_reopening_the_database(settings):
    await insert_event(settings.db_path, Event("Standup", "2026-08-26", "09:30"))
    # events_on opens its own connection, so this is a genuine reopen.
    reopened = load_settings(state_dir=settings.state_dir)
    assert len(await events_on(reopened.db_path, "2026-08-26")) == 1


async def test_create_event_tool_persists_and_confirms(settings):
    create = tool_named(build_scheduling_tools(settings), "create_event")
    result = await create.ainvoke(
        {"title": "Tennis with Raj", "day": "2026-08-29", "start_time": "08:00"}
    )

    assert "Tennis with Raj" in result
    assert len(await events_on(settings.db_path, "2026-08-29")) == 1


async def test_list_events_tool_reports_an_empty_day_honestly(settings):
    list_events = tool_named(build_scheduling_tools(settings), "list_events")
    result = await list_events.ainvoke({"day": "2026-12-25"})
    assert "No events" in result


async def test_create_event_rejects_a_weekday_name(settings):
    """Relative dates must be resolved before the tool, not inside it."""
    create = tool_named(build_scheduling_tools(settings), "create_event")
    with pytest.raises(ValueError, match="absolute ISO date"):
        await create.ainvoke({"title": "Tennis", "day": "Saturday", "start_time": "08:00"})


async def test_create_event_rejects_a_malformed_time(settings):
    create = tool_named(build_scheduling_tools(settings), "create_event")
    with pytest.raises(ValueError, match="HH:MM"):
        await create.ainvoke({"title": "Tennis", "day": "2026-08-29", "start_time": "8am"})


async def test_remember_tool_writes_one_fact(settings):
    async with open_store(settings) as store:
        remember = tool_named(build_tools(settings, store), "remember")
        await remember.ainvoke({"fact": "Alex prefers morning meetings"})
        assert await recall_facts(store, settings) == ["Alex prefers morning meetings"]


async def test_registry_exposes_the_expected_tools(settings):
    async with open_store(settings) as store:
        names = {tool.name for tool in build_tools(settings, store)}
        assert names == {"create_event", "list_events", "remember"}


async def test_unknown_tool_name_raises_and_names_the_alternatives(settings):
    async with open_store(settings) as store:
        tools = build_tools(settings, store)
        with pytest.raises(UnknownToolError) as excinfo:
            lookup(tools, "delete_everything")

    message = str(excinfo.value)
    assert "delete_everything" in message
    assert "create_event" in message


def test_fixed_clock_is_stable():
    clock = Clock.fixed("2026-08-29")
    assert clock.today.isoformat() == "2026-08-29"
    assert "Saturday" in clock.describe()


def test_real_clock_reports_today():
    from datetime import date

    assert Clock.real().today == date.today()
