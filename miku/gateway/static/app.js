// The cockpit. Plain ES modules, no framework, no build step.
//
// One renderer serves both the live turn and the traces tab. They differ only
// in how the records arrive -- one record at a time off an open stream, or all
// at once from a finished turn -- and not at all in what a record means. A
// second renderer would be a second place for the tree logic to be wrong.

const $ = (selector) => document.querySelector(selector);

// --- the tree ---------------------------------------------------------------

// Records are placed by `parent`, never by arrival order. Five fan-out branches
// arrive interleaved and in whatever order they finish; the shape comes from
// the links. A record whose parent has not been seen becomes a root rather than
// being dropped, so a truncated or still-arriving turn still renders.
export function buildTree(records) {
  const bySpan = new Map();
  for (const record of records) {
    if (record.span) bySpan.set(record.span, { record, children: [] });
  }

  const roots = [];
  for (const record of records) {
    const node = bySpan.get(record.span);
    if (!node) continue;
    const parent = record.parent ? bySpan.get(record.parent) : null;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

// The server hands back a finished turn already nested. Flatten it so the one
// renderer above stays the only thing that knows the shape.
export function flatten(nested, into = []) {
  for (const node of nested) {
    into.push(node.record);
    flatten(node.children, into);
  }
  return into;
}

// --- when it happened -------------------------------------------------------

// Offsets from the turn's first record, never from the record before. The
// events of a turn do not bracket work: `tools` records its event *before* its
// work so that the calls it makes have a parent to hang under, while assemble,
// agent, plan_angles, generate and select_best all record theirs *after*. A gap
// between adjacent records is therefore the duration of something for five of
// six nodes and of nothing for the sixth, and nothing in the record says which.
// Measured from the start of the turn, every row is true the same way.
export function elapsed(records) {
  const offsets = new Map();
  let origin = null;

  for (const record of records) {
    const at = Date.parse(record.ts);
    // A record without a usable timestamp -- an error frame synthesised by the
    // gateway, say -- simply gets no offset rather than poisoning the origin.
    if (!Number.isFinite(at)) continue;
    if (origin === null) origin = at;
    // Clamped. `datetime.now(UTC)` is not monotonic, and a clock step mid-turn
    // would otherwise render "-0.40s", which reads as a bug in the agent rather
    // than one in the clock.
    offsets.set(record.span, Math.max(0, (at - origin) / 1000));
  }
  return offsets;
}

// Deliberately not labelled "started". See `elapsed`: for five of the six nodes
// this is when the step finished. `+2.31s` is true for all six.
function stamp(record, offsets) {
  const seconds = offsets.get(record.span);
  if (seconds === undefined) return "";
  return `<span class="at" title="${escape(record.ts)}">+${seconds.toFixed(2)}s</span>`;
}

function label(record) {
  const parts = [];
  const kind = record.node ? `${record.kind}:${record.node}` : record.kind;
  parts.push(`<span class="kind ${record.kind}">${escape(kind)}</span>`);

  if (record.branch !== undefined && record.branch !== null) {
    parts.push(`<span class="branch">[branch ${record.branch}]</span>`);
  }
  const detail = describe(record);
  if (detail) parts.push(`<span class="detail">${escape(detail)}</span>`);
  return parts.join(" ");
}

// What is worth showing per kind. Deliberately small: the trace carries more
// than a person can read at a glance, and a wall of JSON is not observability.
function describe(record) {
  if (record.kind === "tool_call") return `${record.tool}(${short(record.args)})`;
  if (record.kind === "tool") return record.ok ? `${record.tool} ok` : `${record.tool} FAILED`;
  if (record.kind === "error") return record.error || "";
  if (record.kind === "clamp") return `narrowed to ${record.using} of ${record.asked}`;
  if (record.kind === "budget") return "request budget spent";
  if (record.node === "plan_angles") return `${record.branches} options in parallel`;
  if (record.node === "generate") {
    return record.ok ? `${record.angle || ""} ${record.day || ""} ${record.start_time || ""}` : "no usable slot";
  }
  if (record.node === "select_best") return `picked ${record.chosen} of ${record.candidates}`;
  if (record.node === "assemble") return `${record.facts ?? 0} facts, today ${record.today ?? ""}`;
  if (record.iteration !== undefined) return `iteration ${record.iteration}`;
  return "";
}

function short(args, limit = 70) {
  const text = Object.entries(args || {})
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(", ");
  return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
}

function escape(text) {
  return String(text).replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );
}

function renderNodes(nodes, offsets) {
  if (!nodes.length) return "";
  const items = nodes
    .map(
      (node) =>
        `<li class="node">${stamp(node.record, offsets)}${label(node.record)}` +
        `${renderNodes(node.children, offsets)}</li>`,
    )
    .join("");
  return `<ul>${items}</ul>`;
}

export function renderTree(target, records) {
  if (!records.length) {
    target.innerHTML = '<p class="dim">nothing recorded.</p>';
    return;
  }
  // Offsets are additional information on a row, not a reordering of them: the
  // tree is still built from `parent`, as it always was.
  target.innerHTML = renderNodes(buildTree(records), elapsed(records));
}

// --- the diagram ------------------------------------------------------------

// The agent's architecture, written by hand.
//
// Deliberately NOT derived from the compiled graph. LangGraph knows the
// execution schedule -- three nodes and a cycle -- and that is not what a reader
// needs. `recall_facts` is exactly the "retrieve context" step a reader looks
// for, and it is a line inside `assemble`, not a node. `load_persona` likewise.
// The fan-out is behind a tool because build.py chose that: "delegation adds no
// node and no edge here". A derived diagram would be unfalsifiable and
// uninformative at once -- every box right, and the two things worth seeing
// missing.
//
// Hand-authored is therefore a second place the architecture is written down.
// That is accepted; going unwatched is not. `nodesClaimed` below feeds a test
// that fails when the graph gains or loses a node this does not account for.
// The guard covers what is derivable; `inside` is not derivable and not guarded.
//
// Edges carry no words. `conditional` renders them dashed and `when` becomes a
// title, because a label on every arrow was noise -- a turn ends at END whether
// or not the page says "no tool calls" -- while the difference between a routing
// decision and an unconditional edge is worth keeping.
export const TOPOLOGY = {
  entry: "START",
  exit: "END",
  boxes: [
    {
      id: "assemble",
      title: "assemble",
      nodes: ["assemble"],
      // No count. It runs once per turn, so a badge reading "1" is noise.
      inside: ["load persona (SOUL.md)", "recall facts from the store"],
    },
    {
      id: "agent",
      title: "agent",
      nodes: ["agent"],
      // One `node` record per lap, and the same return increments `iterations`,
      // so this badge equals TurnResult.iterations by construction. A case
      // asserts exactly that -- the badge against a number the session computed
      // without the diagram's help.
      count: { kind: "node", unit: "lap" },
      inside: ["one model call, every tool bound"],
      from: "assemble",
      exit: true,
      exitWhen: "the model asked for no tools, so the turn ends",
    },
    {
      id: "tools",
      title: "tools",
      nodes: ["tools"],
      // One `tool_call` record per requested call, which is what a reader means
      // by "how much did this do". Counting records instead read x3 for a single
      // remember -- the node event, the request and the result all carry
      // node=tools. Equals len(TurnResult.tool_calls), and a case asserts it.
      count: { kind: "tool_call", unit: "call" },
      // The fan-out subgraph runs inside this node, reached through
      // propose_slots. Its nodes are accounted for here rather than drawn as
      // their own box: a separate box earned its place when it was the only
      // thing that moved during a fan-out, and it stopped earning it once these
      // names count through `tools` -- the count climbing x1..x8 is the same
      // signal with one less box. Listed, so the drift guard still covers them
      // and so restoring the box is a change to this line rather than a search.
      deferred: ["plan_angles", "generate", "select_best", "format"],
      inside: ["run every requested call", "a failure becomes a result"],
      from: "agent",
      conditional: true,
      when: "the model asked for a tool",
      loopsTo: "agent",
      backWhen: "every tools run returns -- this edge has no condition",
    },
  ],
};

// Which box owns each node name, drawn or deferred.
function ownerOf(topology) {
  const owner = new Map();
  for (const box of topology.boxes) {
    for (const name of box.nodes) owner.set(name, box.id);
    for (const name of box.deferred || []) owner.set(name, box.id);
  }
  return owner;
}

// The graph node names this diagram accounts for. The drift guard's only
// consumer: it compares this against the nodes the builders actually register.
// Deferred names are included -- not drawing a node is a layout decision, and
// must not quietly shrink what the guard checks.
export function nodesClaimed(topology) {
  return topology.boxes.flatMap((box) => box.nodes.concat(box.deferred || []));
}

// The kinds a step emits on being entered. An allowlist, not a denylist: a
// kind nobody has classified should fail to count rather than silently inflate
// a number. `cap` and `budget` are in it because a turn that hits either ran the
// agent node -- it just never reached a model call.
const ENTRY_KINDS = new Set(["node", "cap", "budget"]);

// What each box shows, as a pure function of the whole record set -- never an
// accumulation, and never mutated in place.
//
// Three questions, deliberately not collapsed into one number:
//
//   lit     did anything happen here at all
//   count   the thing this box's badge means, declared per box
//   inside  entries of nodes this box accounts for without drawing
//
// They came apart because one number could not answer all three honestly. `lit`
// cannot be `count > 0`: a turn that hits the iteration cap emits `cap` and no
// `node` record, so the agent would report zero laps and go dark despite being
// what ended the turn. And `count` cannot be "records seen here", which is what
// it used to be -- `tools` emits three records for one tool call, so a single
// `remember` displayed as x3.
//
// Two things follow from purity. One renderer serves a turn in progress and a
// turn read back from a file, because "in progress" is just a shorter list. And
// a replay later is `paint(topology, records.filter(r => r.ts <= t))` plus a
// slider, rather than a rewrite of a painter that only knows how to go forwards.
export function paint(topology, records) {
  const owner = ownerOf(topology);
  const deferred = new Map();
  const counted = new Map();
  const painted = {};

  // Seeded in box order, so two paintings of the same records are identical
  // objects rather than merely equivalent ones.
  for (const box of topology.boxes) {
    painted[box.id] = { lit: false, count: 0, inside: 0 };
    if (box.count) counted.set(box.id, box.count.kind);
    for (const name of box.deferred || []) deferred.set(name, box.id);
  }

  for (const record of records) {
    const id = owner.get(record.node);
    // A record naming a node this diagram does not know -- an older trace, a
    // newer graph -- marks nothing and breaks nothing.
    if (id === undefined) continue;
    const state = painted[id];
    state.lit = true;

    if (deferred.has(record.node)) {
      if (ENTRY_KINDS.has(record.kind)) state.inside += 1;
    } else if (counted.get(id) === record.kind) {
      state.count += 1;
    }
  }
  return painted;
}

// How far the turn has got: the box owning the most recent record that names a
// node this diagram knows.
//
// Not "what is running". The events do not bracket work -- `tools` records its
// event before doing anything so the calls it makes have a parent, while every
// other node records after finishing -- so the last record marks a node that has
// started for one of six nodes and one that has stopped for the other five.
// "How far it got" is true for all six, which is why nothing on screen labels
// this: a visual state can say "here" without claiming why.
//
// Deliberately NOT part of `paint`, which is required to be independent of
// arrival order. This is defined by arrival order. Folding it in would make one
// function that is both.
export function frontier(topology, records) {
  const owner = ownerOf(topology);
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const id = owner.get(records[index].node);
    if (id !== undefined) return id;
  }
  return null;
}

// A box carries a count, not a time. `agent` is entered three times in a
// three-lap turn and has no single timestamp; the tree below has one row per
// record, where the question does not arise.
//
// The unit is rendered with the number. Two boxes count different things now --
// laps and calls -- and a bare "x3" on both would be the same trap the count
// itself just came out of: a number that looks comparable and is not.
function renderBox(box, painted, at) {
  const state = painted[box.id] || { lit: false, count: 0, inside: 0 };
  const steps = (box.inside || [])
    .map((step) => `<li>${escape(step)}</li>`)
    .join("");

  const badges = [];
  if (box.count && state.count) {
    badges.push(`<span class="count">${state.count} ${unitFor(box.count.unit, state.count)}</span>`);
  }
  if (state.inside) {
    // Work this box accounts for without drawing. Without it a fan-out and a
    // one-call turn both read "1 call" and the diagram stops moving through the
    // longest turn there is.
    badges.push(`<span class="within">${state.inside} inside</span>`);
  }

  const marks = (state.lit ? " lit" : "") + (box.id === at ? " frontier" : "");
  return (
    `<div class="box${marks}">` +
    `<span class="box-title">${escape(box.title)}</span>` +
    (badges.length ? `<span class="badges">${badges.join("")}</span>` : "") +
    (steps ? `<ul class="inside">${steps}</ul>` : "") +
    `</div>`
  );
}

function unitFor(unit, count) {
  return count === 1 ? escape(unit) : `${escape(unit)}s`;
}

// The spine runs left to right: start, assemble context, think, answer. A box
// that loops back to another is drawn *below* that box rather than beside it,
// because vertical distance from the spine is depth of delegation -- `tools` is
// one step off the path because the turn detours and returns.
//
// HTML and CSS, no SVG coordinates and no layout algorithm. Three boxes do not
// need one, and eighty lines of graph-theoretic positioning is exactly the kind
// of indirection this repo is written to avoid.
//
// `at` is the frontier box, or null. The traces pane passes null: a finished
// turn has no frontier.
export function renderDiagram(target, topology, painted, at = null) {
  const excursions = new Map();
  for (const box of topology.boxes) {
    if (!box.loopsTo) continue;
    excursions.set(box.loopsTo, (excursions.get(box.loopsTo) || []).concat([box]));
  }
  const spine = topology.boxes.filter((box) => !box.loopsTo);

  const parts = [`<div class="terminal">${escape(topology.entry)}</div>`];
  for (const box of spine) {
    parts.push(link(box.conditional, box.when));
    parts.push(column(box, excursions.get(box.id) || [], painted, at));
    if (box.exit) parts.push(link(true, box.exitWhen));
  }
  parts.push(`<div class="terminal">${escape(topology.exit)}</div>`);

  target.innerHTML = `<div class="flow">${parts.join("")}</div>`;
}

function column(box, away, painted, at) {
  const parts = [renderBox(box, painted, at)];

  for (const excursion of away) {
    // Two connectors, not one double-headed link. Down is conditional --
    // route_after_agent decides -- while up is not: every tools run returns.
    // Dashed and solid carry that without a word on either.
    parts.push(
      '<div class="loop">' +
        arrow("down", excursion.conditional, excursion.when) +
        arrow("up", false, excursion.backWhen) +
        "</div>",
    );
    parts.push(renderBox(excursion, painted, at));
  }
  return `<div class="rail">${parts.join("")}</div>`;
}

function arrow(direction, conditional, when) {
  const title = when ? ` title="${escape(when)}"` : "";
  return `<span class="${direction}${conditional ? " maybe" : ""}"${title}></span>`;
}

function link(conditional, when) {
  const title = when ? ` title="${escape(when)}"` : "";
  return `<div class="link${conditional ? " maybe" : ""}"${title}></div>`;
}

// --- streaming a turn -------------------------------------------------------

// SSE over a POST fetch: one connection per turn. `EventSource` was not used
// because it is GET-only, which would put the user's message in a URL.
async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data:")) yield JSON.parse(line.slice(5).trim());
      }
    }
  }
}

const live = { records: [], threadId: null };

// Repaint from the whole record set, never from the one that just arrived. The
// cost is four boxes; the benefit is that this is the same call the traces pane
// makes, and the same call a replay would make with a filtered list.
function repaint(selector, records, streaming = false) {
  const target = $(selector);
  if (!target) return;
  // The frontier only means something while records are still arriving. A
  // finished turn has no "here" -- it is all behind you.
  const at = streaming ? frontier(TOPOLOGY, records) : null;
  renderDiagram(target, TOPOLOGY, paint(TOPOLOGY, records), at);
}

async function sendTurn(message) {
  const status = $("#status");
  const replyBox = $("#reply");
  const button = $("#composer button");

  live.records = [];
  repaint("#diagram", live.records);
  replyBox.hidden = true;
  button.disabled = true;
  status.textContent = "thinking...";
  showPane("live");

  try {
    const response = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, thread_id: live.threadId }),
    });
    if (!response.ok) throw new Error(`server said ${response.status}`);

    for await (const event of readEvents(response)) {
      if (event.kind === "reply") {
        live.threadId = event.thread_id;
        replyBox.textContent = event.reply;
        replyBox.hidden = false;
        status.textContent = `${event.requests} requests, ${event.iterations} iterations`;
        // The turn is over, so the frontier is cleared while the counts stay.
        repaint("#diagram", live.records);
        continue;
      }
      if (event.kind === "error") {
        status.textContent = event.error;
      }
      live.records.push(event);
      renderTree($("#live-tree"), live.records);
      repaint("#diagram", live.records, true);
    }
  } catch (error) {
    status.textContent = String(error);
  } finally {
    button.disabled = false;
  }
}

// --- the read-only tabs -----------------------------------------------------

const rows = (pairs) =>
  pairs.map(([left, right]) => `<tr><th>${escape(left)}</th><td>${right}</td></tr>`).join("");

async function loadConfig() {
  const view = await (await fetch("/api/config")).json();
  $("#pane-config").innerHTML = `
    <h2>provider</h2>
    <table>${rows([
      ["provider", escape(view.provider)],
      ["user", escape(view.user_id)],
      ["state", escape(view.state_dir)],
    ])}</table>
    <h2>roles</h2>
    <table>${view.roles
      .map(
        (role) =>
          `<tr><th>${escape(role.role)}</th><td>${escape(role.model || role.error)}${
            role.override ? ' <span class="dim">(overridden)</span>' : ""
          }</td></tr>`,
      )
      .join("")}</table>
    <h2>limits</h2>
    <table>${rows(
      Object.entries(view.limits).map(([key, value]) => [key, escape(value)]),
    )}</table>`;
}

async function loadTools() {
  const tools = await (await fetch("/api/tools")).json();
  $("#pane-tools").innerHTML = `<table>${tools
    .map(
      (tool) =>
        `<tr><th>${escape(tool.name)}</th><td class="wrap">${escape(tool.description)}</td></tr>`,
    )
    .join("")}</table>`;
}

async function loadMemory() {
  const facts = await (await fetch("/api/memory")).json();
  $("#pane-memory").innerHTML = facts.length
    ? `<table>${facts
        .map(
          (fact) =>
            `<tr><th>${escape(fact.created_at.slice(0, 10))}</th>` +
            `<td class="wrap">${escape(fact.fact)}</td></tr>`,
        )
        .join("")}</table>`
    : '<p class="dim">nothing remembered yet.</p>';
}

async function loadTraces() {
  const listing = await (await fetch("/api/traces")).json();
  const list = $("#turn-list");
  list.innerHTML = listing.turns
    .map((turnId) => `<li><button data-turn="${escape(turnId)}">${escape(turnId)}</button></li>`)
    .join("");

  list.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      list
        .querySelectorAll("button")
        .forEach((other) => other.setAttribute("aria-current", String(other === button)));
      const nested = await (await fetch(`/api/traces/${button.dataset.turn}`)).json();
      const records = flatten(nested);
      renderTree($("#trace-tree"), records);
      // The same paint a live turn gets. A finished turn is only a longer list.
      repaint("#trace-diagram", records);
    });
  });
}

const LOADERS = {
  config: loadConfig,
  tools: loadTools,
  memory: loadMemory,
  traces: loadTraces,
};

function showPane(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.pane === name));
  });
  document.querySelectorAll(".pane").forEach((pane) => {
    pane.hidden = pane.dataset.pane !== name;
  });
  // Read on open rather than on a timer: these are cheap, and a cockpit that
  // polls is a cockpit that lies about when it last looked.
  LOADERS[name]?.().catch((error) => {
    $("#status").textContent = String(error);
  });
}

// Wiring is a function rather than top-level statements so this module can be
// imported without a DOM. `buildTree` is the port of waku's event application
// and the piece most likely to be subtly wrong -- five branches arriving out of
// order is not something eyeballing a page reliably catches -- so it is worth
// being able to test it directly.
export function mount() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => showPane(tab.dataset.pane));
  });

  // The diagram describes what the agent is, so it is complete and unmarked
  // before any turn has run.
  repaint("#diagram", []);
  repaint("#trace-diagram", []);

  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#message");
    const message = input.value.trim();
    if (!message) return;
    input.value = "";
    sendTurn(message);
  });
}

if (typeof document !== "undefined") mount();
