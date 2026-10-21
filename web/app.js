// Fleet page: renders the snapshot (docs/snapshot.md) and keeps it live over Server-Sent Events.
"use strict";

let snapshot = null;
let receivedAt = 0;
let connected = false;
const pending = new Set();

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ms = (iso) => (iso ? Date.parse(iso) : NaN);

function ago(iso) {
  const t = ms(iso);
  if (Number.isNaN(t)) return "—";
  return span(Date.now() - t) + " ago";
}

function span(delta) {
  const s = Math.max(0, Math.round(delta / 1000));
  if (s < 60) return s + " s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + " min";
  const h = Math.floor(m / 60);
  if (h < 48) return h + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  return Math.floor(h / 24) + " d";
}

function until(iso) {
  const t = ms(iso);
  if (Number.isNaN(t)) return "—";
  const d = new Date(t);
  const day = d.toLocaleDateString(undefined, { weekday: "short" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${day} ${time} (in ${span(t - Date.now())})`;
}

function threadLink(threadId, text) {
  return `<a href="t3-thread://v1/${esc(threadId)}" title="Open in T3 Code">${esc(text)}</a>`;
}

const STATUS = {
  running: ["ok", "Running"], starting: ["ok", "Starting"], preparing: ["ok", "Preparing"], queued: ["ok", "Queued"],
  waiting: ["warn", "Waiting"], completed: ["", "Done"], idle: ["", "Idle"], interrupted: ["bad", "Interrupted"],
  failed: ["bad", "Failed"], cancelled: ["", "Cancelled"], rolled_back: ["", "Rolled back"],
};
const KIND = {
  loop: "Review loop", waiting: "Waiting", failed: "Failed", interrupted: "Interrupted", quiet: "Quiet",
};

function state(status) {
  const [tone, label] = STATUS[status] || ["", status || "Unknown"];
  return `<span class="state"><span class="dot ${tone}"></span>${esc(label)}</span>`;
}

function setHTML(id, html) {
  const el = $(id);
  if (el.innerHTML !== html) el.innerHTML = html;
}

function renderHeader(s) {
  setHTML("servers", s.servers.map((sv) => {
    const tone = sv.ok ? "ok" : "bad";
    const title = sv.ok ? `${sv.threads} unsettled threads · ${sv.latencyMs ?? "—"} ms` : `Unreachable: ${sv.error}`;
    return `<span class="chip ${sv.ok ? "" : "bad"}" title="${esc(title)}"><span class="dot ${tone}"></span>${esc(sv.id)}${sv.ok ? "" : ` · ${esc(ago(sv.lastSeenAt))}`}</span>`;
  }).join(""));
}

function renderStats(s) {
  const n = s.summary;
  const tiles = [
    ["Lanes", n.lanes, ""], ["Running", n.running, ""],
    ["Review loops", n.loops, n.loops ? "bad" : ""], ["Waiting", n.waiting, n.waiting ? "warn" : ""],
    ["Stalled", n.stalled, n.stalled ? "bad" : ""], ["To tidy", n.tidy, n.tidy ? "warn" : ""],
  ];
  setHTML("stats", tiles.map(([l, v, tone]) => `<div class="stat ${tone}"><div class="n">${v}</div><div class="l">${l}</div></div>`).join(""));
}

function renderAttention(s) {
  $("c-attention").textContent = s.attention.length || "";
  if (!s.attention.length) return setHTML("attention", `<div class="empty">Nothing is stuck, looping or waiting.</div>`);
  const rows = s.attention.map((a) => {
    const lane = a.laneTitle && a.laneTitle !== a.title ? `<div class="sub">in ${esc(a.laneTitle)}</div>` : "";
    const pr = a.prUrl ? ` · <a href="${esc(a.prUrl)}">PR</a>` : "";
    return `<tr>
      <td class="icon"><span class="dot ${a.severity === "bad" ? "bad" : "warn"}"></span></td>
      <td><div class="title">${threadLink(a.threadId, a.title)} <span class="badge ${a.severity}">${esc(KIND[a.kind] || a.kind)}</span></div>${lane}</td>
      <td>${esc(a.reason)}<div class="sub">since ${esc(ago(a.since))}${pr}</div></td>
      <td class="sub nowrap hide-s">${esc(a.server)}</td>
    </tr>`;
  }).join("");
  setHTML("attention", `<table><tbody>${rows}</tbody></table>`);
}

function sideEffect(e) {
  if (!e) return `<span class="sub">none seen</span>`;
  return `<span class="nowrap">${esc(ago(e.at))}</span><div class="sub mono" title="${esc(e.summary)}">${esc(e.kind)}</div>`;
}

function prs(list) {
  if (!list.length) return `<span class="sub">—</span>`;
  return list.map((p) => {
    const round = p.round == null ? "" : ` · r${p.round}`;
    const tone = p.state === "open" && p.round != null && p.round - 1 >= (snapshot.thresholds.loopRound || 2) ? "bad" : p.state === "merged" ? "ok" : "";
    return `<a class="badge ${tone}" href="${esc(p.url)}" title="${esc(p.repository)} ${esc(p.state)}">#${p.number}${round}</a>`;
  }).join(" ");
}

function renderLanes(s) {
  $("c-lanes").textContent = s.lanes.length || "";
  if (!s.lanes.length) return setHTML("lanes", `<div class="empty">No unsettled lanes.</div>`);
  const rows = s.lanes.map((l) => {
    const flags = l.flags.map((f) => `<span class="badge ${f === "loop" || f === "failed" || f === "interrupted" ? "bad" : "warn"}">${esc(KIND[f] || f)}</span>`).join(" ");
    const kids = l.children.total ? `${l.children.running} running<div class="sub">of ${l.children.total}</div>` : `<span class="sub">0</span>`;
    return `<tr>
      <td><div class="title">${threadLink(l.threadId, l.title)}${l.roster ? ` <span class="badge" title="In the harness lane roster">roster</span>` : ""}</div>
        <div class="sub">${esc(l.server)} · ${esc(l.project)} · ${esc(l.account)} · ${esc(l.model)}</div></td>
      <td>${state(l.status)}${flags ? `<div>${flags}</div>` : ""}</td>
      <td class="hide-s">${l.currentStep ? esc(l.currentStep) : `<span class="sub">—</span>`}<div class="sub">updated ${esc(ago(l.updatedAt))}</div></td>
      <td>${sideEffect(l.lastSideEffect)}</td>
      <td class="nowrap">${kids}</td>
      <td>${prs(l.prs)}</td>
    </tr>`;
  }).join("");
  setHTML("lanes", `<table><thead><tr><th>Lane</th><th>State</th><th class="hide-s">Current step</th><th>Last side effect</th><th>Children</th><th>PRs · round</th></tr></thead><tbody>${rows}</tbody></table>`);
}

function renderTidy(s) {
  $("c-tidy").textContent = s.tidy.length || "";
  if (!s.tidy.length) return setHTML("tidy", `<div class="empty">Nothing to tidy.</div>`);
  const rows = s.tidy.map((t) => {
    const key = `${t.server}|${t.threadId}`;
    const busy = pending.has(key) ? " disabled" : "";
    const buttons = t.actions.map((a) => `<button class="btn" data-action="${a}" data-server="${esc(t.server)}" data-thread="${esc(t.threadId)}"${busy}>${a === "settle" ? "Settle" : "Nudge"}</button>`).join("");
    const parent = t.parentTitle ? `<div class="sub">child of ${esc(t.parentTitle)}</div>` : "";
    return `<tr>
      <td><div class="title">${threadLink(t.threadId, t.title)}</div>${parent}</td>
      <td>${esc(t.reason)}<div class="sub">idle ${esc(span(Date.now() - ms(t.idleSince)))}</div></td>
      <td class="right">${buttons}</td>
    </tr>`;
  }).join("");
  setHTML("tidy", `<table><tbody>${rows}</tbody></table>`);
}

const PACE = { on_pace: ["ok", "On pace"], too_fast: ["bad", "Too fast"], too_slow: ["warn", "Too slow"], unknown: ["", "Unknown"] };
const WEEK_MS = 7 * 24 * 3600 * 1000;

function paceDetail(a) {
  if (a.pace === "too_fast" && a.runsOutAt) return `runs out ${until(a.runsOutAt)}`;
  if (a.pace === "too_slow" && a.roomPercent != null && a.burnPerHour != null && a.resetsAt) {
    const hours = Math.max(0, (ms(a.resetsAt) - Date.now()) / 3600000);
    const left = Math.max(0, a.roomPercent - a.burnPerHour * hours);
    return `about ${Math.round(left)}% will go unused`;
  }
  if (a.pace === "too_slow" && a.roomPercent != null && a.resetsAt) {
    const target = Math.max(0, (ms(a.resetsAt) - Date.now()) / WEEK_MS * 100);
    return `approximate, no burn reading: ${Math.round(a.roomPercent)}% left, about ${Math.round(target)}% on pace`;
  }
  if (a.burnPerHour != null && a.sustainablePerHour != null) return `${a.burnPerHour.toFixed(2)} vs ${a.sustainablePerHour.toFixed(2)} pts/h`;
  return a.state === "unknown" ? "no current reading" : esc(a.state);
}

function renderAccounts(s) {
  if (!s.accounts.length) return setHTML("accounts", `<div class="empty">No account readings yet.</div>`);
  const order = { too_fast: 0, too_slow: 1, on_pace: 2, unknown: 3 };
  const list = [...s.accounts].sort((a, b) => (order[a.pace] ?? 4) - (order[b.pace] ?? 4) || (a.roomPercent ?? 101) - (b.roomPercent ?? 101));
  setHTML("accounts", list.map((a) => {
    const [tone, label] = PACE[a.pace] || PACE.unknown;
    const room = a.roomPercent == null ? "—" : `${Math.round(a.roomPercent)}% left`;
    const width = a.roomPercent == null ? 0 : Math.max(0, Math.min(100, a.roomPercent));
    const target = a.resetsAt ? Math.max(0, Math.min(100, ((ms(a.resetsAt) - Date.now()) / WEEK_MS) * 100)) : null;
    const marker = target == null ? "" : `<b style="left:calc(${target.toFixed(1)}% - 1px)" title="On-pace room now: ${Math.round(target)}%"></b>`;
    return `<div class="acct">
      <div class="row"><span><span class="title">${esc(a.id)}</span> <span class="sub">${esc(a.name)}${a.running ? ` · ${a.running} running` : ""}</span></span>
        <span class="num nowrap">${room}</span></div>
      <div class="meter ${tone === "bad" ? "bad" : tone === "warn" ? "warn" : ""}"><i style="width:${width}%"></i>${marker}</div>
      <div class="row" style="margin:6px 0 0"><span><span class="badge ${tone}">${label}</span> <span class="sub">${paceDetail(a)}</span></span>
        <span class="sub nowrap">resets ${esc(until(a.resetsAt))}</span></div>
    </div>`;
  }).join(""));
}

function renderFooter(s) {
  const sources = Object.entries(s.sources).map(([name, src]) => `${name} ${src.ok ? "ok" : "failing (" + src.error + ")"} ${ago(src.at)}`).join(" · ");
  const t = s.thresholds;
  setHTML("footer", `Flags: PR at fix round ${t.loopRound}+ · waiting over ${t.waitingMinutes} min · failed or interrupted · running with no push, PR, merge, deploy or comment for ${t.quietHours} h · done and unsettled over ${t.tidyMinutes} min.<br>Sources: ${esc(sources || "none yet")}. The marker on each account bar is where room should be now to reach zero exactly at reset.`);
}

function renderLive() {
  if (!snapshot) return;
  const age = Date.now() - ms(snapshot.generatedAt);
  const tone = !connected ? "bad" : age > 30000 ? "warn" : "ok";
  const text = !connected ? `Disconnected · data from ${ago(snapshot.generatedAt)}` : `Live · updated ${ago(snapshot.generatedAt)}`;
  setHTML("live", `<span class="dot ${tone}"></span><span>${esc(text)}</span>`);
}

function render() {
  if (!snapshot) return;
  renderHeader(snapshot);
  renderStats(snapshot);
  renderAttention(snapshot);
  renderLanes(snapshot);
  renderTidy(snapshot);
  renderAccounts(snapshot);
  renderFooter(snapshot);
  renderLive();
}

function accept(data) {
  snapshot = data;
  receivedAt = Date.now();
  render();
}

function connect() {
  const events = new EventSource("api/events");
  events.addEventListener("snapshot", (e) => { connected = true; accept(JSON.parse(e.data)); });
  events.onopen = () => { connected = true; renderLive(); };
  events.onerror = () => { connected = false; renderLive(); };
}

let toastTimer = 0;
function toast(text, bad) {
  const el = $("toast");
  el.textContent = text;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast" + (bad ? " bad" : ""); }, 3000);
}

document.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  const { action, server, thread } = button.dataset;
  const key = `${server}|${thread}`;
  pending.add(key);
  button.disabled = true;
  try {
    const response = await fetch(`api/actions/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Fleet-Action": "1" },
      body: JSON.stringify({ server, threadId: thread }),
    });
    const result = await response.json().catch(() => ({ ok: false, error: `http-${response.status}` }));
    toast(result.ok ? (action === "settle" ? "Settled" : "Nudge sent") : `Could not ${action}: ${result.error}`, !result.ok);
  } catch (error) {
    toast(`Could not ${action}: network error`, true);
  } finally {
    pending.delete(key);
    render();
  }
});

fetch("api/snapshot").then((r) => r.json()).then(accept).catch(() => {});
connect();
// Relative times: the status line every second; the tables less often so text selection survives.
setInterval(renderLive, 1000);
setInterval(render, 15000);
