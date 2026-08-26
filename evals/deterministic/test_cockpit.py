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

from evals.helpers import WEB_SKIP_REASON, StubModel, has_web_extra, says
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


def test_the_repository_declares_no_javascript_toolchain():
    """A build step inside a uv-managed Python repo is the fastest way to lose
    the legibility this project optimises for."""
    forbidden = ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"]
    forbidden += ["vite.config.js", "webpack.config.js", "tsconfig.json"]

    present = [name for name in forbidden if (REPO / name).exists()]
    assert not present, f"a frontend toolchain crept in: {present}"

    assert not (REPO / "node_modules").exists()


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
