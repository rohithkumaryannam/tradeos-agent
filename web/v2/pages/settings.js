import { html, useState, useEffect } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Seg, Toggle, NumberInput, Select, Callout, confirmDialog, cx } from "../lib/ui.js";
import { useStore, act, toast, chime, refreshConfig } from "../lib/store.js";
import { api, prefs, sameValue, fmtTime, fmtDur, download, toCSV, TIMEFRAMES } from "../lib/util.js";
import { controls, modeLabel } from "../app.js";

const SECTIONS = [["general", "General", "sliders"], ["engine", "Engine", "server"], ["notify", "Notifications", "bell"], ["look", "Appearance", "sun"], ["data", "Data & export", "download"], ["security", "Security", "lock"], ["about", "About", "info"]];
const ACCENTS = [[250, "Violet"], [222, "Blue"], [192, "Cyan"], [165, "Teal"], [142, "Green"], [32, "Amber"], [345, "Rose"]];
const EVENTS = [["entry", "Entries"], ["exit", "Exits"], ["error", "Errors"], ["bracket", "Bracket warnings"], ["risk", "Risk events"], ["reject", "Rejected entries"]];

function Row({ title, desc, children, source }) {
  return html`<div class="set-row"><div><div class="sr-title">${title}${source && html`<span class="src-tag">${source}</span>`}</div>${desc && html`<div class="sr-desc">${desc}</div>`}</div><div class="sr-ctl">${children}</div></div>`;
}

function usePrefs() {
  const [p, setP] = useState(prefs.get());
  useEffect(() => prefs.subscribe(setP), []);
  return p;
}

function Engine() {
  const st = useStore();
  const cfg = st.config;
  const [d, setD] = useState({});
  const [busy, setBusy] = useState(false);
  if (!cfg) return html`<div class="skeleton" style="height:240px"></div>`;
  const e = cfg.engine, f = cfg.engine_config, lim = cfg.engine_limits;
  const v = (k) => (k in d ? d[k] : e[k]);
  const dirty = Object.keys(d).filter((k) => !sameValue(d[k], e[k]));
  const save = async () => {
    const body = {};
    for (const k of dirty) body[k] = k === "resolution" ? d[k] : k === "ai_narrate" ? !!d[k] : Number(d[k]);
    setBusy(true);
    const ok = await act(() => api("/api/config/engine", { method: "POST", body }), { ok: "Engine settings saved", fail: "Couldn't save engine settings" });
    setBusy(false);
    if (ok) setD({});
  };
  const reset = async () => {
    if (!(await confirmDialog({ title: "Reset engine settings to config.yaml?", confirm: "Reset" }))) return;
    await act(() => api("/api/config/engine/reset", { method: "POST" }), { ok: "Engine reset to config.yaml" });
    setD({});
  };
  const src = (k) => (!sameValue(e[k], f[k]) ? `file: ${f[k]}` : null);
  return html`<${Card} title="Engine" sub="Saved on the server — applies immediately and survives restarts" flush
      foot=${html`<${Button} variant="ghost" icon="undo" onClick=${reset}>Reset to config.yaml</${Button}><span class="grow"></span>
        ${dirty.length > 0 && html`<${Button} variant="ghost" onClick=${() => setD({})}>Discard</${Button}>`}<${Button} variant="primary" loading=${busy} disabled=${!dirty.length} onClick=${save}>Save${dirty.length ? ` (${dirty.length})` : ""}</${Button}>`}>
    <${Row} title="Loop interval" desc="Seconds between evaluation cycles. Lower reacts faster but uses more of Delta's API quota." source=${src("poll_seconds")}>
      <div style="width:150px"><${NumberInput} value=${v("poll_seconds")} min=${lim.poll_seconds[0]} max=${lim.poll_seconds[1]} step="1" suffix="sec" changed=${"poll_seconds" in d && !sameValue(d.poll_seconds, e.poll_seconds)} onInput=${(x) => setD({ ...d, poll_seconds: x })} /></div></${Row}>
    <${Row} title="Candle lookback" desc="Candles fetched per market each cycle. Raised automatically if a strategy needs more." source=${src("lookback")}>
      <div style="width:150px"><${NumberInput} value=${v("lookback")} min=${lim.lookback[0]} max=${lim.lookback[1]} step="10" suffix="bars" changed=${"lookback" in d && !sameValue(d.lookback, e.lookback)} onInput=${(x) => setD({ ...d, lookback: x })} /></div></${Row}>
    <${Row} title="Base timeframe" desc="The shared candle feed used for position management. Strategies on other timeframes fetch their own candles." source=${src("resolution")}>
      <div style="width:150px"><${Select} value=${v("resolution")} onChange=${(x) => setD({ ...d, resolution: x })} options=${cfg.timeframes || TIMEFRAMES} /></div></${Row}>
    <${Row} title="AI trade narration" desc="Adds a one-line plain-English “why” to the feed on every entry and exit." source=${src("ai_narrate")}>
      <${Toggle} checked=${v("ai_narrate")} onChange=${(x) => setD({ ...d, ai_narrate: x })} /></${Row}>
  </${Card}>`;
}

function Notify() {
  const st = useStore();
  const p = usePrefs();
  const [perm, setPerm] = useState(typeof Notification !== "undefined" ? Notification.permission : "unsupported");
  const alerts = st.config?.alerts || {};
  const ask = async () => {
    if (typeof Notification === "undefined") return;
    const r = await Notification.requestPermission();
    setPerm(r);
    if (r === "granted") { prefs.set({ notify: true }); toast("Browser notifications on", { tone: "up", icon: "bell" }); }
  };
  const evs = new Set(p.notifyEvents || []);
  return html`<div class="stack">
    <${Card} title="In this browser" sub="Stored on this device only" flush>
      <${Row} title="Pop-up toasts" desc="Entries, exits, rejections and errors appear in the corner as they happen."><${Toggle} checked=${p.toasts} onChange=${(x) => prefs.set({ toasts: x })} /></${Row}>
      <${Row} title="Trade sounds" desc="A soft chime on entries and exits, a lower tone on losses and errors.">
        <${Button} size="sm" variant="ghost" icon="volume" onClick=${() => chime("up")}>Test</${Button}><${Toggle} checked=${p.sound} onChange=${(x) => prefs.set({ sound: x })} /></${Row}>
      <${Row} title="System notifications" desc=${perm === "denied" ? "Blocked in your browser settings — allow notifications for this site to enable." : perm === "unsupported" ? "Not supported in this browser." : "Get alerts even when TradeOS is in the background (install it as an app on your phone for the best experience)."}>
        ${perm === "granted" ? html`<${Toggle} checked=${p.notify} onChange=${(x) => prefs.set({ notify: x })} />` : html`<${Button} size="sm" onClick=${ask} disabled=${perm !== "default"}>Enable</${Button}>`}</${Row}>
      <${Row} title="Notify me about" desc="Applies to system notifications.">
        <div class="chips" style="justify-content:flex-end">${EVENTS.map(([k, l]) => html`<button class=${cx("chip", evs.has(k) && "changed")} style="cursor:pointer" onClick=${() => { const n = new Set(evs); n.has(k) ? n.delete(k) : n.add(k); prefs.set({ notifyEvents: [...n] }); }}>${evs.has(k) ? "✓ " : ""}${l}</button>`)}</div></${Row}>
    </${Card}>
    <${Card} title="Off-device alerts" sub="Sent by the server even when no browser is open" flush>
      <${Row} title="Channels" desc="Configured in the server's .env: TRADEOS_ALERT_WEBHOOK_URL, TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, or SMTP settings.">
        <div class="chips" style="justify-content:flex-end">${[["webhook", "Webhook"], ["telegram", "Telegram"], ["email", "Email"]].map(([k, l]) => html`<${Badge} tone=${alerts[k] ? "up" : ""} dot>${l}</${Badge}>`)}</div></${Row}>
      <${Row} title="Test" desc="Sends a test message to every configured channel."><${Button} size="sm" icon="send" disabled=${!alerts.configured} onClick=${() => act(() => api("/api/alerts/test", { method: "POST" }), { ok: "Test alert sent", refresh: false })}>Send test alert</${Button}></${Row}>
    </${Card}>
  </div>`;
}

function Look() {
  const p = usePrefs();
  return html`<${Card} title="Appearance" sub="Stored on this device" flush>
    <${Row} title="Theme"><${Seg} value=${p.theme} onChange=${(x) => prefs.set({ theme: x })} options=${[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }, { value: "system", label: "System" }]} /></${Row}>
    <${Row} title="Accent colour"><div class="swatches">${ACCENTS.map(([h, n]) => html`<button title=${n} class=${p.accent === h && "on"} style=${{ background: `hsl(${h} 90% 64%)` }} onClick=${() => prefs.set({ accent: h })} aria-label=${n}></button>`)}</div></${Row}>
    <${Row} title="Density" desc="Compact fits more on screen."><${Seg} value=${p.density} onChange=${(x) => prefs.set({ density: x })} options=${[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} /></${Row}>
    <${Row} title="Times" desc="How timestamps and chart axes are shown."><${Seg} value=${p.utc ? "utc" : "local"} onChange=${(x) => prefs.set({ utc: x === "utc" })} options=${[{ value: "local", label: "Local time" }, { value: "utc", label: "UTC" }]} /></${Row}>
    <${Row} title="Collapsed sidebar" desc="Icons only, on wide screens."><${Toggle} checked=${p.collapsed} onChange=${(x) => prefs.set({ collapsed: x })} /></${Row}>
  </${Card}>`;
}

export default function Settings() {
  const st = useStore();
  const s = st.snap;
  const cfg = st.config;
  const p = usePrefs();
  const [sec, setSec] = useState("general");
  useEffect(() => { refreshConfig(); }, []);
  const exportTrades = () => download(`tradeos-trades-${new Date().toISOString().slice(0, 10)}.csv`, toCSV(st.trades || [],
    ["entry_ts", "exit_ts", "symbol", "strategy", "side", "size", "entry", "exit", "sl", "tp", "pnl", "pnl_pct", "r", "duration_s", "reason", "entry_note", "exit_note"].map((k) => ({ label: k, value: k }))), "text/csv");
  return html`<div class="stack">
    <div class="page-head"><div><h2>Settings</h2><p>Everything about how TradeOS trades and looks. Strategy and risk settings live on their own pages.</p></div></div>
    <div class="settings">
      <nav class="settings-nav">${SECTIONS.map(([k, l, ic]) => html`<button class=${sec === k && "on"} onClick=${() => setSec(k)}><span class="row" style="gap:10px"><${Icon} name=${ic} size=${16} />${l}</span></button>`)}</nav>
      <div class="stack" style="min-width:0">
        ${sec === "general" && html`<${Card} title="Trading" sub="Live controls for the engine" flush>
          <${Row} title="Bot" desc=${s?.running ? "Running — evaluating every market each cycle." : "Stopped — no new signals and no local protection."}>
            ${s?.running ? html`<${Button} variant="danger" icon="stop" onClick=${controls.stop}>Stop bot</${Button}>` : html`<${Button} variant="success solid" icon="play" onClick=${controls.start}>Start bot</${Button}>`}</${Row}>
          <${Row} title="Venue" desc="Paper uses simulated fills; Delta Demo places real orders on your Delta testnet account. Requires flat positions.">
            <${Seg} value=${s?.mode} onChange=${(m) => m !== s?.mode && controls.setMode(m)} options=${[{ value: "PAPER", label: "Paper" }, { value: "DELTA_DEMO", label: "Delta Demo", disabled: cfg && !cfg.credentials?.delta, title: cfg && !cfg.credentials?.delta ? "Add DELTA_API_KEY / SECRET to .env" : "" }]} /></${Row}>
          <${Row} title="Execution" desc="Auto executes signals immediately; Manual holds each signal for you to confirm on the Trade page.">
            <${Seg} value=${s?.auto ? "auto" : "manual"} onChange=${(x) => controls.setAuto(x === "auto")} options=${[{ value: "auto", label: "Auto" }, { value: "manual", label: "Manual confirm" }]} /></${Row}>
          <${Row} title="New entries" desc="Pausing stops new trades; open positions keep their stop-loss and take-profit.">
            <${Toggle} tone="up" checked=${s ? !s.entries_paused : false} onChange=${(x) => (x ? controls.resume() : controls.pause())} label=${s?.entries_paused ? "Paused" : "Allowed"} /></${Row}>
          <${Row} title="Your name" desc="Shown in the greeting on this device."><input class="input" style="width:200px" value=${p.name} onInput=${(e) => prefs.set({ name: e.currentTarget.value })} /></${Row}>
        </${Card}>`}
        ${sec === "engine" && html`<${Engine} />`}
        ${sec === "notify" && html`<${Notify} />`}
        ${sec === "look" && html`<${Look} />`}
        ${sec === "data" && html`<${Card} title="Data & export" flush>
          <${Row} title="Configuration" desc="The live configuration — strategies, per-coin settings, risk and engine — as a config.yaml you can keep or commit."><a class="btn" href="/api/config/export"><${Icon} name="download" size=${16} />Download YAML</a></${Row}>
          <${Row} title="Trade history" desc=${`${(st.trades || []).length} closed trades as CSV.`}><${Button} icon="download" onClick=${exportTrades} disabled=${!(st.trades || []).length}>Download CSV</${Button}></${Row}>
          <${Row} title="Where settings live" desc="Dashboard changes are saved on the server (MongoDB when configured, plus a local file) and win over config.yaml until a deploy bumps the config revision."><${Badge} tone="accent">revision ${cfg?.config_revision ?? "—"}</${Badge}></${Row}>
        </${Card}>`}
        ${sec === "security" && html`<${Card} title="Security" flush>
          <${Row} title="Session" desc="Signs this browser out of the dashboard."><a class="btn danger" href="/logout"><${Icon} name="logout" size=${16} />Sign out</a></${Row}>
          <${Row} title="Secrets" desc="API keys and tokens stay in the server's .env. The dashboard can see whether they're set, never their values.">
            <div class="chips" style="justify-content:flex-end">${[["delta", "Delta keys"], ["mongo", "MongoDB"], ["auth", "Login token"]].map(([k, l]) => html`<${Badge} tone=${cfg?.credentials?.[k] ? "up" : "warn"} dot>${l}</${Badge}>`)}</div></${Row}>
          ${cfg && !cfg.credentials?.auth && html`<div class="card-body"><${Callout} tone="warn" title="No login token set">Anyone who can reach this server can control the bot. Set TRADEOS_TOKEN in .env.</${Callout}></div>`}
        </${Card}>`}
        ${sec === "about" && html`<${Card} title="About TradeOS" flush>
          <${Row} title="Version"><span class="mono">${cfg?.version || s?.server?.version || "—"}</span></${Row}>
          <${Row} title="Server started"><span>${s?.server?.started_at ? fmtTime(s.server.started_at * 1000) + " · up " + fmtDur(Date.now() / 1000 - s.server.started_at) : "—"}</span></${Row}>
          <${Row} title="Classic dashboard" desc="The previous interface, still available."><a class="btn" href="/classic">Open</a></${Row}>
          <${Row} title="Shortcuts" desc="⌘K command palette · ⌘J Jarvis · g + key to jump between pages · ? for help."><${Badge}>Keyboard</${Badge}></${Row}>
          <${Row} title="Charts" desc="Charting by TradingView Lightweight Charts™."><a class="btn ghost" href="https://www.tradingview.com/" target="_blank" rel="noopener">tradingview.com</a></${Row}>
        </${Card}>`}
      </div>
    </div>
  </div>`;
}
