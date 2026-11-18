// Global live state: one websocket for the 1 s snapshot, plus on-demand REST
// data (config, strategy catalog, full trade ledger). Components subscribe via
// useStore(); every frame re-renders subscribers (cheap at this size).
import { useEffect, useState } from "./preact.js";
import { api, prefs, debounce } from "./util.js";

export const store = {
  snap: null,
  config: null,
  catalog: null,
  trades: null,
  tradesCount: -1,
  ws: "connecting",        // connecting | live | reconnecting | offline
  lastFrame: 0,
  frameGap: null,
  events: [],              // session event history (newest first), for activity + bell
  unread: 0,
  modals: [],
  toasts: [],
  route: { path: location.pathname, params: {} },
};

const listeners = new Set();
let scheduled = false;
export function setStore(patch) {
  Object.assign(store, patch);
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => { scheduled = false; listeners.forEach((fn) => fn()); });
}

export function useStore() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((v) => (v + 1) % 1e9);
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  return store;
}

// ── toasts / modals (rendered by the app shell) ──
let toastId = 0;
export function toast(title, { body = "", tone = "info", icon, timeout = 4200 } = {}) {
  const id = ++toastId;
  setStore({ toasts: [...store.toasts, { id, title, body, tone, icon }].slice(-4) });
  if (timeout) setTimeout(() => dismissToast(id), timeout);
  return id;
}
export function dismissToast(id) { setStore({ toasts: store.toasts.filter((t) => t.id !== id) }); }
export function toastError(e, title = "Something went wrong") {
  toast(title, { body: e?.message || String(e), tone: "down", icon: "alert", timeout: 7000 });
}

let modalId = 0;
/** render(close) → vnode. Returns a close function. */
export function openModal(render, opts = {}) {
  const id = ++modalId;
  const close = () => setStore({ modals: store.modals.filter((m) => m.id !== id) });
  setStore({ modals: [...store.modals, { id, render, close, kind: opts.kind || "modal", size: opts.size, dismissable: opts.dismissable !== false }] });
  return close;
}
export function closeTopModal() {
  const top = store.modals[store.modals.length - 1];
  if (top && top.dismissable) top.close();
}

// ── websocket ──
let sock = null;
let retry = 0;
let pollTimer = null;
let seenFeed = null;

function wsUrl() {
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws?lite=1";
}

export function connect() {
  try { sock?.close(); } catch { /* ignore */ }
  setStore({ ws: retry ? "reconnecting" : "connecting" });
  sock = new WebSocket(wsUrl());
  sock.onmessage = (ev) => {
    let snap;
    try { snap = JSON.parse(ev.data); } catch { return; }
    retry = 0;
    stopPolling();
    ingest(snap);
  };
  sock.onclose = (ev) => {
    if (ev.code === 4401) { location.href = "/login"; return; }
    retry += 1;
    setStore({ ws: retry > 3 ? "offline" : "reconnecting" });
    if (retry > 2) startPolling();
    setTimeout(connect, Math.min(10000, 800 * 2 ** Math.min(retry, 4)));
  };
  sock.onerror = () => { try { sock.close(); } catch { /* ignore */ } };
}

function startPolling() {
  if (pollTimer) return;
  const tick = async () => {
    try { ingest(await api("/api/state?lite=true"), true); } catch { /* keep trying */ }
  };
  tick();
  pollTimer = setInterval(tick, 4000);
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

function ingest(snap, polled = false) {
  const now = Date.now();
  const patch = { snap, ws: polled ? "offline" : "live", frameGap: store.lastFrame ? now - store.lastFrame : null, lastFrame: now };
  // trade ledger: refetch whenever the server's count changes
  if (snap.trade_count !== store.tradesCount) {
    patch.tradesCount = snap.trade_count;
    refreshTrades();
  }
  processFeed(snap.feed || [], patch);
  setStore(patch);
  if (!store.config) refreshConfig();
}

// ── feed → notifications ──
const NOTIFY_ICON = { entry: "zap", exit: "check", reject: "alert", risk: "shield", error: "alert", bracket: "alert", strategy: "nodes", mode: "settings", bot: "power", pending: "clock", account: "wallet", reconcile: "refresh" };
function feedKey(f) { return `${f.t}|${f.event}|${f.detail}`; }
function processFeed(feed, patch) {
  if (!seenFeed) {
    seenFeed = new Set(feed.map(feedKey));
    patch.events = feed.map((f) => ({ ...f, key: feedKey(f) }));
    return;
  }
  const fresh = feed.filter((f) => !seenFeed.has(feedKey(f))).reverse();
  if (!fresh.length) return;
  const p = prefs.get();
  for (const f of fresh) {
    seenFeed.add(feedKey(f));
    const ev = { ...f, key: feedKey(f) };
    patch.events = [ev, ...(patch.events || store.events)].slice(0, 300);
    const important = ["entry", "exit", "error", "bracket", "reject", "risk", "pending"].includes(f.event);
    if (important) patch.unread = (patch.unread ?? store.unread) + 1;
    const pnl = /PnL ([+-][\d.]+)/.exec(f.detail || "");
    const toneFor = f.event === "exit" ? (pnl && Number(pnl[1]) < 0 ? "down" : "up")
      : f.event === "entry" ? "accent" : ["error", "bracket"].includes(f.event) ? "down" : ["reject", "risk", "pending"].includes(f.event) ? "warn" : "info";
    if (p.toasts && important) toast(eventTitle(f.event), { body: f.detail, tone: toneFor, icon: NOTIFY_ICON[f.event] || "info" });
    if (p.sound && ["entry", "exit", "error", "bracket"].includes(f.event)) chime(toneFor);
    if (p.notify && (p.notifyEvents || []).includes(f.event)) systemNotify(eventTitle(f.event), f.detail);
  }
}
export function eventTitle(e) {
  return ({ entry: "Position opened", exit: "Position closed", reject: "Entry rejected", risk: "Risk", error: "Error",
    bracket: "Bracket warning", strategy: "Strategy changed", mode: "Engine", bot: "Bot", pending: "Signal awaiting confirmation",
    account: "Account", reconcile: "Reconciled" })[e] || (e ? e[0].toUpperCase() + e.slice(1) : "Event");
}
export function eventIcon(e) { return NOTIFY_ICON[e] || "info"; }

let audioCtx = null;
export function chime(kind = "up") {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const notes = kind === "down" ? [523.25, 392] : kind === "accent" ? [659.25, 880] : [587.33, 783.99];
    notes.forEach((freq, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = freq;
      const t0 = audioCtx.currentTime + i * 0.12;
      g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.09, t0 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.32);
      o.connect(g).connect(audioCtx.destination); o.start(t0); o.stop(t0 + 0.35);
    });
  } catch { /* audio unavailable */ }
}

async function systemNotify(title, body) {
  try {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (document.visibilityState === "visible" && document.hasFocus()) return;
    const reg = await navigator.serviceWorker?.getRegistration?.();
    if (reg) reg.showNotification("TradeOS · " + title, { body, icon: "/static/v2/icon.svg", badge: "/static/v2/icon.svg", tag: title + body });
    else new Notification("TradeOS · " + title, { body, icon: "/static/v2/icon.svg" });
  } catch { /* ignore */ }
}

// ── REST refreshers ──
export async function refreshConfig() {
  try { setStore({ config: await api("/api/config") }); } catch { /* shown via page states */ }
}
export async function refreshCatalog() {
  try { setStore({ catalog: (await api("/api/strategies")).strategies }); } catch { setStore({ catalog: [] }); }
}
export const refreshTrades = debounce(async () => {
  try { setStore({ trades: (await api("/api/trades")).trades }); } catch { /* keep last */ }
}, 250);

/** Run a mutating API call with a busy flag, a toast and a config refresh. */
export async function act(fn, { ok, okBody, fail = "Action failed", refresh = true } = {}) {
  try {
    const out = await fn();
    if (ok) toast(ok, { body: okBody || "", tone: "up", icon: "check" });
    if (refresh) refreshConfig();
    return out ?? true;
  } catch (e) {
    toastError(e, fail);
    return null;
  }
}

// ── derived helpers ──
export function slotFor(symbol, s = store.snap) {
  const slots = s?.slots || [];
  return slots.find((x) => x.symbols?.length === 1 && x.symbols[0] === symbol) || slots.find((x) => x.symbols?.includes(symbol)) || null;
}

export function openPositions(s = store.snap) {
  if (!s?.markets) return [];
  return Object.values(s.markets).filter((m) => m.position).map((m) => ({ symbol: m.symbol, price: m.price, ...m.position }));
}

export function unrealizedTotal(s = store.snap) {
  return openPositions(s).reduce((a, p) => a + (Number(p.unrealized) || 0), 0);
}
