"""The cockpit's static assets, and the one piece of real logic inside them.

`buildTree` is the port of waku's event application and the most likely thing in
the frontend to be subtly wrong: five fan-out branches arrive interleaved and in
whatever order they finish, and eyeballing a rendered page does not reliably
catch a misplaced parent. Node is used to exercise it directly.

Node is a *system* tool here, never a project dependency -- there is no package
manifest, no lockfile and no bundler, and task 6.8 keeps it that way. These
cases skip when node is absent, exactly as the live cases skip without
credentials.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evals.helpers import WEB_SKIP_REASON, StubModel, has_web_extra, says, wants
from miku.graph.fanout import build_fanout_graph
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.clock import Clock

STATIC = Path(__file__).resolve().parents[2] / "miku" / "gateway" / "static"
REPO = Path(__file__).resolve().parents[2]

FIXED_CLOCK = Clock.fixed("2026-08-25")

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed (it is not a dependency)"
)


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester", max_iterations=3)


# --- Served, and buildless --------------------------------------------------


def test_the_cockpit_assets_exist():
    assert (STATIC / "index.html").is_file()
    assert (STATIC / "style.css").is_file()
    assert (STATIC / "app.js").is_file()


@pytest.mark.skipif(not has_web_extra(), reason=WEB_SKIP_REASON)
async def test_the_cockpit_page_and_its_assets_are_served(settings):
    import httpx

    from miku.gateway.web import create_app

    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        transport = httpx.ASGITransport(app=create_app(session=session))
        async with httpx.AsyncClient(transport=transport, base_url="http://cockpit") as client:
            page = await client.get("/")
            script = await client.get("/app.js")
            sheet = await client.get("/style.css")

    assert page.status_code == 200
    assert script.status_code == 200
    assert sheet.status_code == 200

    # Structure, not wording. The first version of this asserted the page
    # contained the string "miku", which broke the moment the persona's name was
    # capitalised -- a test failing on a copy edit, in a repo whose own rule is
    # that assertions read observable state and never prose. What actually makes
    # the page work is that it pulls the two assets served above.
    assert 'src="./app.js"' in page.text
    assert 'href="./style.css"' in page.text
    assert "<title>" in page.text
    # The diagram is rendered into containers the page declares, one per pane
    # that shows a turn. Without them the module has nothing to paint into.
    assert 'id="diagram"' in page.text
    assert 'id="trace-diagram"' in page.text
    # And the conversation screen's two: the sidebar it lists into and the
    # transcript it renders into.
    assert 'id="thread-list"' in page.text
    assert 'id="transcript"' in page.text


def test_the_repository_declares_no_javascript_toolchain():
    """A build step inside a uv-managed Python repo is the fastest way to lose
    the legibility this project optimises for."""
    forbidden = ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"]
    forbidden += ["vite.config.js", "webpack.config.js", "tsconfig.json"]

    present = [name for name in forbidden if (REPO / name).exists()]
    assert not present, f"a frontend toolchain crept in: {present}"

    assert not (REPO / "node_modules").exists()


def test_the_composer_sits_inside_the_conversation_it_belongs_to():
    """It used to sit above the tab strip, where it belonged to nothing.

    Structure, not appearance: the form is inside the pane that shows a
    conversation, below the transcript and above the turn's event tree. That
    ordering is the whole change, and it is the kind of thing a later edit
    reverts without noticing.
    """
    page = (STATIC / "index.html").read_text(encoding="utf-8")

    pane = page.index('id="pane-live"')
    transcript = page.index('id="transcript"')
    composer = page.index('id="composer"')
    tree = page.index('id="live-tree"')

    assert pane < transcript < composer < tree
    # And no longer above the tabs, which is where it was when it served every
    # pane and belonged to none of them.
    assert page.index('class="tabs"') < composer


def test_the_page_loads_its_script_as_a_module_without_a_bundle():
    page = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'type="module"' in page
    assert "./app.js" in page
    # No CDN, no import map, no framework tag.
    assert "http://" not in page and "https://" not in page


# --- The tree logic, run for real -------------------------------------------


def run_node(script: str) -> dict:
    """Run a snippet against the real app.js and return what it printed."""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=STATIC,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_the_module_imports_without_a_dom():
    """It must, or none of the cases below can run -- and a module that only
    works inside a page cannot have its logic tested at all."""
    shape = run_node(
        "import * as app from './app.js';"
        "console.log(JSON.stringify(Object.keys(app).sort()));"
    )
    assert "buildTree" in shape
    assert "flatten" in shape
    assert "paint" in shape
    assert "elapsed" in shape


@needs_node
def test_out_of_order_branches_still_land_under_one_parent():
    """The fan-out case, in the order a real turn delivers it: branches arrive
    interleaved, and two of them arrive before the step that caused them has
    been seen."""
    records = [
        {"span": "root", "parent": None, "kind": "node", "node": "agent"},
        {"span": "b3", "parent": "plan", "kind": "node", "node": "generate", "branch": 3},
        {"span": "b1", "parent": "plan", "kind": "node", "node": "generate", "branch": 1},
        {"span": "plan", "parent": "root", "kind": "node", "node": "plan_angles"},
        {"span": "b2", "parent": "plan", "kind": "node", "node": "generate", "branch": 2},
    ]
    shape = run_node(
        "import {buildTree} from './app.js';"
        f"const roots = buildTree({json.dumps(records)});"
        "console.log(JSON.stringify({"
        "roots: roots.length,"
        "planChildren: roots[0].children.find(c => c.record.span === 'plan')"
        "  ?.children.map(c => c.record.branch).sort()"
        "}));"
    )

    assert shape["roots"] == 1, "arrival order changed the shape of the tree"
    assert shape["planChildren"] == [1, 2, 3]


@needs_node
def test_a_record_whose_parent_has_not_arrived_becomes_a_root():
    """A turn still streaming, or a truncated file, must still render."""
    records = [{"span": "orphan", "parent": "never-seen", "kind": "node", "node": "generate"}]
    shape = run_node(
        "import {buildTree} from './app.js';"
        f"const roots = buildTree({json.dumps(records)});"
        "console.log(JSON.stringify({roots: roots.length}));"
    )
    assert shape["roots"] == 1


@needs_node
def test_a_finished_turn_flattens_back_to_the_records_it_came_from():
    """The traces tab receives nested JSON and the live panel receives a flat
    stream. One renderer serves both only because this round-trips."""
    nested = [
        {
            "record": {"span": "a", "parent": None, "kind": "node"},
            "children": [{"record": {"span": "b", "parent": "a", "kind": "tool"}, "children": []}],
        }
    ]
    shape = run_node(
        "import {buildTree, flatten} from './app.js';"
        f"const flat = flatten({json.dumps(nested)});"
        "const roots = buildTree(flat);"
        "console.log(JSON.stringify({"
        "spans: flat.map(r => r.span),"
        "roots: roots.length,"
        "children: roots[0].children.length}));"
    )

    assert shape["spans"] == ["a", "b"]
    assert shape["roots"] == 1
    assert shape["children"] == 1


@needs_node
def test_a_fanout_renders_as_five_branches_nested_under_one_step():
    """The claim task 6.4 asks to check by eye, checked by machine instead.

    `renderTree` only needs something with an `innerHTML` property, so the real
    renderer runs against a stand-in target. This is what would be on screen --
    five branch lines, all inside the list belonging to the step that caused
    them -- without depending on anyone having looked.
    """
    records = [{"span": "root", "parent": None, "kind": "node", "node": "agent"}]
    records.append({"span": "plan", "parent": "root", "kind": "node", "node": "plan_angles",
                    "branches": 5})
    # Reverse order on purpose: last branch first, as a real turn may deliver.
    for branch in (5, 4, 3, 2, 1):
        records.append(
            {
                "span": f"b{branch}",
                "parent": "plan",
                "kind": "node",
                "node": "generate",
                "branch": branch,
                "ok": True,
                "angle": f"angle-{branch}",
                "day": "2026-08-31",
                "start_time": "07:00",
            }
        )

    shape = run_node(
        "import {renderTree} from './app.js';"
        "const target = {innerHTML: ''};"
        f"renderTree(target, {json.dumps(records)});"
        "const html = target.innerHTML;"
        "const plan = html.slice(html.indexOf('plan_angles'));"
        "console.log(JSON.stringify({"
        r"branchLines: (html.match(/\[branch \d\]/g) || []),"
        r"nestedUnderPlan: (plan.match(/\[branch \d\]/g) || []).length,"
        "hasAngle: html.includes('angle-3'),"
        "escaped: !html.includes('<script>')}));"
    )

    assert len(shape["branchLines"]) == 5
    assert shape["nestedUnderPlan"] == 5, "branches rendered outside the step that caused them"
    assert shape["hasAngle"]


# --- the diagram, and the guard that keeps it honest -------------------------


def graph_node_names(session) -> set[str]:
    """Every node the builders actually register, across both graphs.

    The source of truth the hand-authored diagram is checked against. Built here
    rather than read off the gateway: the web gateway deliberately has no access
    to the compiled graph, and this test must not be the reason it gains one.
    """
    names = set(session.graph.get_graph().nodes)
    names |= set(build_fanout_graph(session.deps).get_graph().nodes)
    # `__start__` and `__end__` are the scheduler's, not the agent's.
    return {name for name in names if not name.startswith("__")}


def drift(claimed: set[str], registered: set[str]) -> list[str]:
    """What the diagram and the graph disagree about, named.

    Both directions matter. A node registered but not drawn is a diagram that
    has quietly gone stale; a node drawn but not registered is one that was
    never true. Returning the names rather than the two sets is the difference
    between a failure that says what to change and one that says something is
    wrong.
    """
    problems = []
    for name in sorted(registered - claimed):
        problems.append(f"the graph registers {name!r} and the diagram omits it")
    for name in sorted(claimed - registered):
        problems.append(f"the diagram draws {name!r} and no builder registers it")
    return problems


@needs_node
async def test_the_diagram_depicts_exactly_the_nodes_the_graph_registers(settings):
    """The guard. A hand-authored diagram is allowed; an unwatched one is not.

    This covers the derivable half of the diagram only. What a box says happens
    *inside* it -- the persona load and the fact recall inside `assemble` -- is
    exactly what the compiled graph cannot report, which is why it is drawn by
    hand, which is why nothing here can check it.
    """
    claimed = run_node(
        "import {TOPOLOGY, nodesClaimed} from './app.js';"
        "console.log(JSON.stringify({nodes: nodesClaimed(TOPOLOGY)}));"
    )["nodes"]

    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        registered = graph_node_names(session)

    # Two empty sets agree with each other. Asserted so this case cannot pass by
    # comparing nothing to nothing.
    assert claimed and registered

    problems = drift(set(claimed), registered)
    assert not problems, "; ".join(problems)


def test_the_drift_guard_fails_when_the_diagram_and_the_graph_disagree():
    """The control. A guard that cannot fail is not a guard."""
    assert drift({"agent", "tools"}, {"agent", "tools"}) == []

    omitted = drift({"agent"}, {"agent", "retrieve"})
    assert len(omitted) == 1
    assert "retrieve" in omitted[0]

    invented = drift({"agent", "ghost"}, {"agent"})
    assert len(invented) == 1
    assert "ghost" in invented[0]

    # The two directions are distinguishable, so a failure says which way the
    # disagreement runs rather than only that there is one.
    assert omitted != invented


# One `remember`, as the trace actually records it: the tools node is entered
# once and emits three records -- its own entry, the request, and the result.
ONE_TOOL_CALL = [
    {"span": "a", "kind": "node", "node": "assemble", "facts": 5},
    {"span": "b", "kind": "node", "node": "agent", "iteration": 1},
    {"span": "c", "kind": "node", "node": "tools", "executed": 1},
    {"span": "d", "kind": "tool_call", "node": "tools", "tool": "remember"},
    {"span": "e", "kind": "tool", "node": "tools", "tool": "remember", "ok": True},
    {"span": "f", "kind": "node", "node": "agent", "iteration": 2},
]


@needs_node
def test_a_turn_marks_the_boxes_for_the_nodes_it_ran():
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"console.log(JSON.stringify(paint(TOPOLOGY, {json.dumps(ONE_TOOL_CALL)})));"
    )

    assert shape == {
        # No rule declared: it runs once a turn, so it counts nothing.
        "assemble": {"lit": True, "count": 0, "inside": 0},
        "agent": {"lit": True, "count": 2, "inside": 0},
        "tools": {"lit": True, "count": 1, "inside": 0},
    }


@needs_node
def test_one_tool_call_counts_as_one():
    """The bug this rule exists for. `tools` emits three records for a single
    call -- the node entry, the request, the result -- so counting records read
    x3 for one `remember`, while `agent` counted laps correctly and the same
    badge meant two different things on two boxes."""
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"const painted = paint(TOPOLOGY, {json.dumps(ONE_TOOL_CALL)});"
        f"const records = {json.dumps(ONE_TOOL_CALL)};"
        "console.log(JSON.stringify({"
        "calls: painted.tools.count,"
        "recordsOnTools: records.filter(r => r.node === 'tools').length}));"
    )

    assert shape["recordsOnTools"] == 3, "the fixture stopped reproducing the bug"
    assert shape["calls"] == 1, "the badge is counting records again"


@needs_node
def test_a_capped_turn_stays_lit_with_nothing_to_count():
    """`lit` cannot be `count > 0`. A turn that hits the iteration cap emits
    `cap` from the agent node and no `node` record, so the box has zero laps to
    show and must still read as having run -- it is what ended the turn."""
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "b", "kind": "cap", "node": "agent", "iterations": 3, "limit": 3},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"console.log(JSON.stringify(paint(TOPOLOGY, {json.dumps(records)})));"
    )

    assert shape["agent"]["lit"] is True
    assert shape["agent"]["count"] == 0


@needs_node
def test_a_fanout_counts_through_the_step_that_delegated_it():
    """The subgraph has no box of its own, which is not the same as being
    invisible. Its nodes are declared on `tools`, so a fan-out makes that box's
    count climb rather than leaving the diagram unchanged for the length of the
    longest turn -- which is the failure a separate box was there to prevent.
    """
    records = [{"span": "t", "kind": "node", "node": "tools"}]
    records += [
        {"span": f"b{index}", "kind": "node", "node": node}
        for index, node in enumerate(
            ["plan_angles", "generate", "generate", "select_best", "format"]
        )
    ]
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"console.log(JSON.stringify(paint(TOPOLOGY, {json.dumps(records)})));"
    )

    assert shape["tools"]["inside"] == 5, "the subgraph's records reached no box"
    assert shape["tools"]["lit"] is True
    assert set(shape) == {"assemble", "agent", "tools"}


@needs_node
def test_work_that_is_not_drawn_is_still_covered_by_the_drift_guard():
    """Not drawing a node is a layout decision. It must not shrink what the
    guard checks, or removing a box would become a way to stop the graph being
    watched."""
    shape = run_node(
        "import {TOPOLOGY, nodesClaimed} from './app.js';"
        "console.log(JSON.stringify({"
        "nodes: nodesClaimed(TOPOLOGY),"
        "drawn: TOPOLOGY.boxes.length}));"
    )

    for name in ["plan_angles", "generate", "select_best", "format"]:
        assert name in shape["nodes"], f"{name} stopped being watched when its box went"
    assert shape["drawn"] == 3, "a box was drawn for work that is only accounted for"


@needs_node
def test_an_unknown_node_marks_nothing_and_breaks_nothing():
    """An older trace file, or a newer graph. Either must still paint."""
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "x", "kind": "node", "node": "retrieve_chunks"},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"console.log(JSON.stringify(paint(TOPOLOGY, {json.dumps(records)})));"
    )

    assert shape["assemble"]["lit"] is True
    assert not shape["agent"]["lit"] and not shape["tools"]["lit"]
    assert "retrieve_chunks" not in shape


@needs_node
def test_arrival_order_does_not_change_what_is_painted():
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "b", "kind": "node", "node": "agent"},
        {"span": "c", "kind": "node", "node": "generate"},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"const forward = paint(TOPOLOGY, {json.dumps(records)});"
        f"const backward = paint(TOPOLOGY, {json.dumps(list(reversed(records)))});"
        "console.log(JSON.stringify({"
        "same: JSON.stringify(forward) === JSON.stringify(backward)}));"
    )
    assert shape["same"], "the painted result depended on the order records arrived in"


@needs_node
def test_painting_record_by_record_ends_where_painting_all_at_once_ends():
    """The live pane calls `paint` again on every event with everything seen so
    far. If that did not converge on the same answer as one call, the live pane
    and the traces pane would be two renderers."""
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "b", "kind": "node", "node": "agent"},
        {"span": "c", "kind": "node", "node": "tools"},
        {"span": "d", "kind": "node", "node": "generate"},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"const all = {json.dumps(records)};"
        "let incremental = null;"
        "for (let i = 1; i <= all.length; i++)"
        "  incremental = paint(TOPOLOGY, all.slice(0, i));"
        "console.log(JSON.stringify({"
        "same: JSON.stringify(incremental) === JSON.stringify(paint(TOPOLOGY, all))}));"
    )
    assert shape["same"]


@needs_node
def test_the_diagram_renders_every_box_before_any_turn_has_run():
    """It describes what the agent is, not what a turn did, so it is complete
    with nothing painted."""
    shape = run_node(
        "import {TOPOLOGY, paint, renderDiagram} from './app.js';"
        "const target = {innerHTML: ''};"
        "renderDiagram(target, TOPOLOGY, paint(TOPOLOGY, []));"
        "console.log(JSON.stringify({"
        "boxes: (target.innerHTML.match(/class=.box[^-]/g) || []).length,"
        "declared: TOPOLOGY.boxes.length,"
        "lit: (target.innerHTML.match(/ lit/g) || []).length,"
        "recall: target.innerHTML.includes('recall facts'),"
        # The spine is one rail per box that does not loop back to another.
        "rails: (target.innerHTML.match(/class=.rail./g) || []).length,"
        "spine: TOPOLOGY.boxes.filter(b => !b.loopsTo).length,"
        # Two arrows for the cycle, not one double-headed link: down is the
        # router's decision, up is unconditional.
        "loops: (target.innerHTML.match(/class=.loop./g) || []).length,"
        "arrows: (target.innerHTML.match(/class=.(down|up)/g) || []).length,"
        # Dashed is what the removed labels used to say: the router decides this
        # edge. Solid means it has no condition at all.
        "conditional: (target.innerHTML.match(/ maybe/g) || []).length,"
        # The conditions did not vanish, they moved off the visible surface.
        "titled: (target.innerHTML.match(/title=/g) || []).length,"
        # Any text between an edge's tags would be a label come back.
        "worded: /class=.(link|down|up)[^>]*>[^<]/.test(target.innerHTML)}));"
    )

    assert shape["boxes"] == shape["declared"]
    assert shape["lit"] == 0, "a box was marked before any turn ran"
    # The step the compiled graph cannot report, drawn anyway.
    assert shape["recall"]

    # Structure, not wording. An earlier version asserted the string "back to
    # agent" and broke the moment the label was reworded -- the same mistake this
    # file has already made once, in a repo whose rule is that assertions read
    # observable state and never prose.
    assert shape["rails"] == shape["spine"]
    assert shape["loops"] == 1, "the cycle is not depicted"
    assert shape["arrows"] == 2, "the cycle collapsed to a single arrow"

    # Two of the three edges are routing decisions: agent -> tools, and the exit
    # to END. The return from tools is not one and must not render as though it
    # were.
    assert shape["conditional"] == 2
    assert shape["titled"] >= 3, "the conditions were dropped rather than moved"
    assert not shape["worded"], "an edge carries a label again"


@needs_node
def test_only_entry_kinds_count_as_work_inside_a_box():
    """`clamp` describes something that happened during a step, not a step. An
    allowlist keeps an unclassified kind from silently inflating a number."""
    records = [
        {"span": "t", "kind": "node", "node": "tools"},
        {"span": "p", "kind": "node", "node": "plan_angles"},
        {"span": "c", "kind": "clamp", "node": "plan_angles", "asked": 5, "using": 3},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"console.log(JSON.stringify(paint(TOPOLOGY, {json.dumps(records)})));"
    )
    assert shape["tools"]["inside"] == 1, "a within-step event was counted as a step"


@needs_node
def test_the_frontier_is_the_furthest_the_turn_has_got():
    """Not "what is running", which the records cannot say: `tools` records its
    event before working and every other node records after finishing, so the
    last record marks a started node once and a finished node five times. How
    far it got is true for all six."""
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "b", "kind": "node", "node": "agent"},
        {"span": "c", "kind": "node", "node": "tools"},
    ]
    shape = run_node(
        "import {TOPOLOGY, frontier} from './app.js';"
        f"const records = {json.dumps(records)};"
        "console.log(JSON.stringify({"
        "whole: frontier(TOPOLOGY, records),"
        "partial: frontier(TOPOLOGY, records.slice(0, 2)),"
        "empty: frontier(TOPOLOGY, [])}));"
    )

    assert shape["whole"] == "tools"
    assert shape["partial"] == "agent"
    assert shape["empty"] is None, "an empty turn has no frontier"


@needs_node
def test_the_frontier_skips_a_record_it_cannot_place():
    """A trailing record from a node the diagram does not know must not blank
    the frontier -- the turn is still somewhere."""
    records = [
        {"span": "a", "kind": "node", "node": "agent"},
        {"span": "x", "kind": "node", "node": "retrieve_chunks"},
    ]
    shape = run_node(
        "import {TOPOLOGY, frontier} from './app.js';"
        f"console.log(JSON.stringify({{at: frontier(TOPOLOGY, {json.dumps(records)})}}));"
    )
    assert shape["at"] == "agent"


@needs_node
def test_a_subgraph_record_puts_the_frontier_on_the_step_that_delegated():
    records = [
        {"span": "t", "kind": "node", "node": "tools"},
        {"span": "g", "kind": "node", "node": "generate"},
    ]
    shape = run_node(
        "import {TOPOLOGY, frontier} from './app.js';"
        f"console.log(JSON.stringify({{at: frontier(TOPOLOGY, {json.dumps(records)})}}));"
    )
    assert shape["at"] == "tools"


@needs_node
def test_the_frontier_depends_on_arrival_order_and_paint_does_not():
    """The pair is the point. `paint` is required to be order-independent, and
    the frontier is defined by order -- which is why they are two functions and
    not one that is somehow both."""
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "b", "kind": "node", "node": "agent"},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint, frontier} from './app.js';"
        f"const forward = {json.dumps(records)};"
        "const backward = [...forward].reverse();"
        "console.log(JSON.stringify({"
        "samePaint: JSON.stringify(paint(TOPOLOGY, forward))"
        "  === JSON.stringify(paint(TOPOLOGY, backward)),"
        "sameFrontier: frontier(TOPOLOGY, forward) === frontier(TOPOLOGY, backward)}));"
    )

    assert shape["samePaint"], "paint became order-dependent"
    assert not shape["sameFrontier"], "the frontier stopped tracking arrival order"


@needs_node
def test_only_the_frontier_box_is_marked_as_such():
    records = [
        {"span": "a", "kind": "node", "node": "assemble"},
        {"span": "b", "kind": "node", "node": "agent"},
    ]
    shape = run_node(
        "import {TOPOLOGY, paint, frontier, renderDiagram} from './app.js';"
        f"const records = {json.dumps(records)};"
        "const live = {innerHTML: ''}, done = {innerHTML: ''};"
        "renderDiagram(live, TOPOLOGY, paint(TOPOLOGY, records), frontier(TOPOLOGY, records));"
        "renderDiagram(done, TOPOLOGY, paint(TOPOLOGY, records), null);"
        "console.log(JSON.stringify({"
        "live: (live.innerHTML.match(/ frontier/g) || []).length,"
        "done: (done.innerHTML.match(/ frontier/g) || []).length,"
        "lit: (live.innerHTML.match(/ lit/g) || []).length}));"
    )

    assert shape["live"] == 1, "more than one box claimed to be the frontier"
    # A finished turn has no "here". The traces pane renders this way.
    assert shape["done"] == 0
    assert shape["lit"] == 2, "the frontier replaced the record of what ran"


@needs_node
def test_the_diagram_escapes_what_the_topology_carries():
    shape = run_node(
        "import {paint, renderDiagram} from './app.js';"
        "const evil = {entry: 'START', exit: 'END', boxes: [{id: 'x',"
        " title: '<img src=x onerror=alert(1)>', nodes: ['x'],"
        " inside: ['<script>bad()</script>']}]};"
        "const target = {innerHTML: ''};"
        "renderDiagram(target, evil, paint(evil, []));"
        "console.log(JSON.stringify({"
        "raw: target.innerHTML.includes('<img') || target.innerHTML.includes('<script>'),"
        "escaped: target.innerHTML.includes('&lt;img')}));"
    )

    assert not shape["raw"]
    assert shape["escaped"]


@needs_node
async def test_each_badge_equals_the_number_the_session_reported(settings):
    """The check the old count could not fail.

    Every earlier case asserted that `paint` counts what `paint` counts, so a
    badge meaning the wrong thing passed all of them. These two numbers are
    computed by the session with no involvement from the diagram: `iterations`
    comes off the agent node's own state and `tool_calls` off the graph's update
    stream. If a badge drifts from what the turn actually did, this fails.
    """
    seen: list[dict] = []

    model = StubModel([wants("remember", {"fact": "Alex bikes on Sundays"}), says("noted")])
    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn(
            "remember that", thread_id="badges", on_event=lambda _kind, record: seen.append(record)
        )

    shape = run_node(
        "import {TOPOLOGY, paint} from './app.js';"
        f"console.log(JSON.stringify(paint(TOPOLOGY, {json.dumps(seen)})));"
    )

    assert result.iterations == 2 and len(result.tool_calls) == 1
    assert shape["agent"]["count"] == result.iterations
    assert shape["tools"]["count"] == len(result.tool_calls)


@needs_node
async def test_every_declared_counting_rule_names_a_kind_a_turn_emits(settings):
    """A per-box rule can rot in silence: rename a kind and the badge quietly
    reads zero, with the drift guard none the wiser because it compares node
    names, not kinds."""
    seen: list[dict] = []

    model = StubModel([wants("remember", {"fact": "Alex bikes on Sundays"}), says("noted")])
    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn(
            "remember that", thread_id="kinds", on_event=lambda _kind, record: seen.append(record)
        )

    rules = run_node(
        "import {TOPOLOGY} from './app.js';"
        "console.log(JSON.stringify(TOPOLOGY.boxes"
        "  .filter(box => box.count)"
        "  .map(box => ({nodes: box.nodes, kind: box.count.kind}))));"
    )

    assert rules, "no box declares what its number counts"
    for rule in rules:
        matched = [
            record
            for record in seen
            if record.get("node") in rule["nodes"] and record.get("kind") == rule["kind"]
        ]
        assert matched, f"nothing in a real turn emits {rule['kind']} from {rule['nodes']}"


# --- when it happened -------------------------------------------------------

# Gaps of 2.30, 0.01 and 4.80 between adjacent records, so an implementation
# that measured the preceding gap instead of the offset from the start cannot
# accidentally agree with the expected values below.
TIMED = [
    {"span": "a", "parent": None, "kind": "node", "node": "assemble",
     "ts": "2026-08-27T09:00:00.000000+00:00"},
    {"span": "b", "parent": "a", "kind": "node", "node": "agent",
     "ts": "2026-08-27T09:00:02.300000+00:00"},
    {"span": "c", "parent": "b", "kind": "node", "node": "tools",
     "ts": "2026-08-27T09:00:02.310000+00:00"},
    {"span": "d", "parent": "c", "kind": "node", "node": "plan_angles",
     "ts": "2026-08-27T09:00:07.110000+00:00"},
]


@needs_node
def test_offsets_are_measured_from_the_start_of_the_turn():
    """Not from the record before it.

    The events of a turn do not bracket work -- `tools` records its event before
    doing anything so its calls have a parent, every other node records after --
    so a gap between adjacent records is the duration of something for five of
    six nodes and of nothing for the sixth. Offsets from the turn's first record
    are true for all six.
    """
    shape = run_node(
        "import {elapsed} from './app.js';"
        f"const at = elapsed({json.dumps(TIMED)});"
        "console.log(JSON.stringify({offsets: [...at.values()]}));"
    )

    assert shape["offsets"] == [0.0, 2.3, 2.31, 7.11]
    # The same numbers a gap-to-previous implementation would have produced,
    # stated here so this case fails loudly if someone switches to one.
    assert shape["offsets"] != [0.0, 2.3, 0.01, 4.8]


@needs_node
def test_a_record_without_a_usable_timestamp_gets_no_offset():
    """The gateway synthesises an error frame with no span and no `ts`. It must
    not become the origin every later offset is measured from."""
    records = [dict(TIMED[0]), {"kind": "error", "error": "boom"}, dict(TIMED[1])]
    shape = run_node(
        "import {elapsed} from './app.js';"
        f"const at = elapsed({json.dumps(records)});"
        "console.log(JSON.stringify({size: at.size, b: at.get('b')}));"
    )

    assert shape["size"] == 2
    assert shape["b"] == 2.3


@needs_node
def test_a_clock_step_backwards_clamps_to_zero():
    """`datetime.now(UTC)` is not monotonic. A negative offset would read as a
    bug in the agent rather than one in the clock."""
    records = [
        dict(TIMED[1]),  # later timestamp first, so the origin is ahead
        dict(TIMED[0]),
    ]
    shape = run_node(
        "import {elapsed} from './app.js';"
        f"const at = elapsed({json.dumps(records)});"
        "console.log(JSON.stringify({a: at.get('a')}));"
    )

    assert shape["a"] == 0


@needs_node
def test_showing_offsets_does_not_change_the_tree_that_is_rendered():
    """An offset is extra information on a row, not a reordering of rows."""
    without = [{key: value for key, value in r.items() if key != "ts"} for r in TIMED]
    shape = run_node(
        "import {renderTree} from './app.js';"
        "const timed = {innerHTML: ''}, bare = {innerHTML: ''};"
        f"renderTree(timed, {json.dumps(TIMED)});"
        f"renderTree(bare, {json.dumps(without)});"
        "const stripped = timed.innerHTML.replace(/<span class=\"at\"[^>]*>[^<]*<\\/span>/g, '');"
        "console.log(JSON.stringify({"
        "same: stripped === bare.innerHTML,"
        "stamped: (timed.innerHTML.match(/class=\"at\"/g) || []).length}));"
    )

    assert shape["same"], "the offset changed the structure it was added to"
    assert shape["stamped"] == 4


@needs_node
def test_the_renderer_escapes_what_a_record_carries():
    """Tool arguments are user text and now reach the browser. They are data."""
    records = [
        {
            "span": "a",
            "parent": None,
            "kind": "tool_call",
            "node": "tools",
            "tool": "remember",
            "args": {"fact": "<img src=x onerror=alert(1)>"},
        }
    ]
    shape = run_node(
        "import {renderTree} from './app.js';"
        "const target = {innerHTML: ''};"
        f"renderTree(target, {json.dumps(records)});"
        "console.log(JSON.stringify({"
        "raw: target.innerHTML.includes('<img'),"
        "escaped: target.innerHTML.includes('&lt;img')}));"
    )

    assert not shape["raw"], "a record's text was rendered as markup"
    assert shape["escaped"]


# --- The transcript ---------------------------------------------------------

# One `remember` turn, as `conversation_view` reports it -- which is to say, as
# the real database stores it once the empty `AIMessage` carrying the call has
# been dropped. Not invented: this shape was read back from `.miku/state.db`
# before the renderer was written.
CONVERSATION = [
    {"role": "user", "text": "Beside Naruto, I also like Detective Conan"},
    {"role": "tool", "text": "Remembered: Dang likes Detective Conan"},
    {"role": "tool", "text": "Remembered: Dang likes Dragon Ball"},
    {"role": "assistant", "text": "Got it. I've added those to your preferences."},
]


@needs_node
def test_a_tool_calling_turn_renders_its_tool_lines_and_no_empty_bubble():
    """Tool activity is always shown, and never as something the assistant said.

    Folding these behind a "2 calls" summary was considered and rejected: the
    tool line is the evidence that "I've added those to your preferences" is
    more than plausible, and without it the transcript says Miku replied and not
    that she did anything.
    """
    shape = run_node(
        "import { renderTranscript } from './app.js';"
        f"const html = renderTranscript({json.dumps(CONVERSATION)});"
        "console.log(JSON.stringify({"
        "  user: (html.match(/class=\"said user\"/g) || []).length,"
        "  assistant: (html.match(/class=\"said assistant\"/g) || []).length,"
        "  tool: (html.match(/class=\"said tool\"/g) || []).length,"
        "  keptVerbatim: html.includes('Remembered: Dang likes Dragon Ball'),"
        "  empty: html.includes('></li>')}));"
    )

    assert shape == {
        "user": 1,
        "assistant": 1,
        "tool": 2,
        "keptVerbatim": True,
        "empty": False,
    }


@needs_node
def test_the_transcript_names_who_is_speaking_rather_than_the_stored_role():
    """`assistant` is the server's word for a row, not a name a person reads.

    The role itself has to stay put -- it is the CSS hook and what every other
    case asserts on -- so this checks that the two came apart rather than that
    one was renamed.
    """
    shape = run_node(
        "import { renderTranscript } from './app.js';"
        f"const html = renderTranscript({json.dumps(CONVERSATION)});"
        "console.log(JSON.stringify({"
        "  named: html.includes('>Miku<'),"
        "  roleKept: html.includes('class=\"said assistant\"'),"
        "  roleShown: html.includes('>assistant<')}));"
    )

    assert shape == {"named": True, "roleKept": True, "roleShown": False}


@needs_node
def test_a_trace_route_is_offered_only_where_a_turn_is_known():
    """`turn_id` is reported when a turn runs and is not in checkpointed state.

    A conversation read back from storage therefore has none, and gets no route
    rather than a broken one.
    """
    shape = run_node(
        "import { renderTranscript } from './app.js';"
        f"const said = {json.dumps(CONVERSATION)};"
        "console.log(JSON.stringify({"
        "  live: (renderTranscript(said, 'turn-9').match(/to-trace/g) || []).length,"
        "  stored: (renderTranscript(said).match(/to-trace/g) || []).length}));"
    )

    assert shape == {"live": 1, "stored": 0}


@needs_node
def test_the_transcript_renders_text_as_text():
    """Markup in a message is content, not markup. Markdown would need a library,
    which would need a build step or a CDN -- both forbidden two cases above."""
    said = [{"role": "user", "text": "<script>alert('x')</script> & \"quoted\""}]
    shape = run_node(
        "import { renderTranscript } from './app.js';"
        f"const html = renderTranscript({json.dumps(said)});"
        "console.log(JSON.stringify({"
        "  raw: html.includes('<script>'),"
        "  escaped: html.includes('&lt;script&gt;')}));"
    )

    assert shape == {"raw": False, "escaped": True}


@needs_node
def test_an_empty_conversation_and_a_missing_field_both_render():
    """Absence is data on this side too. An exchange with no text is a row the
    server can legitimately produce, and it must not blank the whole screen."""
    shape = run_node(
        "import { renderTranscript } from './app.js';"
        "const partial = renderTranscript([{role: 'assistant'}]);"
        "console.log(JSON.stringify({"
        "  empty: renderTranscript([]).includes('nothing said yet'),"
        "  partial: partial.includes('said assistant'),"
        "  undefinedLeaked: partial.includes('undefined')}));"
    )

    assert shape == {"empty": True, "partial": True, "undefinedLeaked": False}


@needs_node
def test_the_thread_list_names_a_conversation_that_has_no_title_by_its_identifier():
    """Listed rather than hidden. Hiding it would leave state nothing on the page
    can reach -- and it is that decision, not the transcript, that put a remove
    button in this phase, so every listed row carries one."""
    threads = [
        {"thread_id": "anime", "title": "Beside Naruto", "message_count": 4},
        {"thread_id": "blank", "title": "", "message_count": 0},
    ]
    shape = run_node(
        "import { renderThreads } from './app.js';"
        f"const html = renderThreads({json.dumps(threads)}, 'anime');"
        "console.log(JSON.stringify({"
        "  rows: (html.match(/class=\"thread\"/g) || []).length,"
        "  blankNamed: html.includes('>blank<'),"
        "  removals: (html.match(/data-remove/g) || []).length,"
        "  current: (html.match(/aria-current/g) || []).length,"
        "  counts: html.includes('0 msgs') && html.includes('4 msgs')}));"
    )

    assert shape == {
        "rows": 2,
        "blankNamed": True,
        "removals": 2,
        "current": 1,
        "counts": True,
    }


@needs_node
def test_an_empty_listing_is_a_sentence_not_a_blank_sidebar():
    shape = run_node(
        "import { renderThreads } from './app.js';"
        "console.log(JSON.stringify({html: renderThreads([])}));"
    )

    assert "no conversations yet" in shape["html"]
