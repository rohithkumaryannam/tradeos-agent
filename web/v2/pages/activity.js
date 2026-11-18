import { html, useState, useEffect } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Tabs, Seg, Empty, confirmDialog, cx, Ago } from "../lib/ui.js";
import { useStore, store, act, refreshConfig } from "../lib/store.js";
import { api, fmtTime, timeAgo, strategyLabel, paramInfo, isNum } from "../lib/util.js";
import { FeedList } from "../lib/widgets.js";

const LABELS = {
  "risk.update": ["Risk controls updated", "shield"], "risk.reset": ["Risk reset to config.yaml", "undo"],
  "engine.update": ["Engine settings updated", "settings"], "engine.reset": ["Engine reset to config.yaml", "undo"],
  "slot.update": ["Strategy slot updated", "nodes"], "market.add": ["Market added", "plus"], "market.remove": ["Market removed", "trash"],
  "bot.start": ["Bot started", "play"], "bot.stop": ["Bot stopped", "stop"], "entries.pause": ["New entries paused", "pause"],
  "entries.resume": ["New entries resumed", "play"], "mode.set": ["Venue changed", "globe"], "execution.auto": ["Execution mode changed", "zap"],
  "strategy.global": ["Global strategy changed", "nodes"], "signal.confirm": ["Signal confirmed", "check"], "signal.dismiss": ["Signal dismissed", "x"],
  "position.close": ["Position closed", "x"], "positions.close_all": ["All positions closed", "octagon"], "trade.manual": ["Manual order", "candles"],
  "paper.reset": ["Paper balance reset", "refresh"], "alerts.test": ["Test alert sent", "bell"],
};
function label(e) {
  if (e.action?.startsWith("jarvis.")) return [`Jarvis · ${e.action.slice(7).replace(/_/g, " ")}`, "sparkles"];
  return LABELS[e.action] || [e.action, "info"];
}

function flat(obj, prefix = "") {
  const out = {};
  if (obj === null || obj === undefined || typeof obj !== "object" || Array.isArray(obj)) { out[prefix || "value"] = obj; return out; }
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flat(v, key));
    else out[key] = v;
  }
  return out;
}

function diffRows(before, after) {
  const a = flat(before), b = flat(after);
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  return keys.filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k])).map((k) => [k, a[k], b[k]]);
}

function prettyKey(k) {
  const last = k.split(".").pop();
  if (k.startsWith("strategy_risk_pct.")) return `Risk · ${last}`;
  if (k.startsWith("params.") || k.startsWith("effective_params.")) return paramInfo(last).label;
  return last.replace(/_/g, " ");
}
const show = (v) => (v === undefined ? "—" : typeof v === "boolean" ? (v ? "on" : "off") : Array.isArray(v) ? v.join(", ") : v === null ? "—" : String(v));

function revertPlan(e) {
  const b = e.before, cfg = store.config;
  switch (e.action) {
    case "risk.update": case "risk.reset": {
      if (!b) return null;
      const tags = new Set((cfg?.slots || []).map((s) => s.tag));
      const body = Object.fromEntries(Object.entries(b).filter(([k]) => cfg?.risk_limits?.[k]));
      body.strategy_risk_pct = Object.fromEntries(Object.entries(b.strategy_risk_pct || {}).filter(([t]) => tags.has(t)));
      return () => api("/api/config/risk", { method: "POST", body });
    }
    case "engine.update": case "engine.reset": return b ? () => api("/api/config/engine", { method: "POST", body: b }) : null;
    case "slot.update": return b ? () => api("/api/strategy/sub/" + encodeURIComponent(b.tag), { method: "POST", body: { name: b.name, reset_params: true, params: b.params || {}, enabled: b.enabled, risk_pct: b.risk_pct } }) : null;
    case "market.add": return e.target ? () => api("/api/markets/" + encodeURIComponent(e.target), { method: "DELETE" }) : null;
    case "market.remove": return b ? () => api("/api/markets", { method: "POST", body: { symbol: e.target, strategy: b.name, params: b.params || {}, risk_pct: b.risk_pct, tag: b.tag } }) : null;
    case "entries.pause": return () => api("/api/entries/resume", { method: "POST" });
    case "entries.resume": return () => api("/api/entries/pause", { method: "POST" });
    case "execution.auto": return isNum(b) || typeof b === "boolean" ? () => api("/api/auto/" + (b ? "true" : "false"), { method: "POST" }) : null;
    case "bot.stop": return () => api("/api/bot/start", { method: "POST" });
    default: return null;
  }
}

function AuditRow({ e, onReverted }) {
  const [open, setOpen] = useState(false);
  const [lab, icon] = label(e);
  const rows = e.before !== undefined || e.after !== undefined ? diffRows(e.before, e.after) : [];
  const plan = revertPlan(e);
  const revert = async () => {
    if (!(await confirmDialog({ title: "Undo this change?", confirm: "Undo", body: `Restores the state from before “${lab}” (${timeAgo(e.ts)}). The undo itself is recorded too.` }))) return;
    const ok = await act(plan, { ok: "Change undone", fail: "Couldn't undo" });
    if (ok) onReverted?.();
  };
  return html`<div class="feed-row" style="grid-template-columns:28px minmax(0,1fr) auto">
    <div class=${cx("f-icon", e.actor === "jarvis" ? "entry" : "")}><${Icon} name=${icon} size=${14} /></div>
    <div style="min-width:0">
      <div class="f-text"><b>${lab}</b>${e.target ? html` <span class="muted">· ${e.target}</span>` : ""}</div>
      <div class="f-meta">${fmtTime(e.ts, { seconds: true })} · by ${e.actor}${rows.length ? ` · ${rows.length} field${rows.length > 1 ? "s" : ""}` : ""}${e.detail && !e.action.startsWith("jarvis.") ? " · " + e.detail : ""}</div>
      ${open && rows.length > 0 && html`<div class="diff" style="margin-top:8px">${rows.slice(0, 40).map(([k, a, b]) => html`<div class="diff-row"><span class="muted ellipsis" title=${k}>${prettyKey(k)}</span><span class="from num ellipsis">${show(a)}</span><${Icon} name="arrowRight" size=${13} class="faint" /><span class="to num ellipsis">${show(b)}</span></div>`)}</div>`}
      ${open && e.action.startsWith("jarvis.") && e.detail && html`<pre class="mono faint" style="margin:8px 0 0;white-space:pre-wrap;font-size:11.5px">${e.detail}</pre>`}
    </div>
    <div class="row" style="gap:4px">
      ${(rows.length > 0 || e.action.startsWith("jarvis.")) && html`<${Button} size="sm" variant="ghost" onClick=${() => setOpen(!open)}>${open ? "Hide" : "Details"}</${Button}>`}
      ${plan && html`<${Button} size="sm" icon="undo" onClick=${revert}>Undo</${Button}>`}
    </div>
  </div>`;
}

export default function Activity() {
  const st = useStore();
  const [tab, setTab] = useState("events");
  const [filter, setFilter] = useState("");
  const [audit, setAudit] = useState(null);
  const [err, setErr] = useState("");
  const loadAudit = () => api("/api/audit?limit=300").then((r) => { setAudit(r.entries || []); setErr(""); }).catch((e) => setErr(e.message));
  useEffect(() => { loadAudit(); const t = setInterval(loadAudit, 15000); return () => clearInterval(t); }, []);
  const events = st.events.length ? st.events : st.snap?.feed || [];
  const FILTERS = { "": null, trades: (f) => ["entry", "exit"].includes(f.event), issues: (f) => ["reject", "error", "bracket", "risk", "reconcile"].includes(f.event), engine: (f) => ["mode", "bot", "strategy", "account"].includes(f.event) };
  return html`<div class="stack">
    <div class="page-head"><div><h2>Activity</h2><p>What the engine is doing, and every change anyone (or Jarvis) has made — with one-click undo.</p></div>
      ${tab === "audit" && html`<${Button} icon="refresh" onClick=${loadAudit}>Refresh</${Button}>`}</div>
    <${Tabs} value=${tab} onChange=${setTab} tabs=${[{ value: "events", label: "Live events", count: events.length }, { value: "audit", label: "Change history", count: audit?.length }]} />
    ${tab === "events" ? html`<${Card} title="Engine events" sub="This session, newest first" flush
        actions=${html`<${Seg} size="sm" value=${filter} onChange=${setFilter} options=${[{ value: "", label: "All" }, { value: "trades", label: "Trades" }, { value: "issues", label: "Issues" }, { value: "engine", label: "Engine" }]} />`}>
        <${FeedList} events=${events} limit=${300} filter=${FILTERS[filter]} /></${Card}>`
      : html`<${Card} title="Change history" sub="Settings, controls and orders — dashboard and Jarvis" flush>
        ${err ? html`<div class="card-body muted">${err}</div>` : audit === null ? html`<div class="card-body"><div class="skeleton" style="height:200px"></div></div>`
          : audit.length ? html`<div class="feed">${audit.map((e) => html`<${AuditRow} key=${e.ts + e.action + e.target} e=${e} onReverted=${loadAudit} />`)}</div>`
          : html`<${Empty} icon="history" title="No changes recorded yet" body="Every setting change and control action from now on is logged here." />`}
      </${Card}>`}
  </div>`;
}
