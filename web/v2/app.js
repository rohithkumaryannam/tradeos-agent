// TradeOS v2 — application shell: routing, layout, command palette,
// shortcuts, global banners, kill switch, notifications and Jarvis.
import { html, render, useState, useEffect, useRef, useMemo } from "./lib/preact.js";
import { Icon, Logo } from "./lib/icons.js";
import { Button, Badge, Popover, Kbd, ModalFrame, DrawerFrame, confirmDialog, Toggle, Pnl, cx, Ago } from "./lib/ui.js";
import {
  store, setStore, useStore, connect, refreshCatalog, refreshConfig, toast, toastError, dismissToast,
  openModal, closeTopModal, act, eventTitle, eventIcon, openPositions, unrealizedTotal,
} from "./lib/store.js";
import { api, prefs, applyAppearance, fmtUsd, fmtPrice, strategyLabel, timeAgo, coinMeta } from "./lib/util.js";

window.__tradeosBooted = true;

// ── routes ──
export const ROUTES = [
  { path: "/", page: "overview", title: "Overview", icon: "home", group: "Trading", key: "o" },
  { path: "/trade", page: "trade", title: "Trade", icon: "candles", group: "Trading", key: "t" },
  { path: "/positions", page: "positions", title: "Positions", icon: "layers", group: "Trading", key: "p" },
  { path: "/strategies", page: "strategies", title: "Strategies", icon: "nodes", group: "Control", key: "s" },
  { path: "/risk", page: "risk", title: "Risk", icon: "shield", group: "Control", key: "r" },
  { path: "/backtest", page: "backtest", title: "Backtest Lab", icon: "flask", group: "Control", key: "b" },
  { path: "/journal", page: "journal", title: "Journal", icon: "book", group: "Insights", key: "j" },
  { path: "/analytics", page: "analytics", title: "Analytics", icon: "pie", group: "Insights", key: "a" },
  { path: "/ai", page: "jarvis", title: "Jarvis", icon: "sparkles", group: "Insights", key: "i" },
  { path: "/activity", page: "activity", title: "Activity", icon: "activity", group: "System", key: "e" },
  { path: "/system", page: "system", title: "System", icon: "server", group: "System", key: "h" },
  { path: "/settings", page: "settings", title: "Settings", icon: "settings", group: "System", key: "," },
];
const ALIASES = { "/dashboard": "/", "/overview": "/", "/markets": "/trade", "/orders": "/positions", "/trades": "/journal",
  "/jarvis": "/ai", "/feed": "/activity", "/health": "/system", "/bots": "/" };

function resolve(pathname) {
  let path = pathname.replace(/\/+$/, "") || "/";
  path = ALIASES[path] || path;
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "trade" && parts[1]) return { route: ROUTES[1], params: { symbol: decodeURIComponent(parts[1]).toUpperCase() }, path };
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  return { route, params: {}, path: route.path };
}

export function navigate(to, { replace = false } = {}) {
  const url = new URL(to, location.origin);
  if (replace) history.replaceState({}, "", url.pathname + url.search);
  else if (url.pathname + url.search !== location.pathname + location.search) history.pushState({}, "", url.pathname + url.search);
  setStore({ route: { path: url.pathname, search: url.search } });
  window.scrollTo({ top: 0 });
}
window.addEventListener("popstate", () => { setStore({ route: { path: location.pathname, search: location.search } }); window.scrollTo({ top: 0 }); });

export function Link({ to, children, class: cls, ...rest }) {
  return html`<a href=${to} class=${cls} onClick=${(e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault(); navigate(to);
  }} ...${rest}>${children}</a>`;
}

// ── lazy pages ──
const pageCache = {};
function usePage(name) {
  const [, setV] = useState(0);
  useEffect(() => {
    if (pageCache[name]) return;
    import(`./pages/${name}.js`).then((m) => { pageCache[name] = m.default; setV((x) => x + 1); })
      .catch((e) => { pageCache[name] = () => html`<div class="card pad"><b>Couldn't load this page.</b><div class="muted">${String(e)}</div></div>`; setV((x) => x + 1); });
  }, [name]);
  return pageCache[name];
}

// ── engine state helpers ──
export function engineState(s) {
  if (!s) return { tone: "", label: "Connecting", icon: "radio" };
  if (!s.running) return { tone: "down", label: "Stopped", icon: "stop" };
  if (s.execution_enabled === false) return { tone: "warn", label: "Standby", icon: "lock" };
  if (s.entries_paused) return { tone: "warn", label: "Entries paused", icon: "pause" };
  return { tone: "up", label: "Live", icon: "play" };
}
export const modeLabel = (m) => (m === "DELTA_DEMO" ? "Delta Demo" : m === "PAPER" ? "Paper" : m || "—");

// ── controls shared by the top bar, palette and pages ──
export const controls = {
  start: () => act(() => api("/api/bot/start", { method: "POST" }), { ok: "Bot started", fail: "Couldn't start the bot" }),
  async stop() {
    if (!(await confirmDialog({ title: "Stop the bot?", tone: "danger", confirm: "Stop bot",
      body: "Stopping halts the trading loop. Exchange-side stop-loss/take-profit orders stay on Delta, but local protection and new signals stop until you start it again." }))) return;
    return act(() => api("/api/bot/stop", { method: "POST" }), { ok: "Bot stopped" });
  },
  pause: () => act(() => api("/api/entries/pause", { method: "POST" }), { ok: "New entries paused", okBody: "Open positions are still managed." }),
  resume: () => act(() => api("/api/entries/resume", { method: "POST" }), { ok: "New entries resumed" }),
  evaluate: () => act(() => api("/api/evaluate", { method: "POST" }), { ok: "Ran one evaluation cycle", refresh: false }),
  async setAuto(flag) {
    return act(() => api("/api/auto/" + (flag ? "true" : "false"), { method: "POST" }), { ok: flag ? "Auto-execution on" : "Manual confirmation on" });
  },
  async setMode(mode) {
    if (!(await confirmDialog({ title: `Switch to ${modeLabel(mode)}?`, tone: mode === "DELTA_DEMO" ? "danger" : "", confirm: "Switch venue",
      body: mode === "DELTA_DEMO" ? "Orders will be placed on your Delta testnet account. All positions must be flat to switch." : "Trading moves to simulated fills. All positions must be flat to switch." }))) return;
    return act(() => api("/api/mode/" + mode, { method: "POST" }), { ok: `Venue: ${modeLabel(mode)}` });
  },
  async closePosition(symbol) {
    const pos = store.snap?.markets?.[symbol]?.position;
    if (!(await confirmDialog({ title: `Close ${symbol}?`, tone: "danger", confirm: "Close at market",
      body: pos ? `Market-close the ${pos.side.toUpperCase()} ${pos.size} position (unrealized ${fmtUsd(pos.unrealized, { sign: true })}).` : "Market-close this position." }))) return;
    const r = await act(() => api("/api/position/close?symbol=" + encodeURIComponent(symbol), { method: "POST" }), { refresh: false });
    if (r?.result) toast(r.result.event === "exit" ? `${symbol} closed` : `${symbol}: ${r.result.event}`, { body: r.result.detail, tone: r.result.event === "exit" ? "up" : "warn" });
  },
  killSwitch: () => openModal((close) => html`<${KillSwitch} close=${close} />`, { size: "sm" }),
};

function KillSwitch({ close }) {
  const s = useStore().snap;
  const n = openPositions(s).length;
  const [busy, setBusy] = useState("");
  const run = async (kind) => {
    if (kind !== "pause") {
      const ok = await confirmDialog({ title: kind === "kill" ? "Activate kill switch?" : "Close every position?", tone: "danger",
        confirm: kind === "kill" ? "Pause + close all" : "Close all", typeToConfirm: n ? "CLOSE" : null,
        body: `${n} open position${n === 1 ? "" : "s"} will be market-closed${kind === "kill" ? " and new entries paused" : ""}.` });
      if (!ok) return;
    }
    setBusy(kind);
    if (kind === "pause") await controls.pause();
    else {
      const r = await act(() => api("/api/positions/close-all", { method: "POST", body: { pause_entries: kind === "kill" } }), { refresh: false });
      if (r) toast(kind === "kill" ? "Kill switch activated" : "Positions closed", { body: Object.keys(r.results || {}).join(", ") || "Nothing was open", tone: "warn", icon: "octagon" });
    }
    setBusy(""); close();
  };
  return html`<${ModalFrame} title="Emergency controls" sub="Fast ways to cut risk. Exchange brackets stay in place unless a position is closed." close=${close} icon="octagon" iconTone="off">
    <div class="stack-sm">
      <button class="card hover clickable pad row" style="text-align:left" onClick=${() => run("pause")} disabled=${!!busy}>
        <div class="engine-orb paused" style="width:36px;height:36px;border-radius:11px"><${Icon} name="pause" size=${17} /></div>
        <div class="grow"><b>Pause new entries</b><div class="muted" style="font-size:12.5px">Open positions keep their stop-loss / take-profit.</div></div>
        ${busy === "pause" && html`<span class="spinner"></span>`}
      </button>
      <button class="card hover clickable pad row" style="text-align:left" onClick=${() => run("close")} disabled=${!!busy || !n}>
        <div class="engine-orb off" style="width:36px;height:36px;border-radius:11px"><${Icon} name="x" size=${17} /></div>
        <div class="grow"><b>Close all positions</b><div class="muted" style="font-size:12.5px">${n ? `Market-close ${n} open position${n === 1 ? "" : "s"}.` : "Nothing is open right now."}</div></div>
        ${busy === "close" && html`<span class="spinner"></span>`}
      </button>
      <button class="card hover clickable pad row" style="text-align:left;border-color:var(--down-line)" onClick=${() => run("kill")} disabled=${!!busy}>
        <div class="engine-orb off" style="width:36px;height:36px;border-radius:11px"><${Icon} name="octagon" size=${17} /></div>
        <div class="grow"><b class="down">Kill switch</b><div class="muted" style="font-size:12.5px">Pause entries and close everything.</div></div>
        ${busy === "kill" && html`<span class="spinner"></span>`}
      </button>
    </div>
  </${ModalFrame}>`;
}

// ── top bar pieces ──
function EnginePill() {
  const s = useStore().snap;
  const st = engineState(s);
  return html`<${Popover} width="300px" trigger=${({ toggle }) => html`
    <button class=${cx("pill", st.tone)} onClick=${toggle} title="Engine controls">
      <span class=${cx("dot", st.tone === "up" && "pulse")}></span>${st.label}
      <span class="sep hide-sm"></span><span class="hide-sm" style="color:var(--text-2)">${modeLabel(s?.mode)} · ${s?.auto ? "Auto" : "Manual"}</span>
      <${Icon} name="chevronDown" size=${14} />
    </button>`}>
    ${(close) => html`<div class="p-head"><div class="label">Trading engine</div>
        <div class="row" style="margin-top:8px"><b>${st.label}</b><span class="faint" style="margin-left:auto;font-size:12px">cycle ${s?.health?.last_cycle ? timeAgo(s.health.last_cycle) : "—"}</span></div></div>
      <div class="p-sep"></div>
      ${s?.running
        ? html`<button class="p-item danger" onClick=${() => { close(); controls.stop(); }}><${Icon} name="stop" size=${16} />Stop bot</button>`
        : html`<button class="p-item" onClick=${() => { close(); controls.start(); }}><${Icon} name="play" size=${16} class="up" />Start bot</button>`}
      ${s?.entries_paused
        ? html`<button class="p-item" onClick=${() => { close(); controls.resume(); }}><${Icon} name="play" size=${16} />Resume new entries</button>`
        : html`<button class="p-item" onClick=${() => { close(); controls.pause(); }}><${Icon} name="pause" size=${16} />Pause new entries</button>`}
      <button class="p-item" onClick=${() => { close(); controls.evaluate(); }}><${Icon} name="refresh" size=${16} />Run one cycle now</button>
      <div class="p-sep"></div>
      <div class="p-head row"><span class="grow">Auto-execute signals</span><${Toggle} checked=${s?.auto} onChange=${(v) => controls.setAuto(v)} /></div>
      <div class="p-head row"><span class="grow">Venue</span>
        <div class="seg sm"><button class=${s?.mode === "PAPER" && "on"} onClick=${() => { close(); s?.mode !== "PAPER" && controls.setMode("PAPER"); }}>Paper</button>
        <button class=${s?.mode === "DELTA_DEMO" && "on"} onClick=${() => { close(); s?.mode !== "DELTA_DEMO" && controls.setMode("DELTA_DEMO"); }}>Delta Demo</button></div></div>
      ${s?.execution_enabled === false && html`<div class="p-head faint" style="font-size:12px">${s.execution_reason}</div>`}`}
  </${Popover}>`;
}

function Bell() {
  const st = useStore();
  const events = st.events.slice(0, 14);
  return html`<${Popover} width="380px" trigger=${({ toggle }) => html`
    <button class="btn ghost icon-only anchor" onClick=${() => { toggle(); setStore({ unread: 0 }); }} title="Notifications" aria-label="Notifications">
      <${Icon} name="bell" size=${18} />
      ${st.unread > 0 && html`<span style="position:absolute;top:5px;right:5px;min-width:16px;height:16px;padding:0 4px;border-radius:9px;background:var(--down);color:#fff;font-size:10px;font-weight:700;display:grid;place-items:center">${Math.min(99, st.unread)}</span>`}
    </button>`}>
    ${(close) => html`<div class="p-head row"><b class="grow">Activity</b><a href="/activity" onClick=${(e) => { e.preventDefault(); close(); navigate("/activity"); }}>View all</a></div>
      <div style="max-height:420px;overflow-y:auto">
        ${events.length ? events.map((f) => html`<div class="feed-row" style="padding:9px 10px">
          <div class=${cx("f-icon", f.event, f.event === "exit" && /PnL -/.test(f.detail) && "loss")}><${Icon} name=${eventIcon(f.event)} size=${14} /></div>
          <div><div class="f-text" style="font-size:12.5px">${f.detail}</div><div class="f-meta">${eventTitle(f.event)} · ${timeAgo(f.t)}</div></div><span></span></div>`)
        : html`<div class="empty" style="padding:24px"><span>No activity yet</span></div>`}
      </div>`}
  </${Popover}>`;
}

function UserMenu() {
  const p = prefs.get();
  return html`<${Popover} width="240px" trigger=${({ toggle }) => html`
    <button class="btn ghost" style="padding:0 6px 0 4px;gap:8px" onClick=${toggle} aria-label="Account menu">
      <span style="width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:12px;color:#fff;background:linear-gradient(135deg,hsl(var(--accent-h) 85% 62%),#3fd6c3)">${(p.name || "T").slice(0, 1).toUpperCase()}</span>
      <${Icon} name="chevronDown" size=${14} class="hide-sm" />
    </button>`}>
    ${(close) => html`<div class="p-head"><b>${p.name || "Trader"}</b><div class="faint" style="font-size:12px">TradeOS workspace</div></div><div class="p-sep"></div>
      <button class="p-item" onClick=${() => { close(); navigate("/settings"); }}><${Icon} name="settings" size=${16} />Settings</button>
      <button class="p-item" onClick=${() => { close(); openShortcuts(); }}><${Icon} name="keyboard" size=${16} />Keyboard shortcuts</button>
      <a class="p-item" href="/classic"><${Icon} name="grid" size=${16} />Classic dashboard</a>
      <div class="p-sep"></div>
      <a class="p-item danger" href="/logout"><${Icon} name="logout" size=${16} />Sign out</a>`}
  </${Popover}>`;
}

function ThemeToggle() {
  const [, setV] = useState(0);
  useEffect(() => prefs.subscribe(() => setV((x) => x + 1)), []);
  const dark = document.documentElement.dataset.theme !== "light";
  return html`<button class="btn ghost icon-only" title=${dark ? "Light mode" : "Dark mode"} aria-label="Toggle theme"
    onClick=${() => prefs.set({ theme: dark ? "light" : "dark" })}><${Icon} name=${dark ? "sun" : "moon"} size=${18} /></button>`;
}

function ConnDot() {
  const st = useStore();
  if (st.ws === "live") return html`<span class="btn ghost icon-only hide-sm" style="cursor:default;color:var(--up)" data-tip="Live data · updates every second" data-tip-pos="bottom"><${Icon} name="wifi" size=${17} /></span>`;
  const label = st.ws === "offline" ? "Offline · polling" : "Reconnecting";
  return html`<span class=${cx("badge", st.ws === "offline" ? "down" : "warn")}><${Icon} name="wifiOff" size=${13} />${label}</span>`;
}

function Topbar({ route }) {
  const sym = route.params?.symbol;
  return html`<header class="topbar">
    <${Link} to="/" class="topbar-logo" aria-label="TradeOS home"><${Logo} size=${26} /></${Link}>
    <h1 class="ellipsis">${route.route.title}${sym ? html`<span class="crumb"> / ${sym}</span>` : ""}</h1>
    <div class="spacer"></div>
    <button class="search-btn hide-sm" onClick=${openPalette}><${Icon} name="search" size=${16} /><span class="sb-text">Search or run a command…</span><kbd>⌘K</kbd></button>
    <button class="btn ghost icon-only show-sm" onClick=${openPalette} aria-label="Search"><${Icon} name="search" size=${18} /></button>
    <button class="btn sm jarvis-btn hide-sm" onClick=${openJarvis} title="Ask Jarvis (⌘J)"><${Icon} name="sparkles" size=${15} />Jarvis</button>
    <${ConnDot} />
    <${EnginePill} />
    <button class="btn danger sm hide-sm" onClick=${controls.killSwitch} title="Emergency controls"><${Icon} name="octagon" size=${15} />Kill switch</button>
    <${Bell} />
    <span class="hide-sm"><${ThemeToggle} /></span>
    <${UserMenu} />
  </header>`;
}

function Sidebar({ route }) {
  const st = useStore();
  const s = st.snap;
  const p = prefs.get();
  const groups = [...new Set(ROUTES.map((r) => r.group))];
  const nOpen = openPositions(s).length;
  return html`<aside class="sidebar">
    <${Link} to="/" class="brand"><span class="mark"><${Logo} size=${30} /></span><span class="brand-text">TradeOS<small>${modeLabel(s?.mode)}</small></span></${Link}>
    <nav class="nav" aria-label="Main">
      ${groups.map((g) => html`<div class="nav-group"><span class="label">${g}</span>
        ${ROUTES.filter((r) => r.group === g).map((r) => html`<${Link} to=${r.path} class=${cx(route.route.path === r.path && "active")} title=${r.title}>
          <${Icon} name=${r.icon} size=${18} /><span>${r.title}</span>
          ${r.page === "positions" && nOpen > 0 && html`<span class="count live">${nOpen}</span>`}
          ${r.page === "activity" && st.unread > 0 && html`<span class="count">${st.unread}</span>`}
        </${Link}>`)}</div>`)}
    </nav>
    <div class="sidebar-foot">
      <div class="acct-card">
        <div class="row between"><span class="label">Equity</span><span class=${cx("badge", s?.mode === "DELTA_DEMO" ? "accent" : "")}>${modeLabel(s?.mode)}</span></div>
        <div class="big num">${s ? fmtUsd(s.equity) : "—"}</div>
        <div class="row between" style="font-size:12.5px"><span class="muted">Today</span><${Pnl} value=${s?.realized_today} /></div>
        <div class="row between" style="font-size:12.5px"><span class="muted">Open P&L</span><${Pnl} value=${s ? unrealizedTotal(s) : null} /></div>
      </div>
      <button class="btn ghost sm collapse-btn" onClick=${() => prefs.set({ collapsed: !p.collapsed })} title="Collapse sidebar"><${Icon} name="sidebar" size=${16} /></button>
    </div>
  </aside>`;
}

function TabBar({ route }) {
  const items = [ROUTES[0], ROUTES[1], ROUTES[2], ROUTES[3]];
  const moreActive = !items.some((r) => r.path === route.route.path);
  return html`<nav class="tabbar" aria-label="Main">
    ${items.map((r) => html`<${Link} to=${r.path} class=${cx(route.route.path === r.path && "active")}><${Icon} name=${r.icon} size=${21} />${r.title}</${Link}>`)}
    <button class=${cx(moreActive && "active")} onClick=${openMoreSheet}><${Icon} name="grid" size=${21} />More</button>
  </nav>`;
}

function openMoreSheet() {
  openModal((close) => html`<div class="sheet" role="dialog" aria-label="More">
    <div class="grabber"></div>
    <div class="sheet-grid">
      ${ROUTES.slice(4).map((r) => html`<a href=${r.path} onClick=${(e) => { e.preventDefault(); close(); navigate(r.path); }}><${Icon} name=${r.icon} size=${22} />${r.title}</a>`)}
      <a href="#" onClick=${(e) => { e.preventDefault(); close(); controls.killSwitch(); }} style="color:var(--down)"><${Icon} name="octagon" size=${22} />Kill switch</a>
      <a href="#" onClick=${(e) => { e.preventDefault(); close(); prefs.set({ theme: document.documentElement.dataset.theme === "light" ? "dark" : "light" }); }}><${Icon} name="sun" size=${22} />Theme</a>
      <a href="#" onClick=${(e) => { e.preventDefault(); close(); openPalette(); }}><${Icon} name="search" size=${22} />Search</a>
    </div></div>`, { kind: "sheet" });
}

// ── banners ──
function Banners() {
  const st = useStore();
  const s = st.snap;
  if (!s) return null;
  const out = [];
  if (s.mode === "DELTA_DEMO" && s.account_status === "unavailable" && s.account_error_code === "ip_not_whitelisted_for_api_key") {
    out.push(html`<div class="banner down"><${Icon} name="lock" size=${17} class="down" />
      <div class="grow"><b>Delta rejected this server's IP — new orders are blocked.</b> Delta Testnet → API Keys → edit the existing key → whitelist <b class="mono">${s.account_client_ip}</b>.</div>
      <${Button} size="sm" icon="copy" onClick=${() => { navigator.clipboard?.writeText(s.account_client_ip || ""); toast("IP copied", { tone: "up", icon: "check" }); }}>Copy IP</${Button}>
      <${Button} size="sm" icon="refresh" onClick=${() => act(() => api("/api/account/retry", { method: "POST" }), { ok: "Retrying Delta connection", refresh: false })}>Retry</${Button}></div>`);
  } else if (s.mode === "DELTA_DEMO" && s.account_status === "unavailable") {
    out.push(html`<div class="banner down"><${Icon} name="wifiOff" size=${17} class="down" /><div class="grow"><b>Delta account unavailable.</b> ${s.account_error || "Entries are blocked until the account responds."}</div>
      <${Button} size="sm" icon="refresh" onClick=${() => act(() => api("/api/account/retry", { method: "POST" }), { ok: "Retrying Delta connection", refresh: false })}>Retry</${Button}></div>`);
  }
  if (!s.running) out.push(html`<div class="banner down"><${Icon} name="stop" size=${17} class="down" /><div class="grow"><b>The bot is stopped.</b> No new signals, and local stop/target protection is off. Exchange brackets on Delta remain.</div><${Button} size="sm" variant="success solid" icon="play" onClick=${controls.start}>Start bot</${Button}></div>`);
  else if (s.entries_paused) out.push(html`<div class="banner warn"><${Icon} name="pause" size=${17} class="warn" /><div class="grow"><b>New entries are paused.</b> Open positions are still managed.</div><${Button} size="sm" icon="play" onClick=${controls.resume}>Resume</${Button}></div>`);
  if (s.execution_enabled === false) out.push(html`<div class="banner warn"><${Icon} name="lock" size=${17} class="warn" /><div class="grow"><b>Read-only standby.</b> ${s.execution_reason}</div></div>`);
  if (s.portfolio?.unprotected > 0) out.push(html`<div class="banner warn"><${Icon} name="alert" size=${17} class="warn" /><div class="grow"><b>${s.portfolio.unprotected} position${s.portfolio.unprotected > 1 ? "s" : ""} without a confirmed exchange bracket.</b> Protection is local and only works while the bot runs.</div><${Link} to="/positions" class="btn sm">Review</${Link}></div>`);
  if (s.daily_cap_ok === false) out.push(html`<div class="banner warn"><${Icon} name="shield" size=${17} class="warn" /><div class="grow"><b>Daily loss cap reached.</b> ${s.daily_cap_reason}</div></div>`);
  if (st.ws !== "live" && st.lastFrame) out.push(html`<div class="banner info"><span class="spinner"></span><div class="grow">Reconnecting to the trading engine… showing data from <${Ago} ts=${st.lastFrame} />.</div></div>`);
  return out.length ? html`<div class="stack-sm">${out}</div>` : null;
}

// ── command palette ──
function Palette({ close }) {
  const st = useStore();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  const s = st.snap;
  const items = useMemo(() => {
    const list = [];
    ROUTES.forEach((r) => list.push({ group: "Go to", label: r.title, icon: r.icon, hint: "g " + r.key, run: () => navigate(r.path) }));
    Object.keys(s?.markets || {}).forEach((sym) => {
      list.push({ group: "Markets", label: `Trade ${sym}`, icon: "candles", hint: fmtPrice(s.markets[sym].price), run: () => navigate("/trade/" + sym) });
      list.push({ group: "Markets", label: `Backtest ${sym}`, icon: "flask", run: () => navigate("/backtest?symbol=" + sym) });
      if (s.markets[sym].position) list.push({ group: "Positions", label: `Close ${sym} position`, icon: "x", tone: "down", run: () => controls.closePosition(sym) });
    });
    list.push({ group: "Actions", label: s?.running ? "Stop bot" : "Start bot", icon: s?.running ? "stop" : "play", run: s?.running ? controls.stop : controls.start });
    list.push({ group: "Actions", label: s?.entries_paused ? "Resume new entries" : "Pause new entries", icon: "pause", run: s?.entries_paused ? controls.resume : controls.pause });
    list.push({ group: "Actions", label: s?.auto ? "Switch to manual confirmation" : "Switch to auto-execute", icon: "zap", run: () => controls.setAuto(!s?.auto) });
    list.push({ group: "Actions", label: "Run one evaluation cycle", icon: "refresh", run: controls.evaluate });
    list.push({ group: "Actions", label: "Emergency controls / kill switch", icon: "octagon", run: controls.killSwitch });
    list.push({ group: "Actions", label: "Ask Jarvis…", icon: "sparkles", hint: "⌘J", run: () => openJarvis() });
    list.push({ group: "Actions", label: "Add a market", icon: "plus", run: () => navigate("/strategies?add=1") });
    list.push({ group: "Actions", label: "Export configuration (YAML)", icon: "download", run: () => { location.href = "/api/config/export"; } });
    list.push({ group: "Preferences", label: "Toggle light / dark theme", icon: "sun", run: () => prefs.set({ theme: document.documentElement.dataset.theme === "light" ? "dark" : "light" }) });
    list.push({ group: "Preferences", label: prefs.get().sound ? "Mute trade sounds" : "Enable trade sounds", icon: "volume", run: () => prefs.set({ sound: !prefs.get().sound }) });
    list.push({ group: "Preferences", label: "Keyboard shortcuts", icon: "keyboard", hint: "?", run: openShortcuts });
    list.push({ group: "Preferences", label: "Open classic dashboard", icon: "grid", run: () => { location.href = "/classic"; } });
    return list;
  }, [s?.running, s?.entries_paused, s?.auto, Object.keys(s?.markets || {}).join(), JSON.stringify(Object.values(s?.markets || {}).map((m) => !!m.position))]);
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = items.filter((it) => terms.every((t) => (it.label + " " + it.group).toLowerCase().includes(t))).slice(0, 40);
  const run = (it) => { close(); setTimeout(() => it.run(), 10); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((x) => Math.min(shown.length - 1, x + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((x) => Math.max(0, x - 1)); }
    else if (e.key === "Enter" && shown[sel]) { e.preventDefault(); run(shown[sel]); }
  };
  let lastGroup = null;
  return html`<div class="palette" role="dialog" aria-label="Command palette">
    <div class="p-input"><${Icon} name="search" size=${18} class="faint" />
      <input ref=${input} placeholder="Search pages, markets, actions…" value=${q} onInput=${(e) => { setQ(e.currentTarget.value); setSel(0); }} onKeyDown=${onKey} />
      <kbd>esc</kbd></div>
    <div class="p-list">
      ${shown.length ? shown.map((it, i) => {
        const head = it.group !== lastGroup ? html`<div class="p-group label">${it.group}</div>` : null;
        lastGroup = it.group;
        return html`${head}<div class=${cx("p-row", i === sel && "sel")} onMouseMove=${() => setSel(i)} onClick=${() => run(it)}>
          <${Icon} name=${it.icon} size=${17} class=${it.tone === "down" ? "down" : "faint"} /><span>${it.label}</span>${it.hint && html`<span class="p-hint">${it.hint}</span>`}</div>`;
      }) : html`<div class="empty" style="padding:28px"><span>No matches for “${q}”</span></div>`}
    </div>
    <div class="p-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> run</span><span><kbd>⌘</kbd><kbd>K</kbd> toggle</span></div>
  </div>`;
}
let paletteClose = null;
export function openPalette() {
  if (paletteClose) { paletteClose(); paletteClose = null; return; }
  const close = openModal((c) => html`<${Palette} close=${() => { paletteClose = null; c(); }} />`, { kind: "palette" });
  paletteClose = close;
}

function openShortcuts() {
  const rows = [["⌘ K  or  /", "Command palette"], ["⌘ J", "Ask Jarvis"], ["?", "This help"], ["Esc", "Close dialogs"],
    ...ROUTES.map((r) => [`g then ${r.key}`, `Go to ${r.title}`])];
  openModal((close) => html`<${ModalFrame} title="Keyboard shortcuts" close=${close}>
    <div class="kv" style="grid-template-columns:auto 1fr">${rows.map(([k, v]) => html`<dt><kbd>${k}</kbd></dt><dd style="text-align:left;font-weight:500">${v}</dd>`)}</div>
  </${ModalFrame}>`, { size: "sm" });
}

// ── Jarvis drawer ──
let JarvisPanel = null;
export function openJarvis() {
  if (store.route.path === "/ai") return;
  const show = () => openModal((close) => html`<div class="drawer wide" role="dialog" aria-label="Jarvis">
      <${JarvisPanel} panel close=${close} /></div>`, { kind: "drawer" });
  if (JarvisPanel) show();
  else import("./pages/jarvis.js").then((m) => { JarvisPanel = m.JarvisChat; show(); });
}

// ── app ──
function App() {
  const st = useStore();
  const [, setV] = useState(0);
  useEffect(() => prefs.subscribe(() => setV((x) => x + 1)), []);
  const route = resolve(st.route.path);
  const Page = usePage(route.route.page);
  const collapsed = prefs.get().collapsed;

  useEffect(() => {
    document.title = `${route.route.title}${route.params.symbol ? " · " + route.params.symbol : ""} · TradeOS`;
    if (route.path !== st.route.path && !route.params.symbol) history.replaceState({}, "", route.path + (st.route.search || ""));
  }, [route.route.path, route.params.symbol]);

  return html`<div class=${cx("shell", collapsed && "collapsed")}>
    <${Sidebar} route=${route} />
    <div class="main">
      <${Topbar} route=${route} />
      <main class="content" id="content">
        <${Banners} />
        ${Page ? html`<${Page} params=${route.params} search=${st.route.search || location.search} />` : html`<div class="stack"><div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:320px"></div></div>`}
      </main>
    </div>
    <${TabBar} route=${route} />
    ${route.route.page !== "jarvis" && html`<button class="fab" onClick=${openJarvis} title="Ask Jarvis (⌘J)" aria-label="Ask Jarvis"><${Icon} name="sparkles" size=${22} /></button>`}
    <${Overlays} />
  </div>`;
}

function Overlays() {
  const st = useStore();
  return html`
    ${st.modals.map((m, i) => html`<div key=${m.id}>
      ${i === st.modals.length - 1 ? html`<div class="overlay" onClick=${() => m.dismissable && m.close()}></div>` : null}
      ${m.kind === "drawer" ? m.render(m.close)
        : m.kind === "sheet" ? m.render(m.close)
        : m.kind === "palette" ? html`<div class="modal-wrap" onClick=${(e) => { if (e.target === e.currentTarget) m.close(); }}>${m.render(m.close)}</div>`
        : html`<div class="modal-wrap" onClick=${(e) => { if (e.target === e.currentTarget && m.dismissable) m.close(); }}>
            <div class=${cx("modal", m.size)} role="dialog" aria-modal="true">${m.render(m.close)}</div></div>`}
    </div>`)}
    <div class="toasts" aria-live="polite">
      ${st.toasts.map((t) => html`<div key=${t.id} class=${cx("toast", t.tone)}>
        <div class="t-icon"><${Icon} name=${t.icon || (t.tone === "down" ? "alert" : t.tone === "up" ? "check" : "info")} size=${15} /></div>
        <div class="t-body"><strong>${t.title}</strong>${t.body}</div>
        <button class="t-close" onClick=${() => dismissToast(t.id)} aria-label="Dismiss"><${Icon} name="x" size=${15} /></button></div>`)}
    </div>`;
}

// ── keyboard ──
let gPending = 0;
document.addEventListener("keydown", (e) => {
  const tag = (e.target?.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target?.isContentEditable;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") { e.preventDefault(); openJarvis(); return; }
  if (e.key === "Escape") { closeTopModal(); paletteClose = null; return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); openPalette(); return; }
  if (e.key === "?") { openShortcuts(); return; }
  if (e.key === "g") { gPending = Date.now(); return; }
  if (Date.now() - gPending < 1200) {
    const r = ROUTES.find((x) => x.key === e.key);
    gPending = 0;
    if (r) { e.preventDefault(); navigate(r.path); }
  }
});

// ── boot ──
applyAppearance();
matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => prefs.get().theme === "system" && applyAppearance());
render(html`<${App} />`, document.getElementById("app"));
connect();
refreshCatalog();
refreshConfig();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
