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

function renderNodes(nodes) {
  if (!nodes.length) return "";
  const items = nodes
    .map(
      (node) =>
        `<li class="node">${label(node.record)}${renderNodes(node.children)}</li>`,
    )
    .join("");
  return `<ul>${items}</ul>`;
}

export function renderTree(target, records) {
  if (!records.length) {
    target.innerHTML = '<p class="dim">nothing recorded.</p>';
    return;
  }
  target.innerHTML = renderNodes(buildTree(records));
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

async function sendTurn(message) {
  const status = $("#status");
  const replyBox = $("#reply");
  const button = $("#composer button");

  live.records = [];
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
        continue;
      }
      if (event.kind === "error") {
        status.textContent = event.error;
      }
      live.records.push(event);
      renderTree($("#live-tree"), live.records);
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
      renderTree($("#trace-tree"), flatten(nested));
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
