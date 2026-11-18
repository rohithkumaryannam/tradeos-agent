// Formatting, API access, preferences and small helpers shared by every page.

const MINUS = "−";

export function isNum(n) {
  return n !== null && n !== undefined && n !== "" && Number.isFinite(Number(n));
}

export function fmtNum(n, d = 2) {
  if (!isNum(n)) return "—";
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** Price with decimals scaled to magnitude, so 0.05924 and 64,606.5 both read right. */
export function fmtPrice(n) {
  if (!isNum(n)) return "—";
  const v = Math.abs(Number(n));
  let d = 2;
  if (v >= 10000) d = 1;
  else if (v >= 100) d = 2;
  else if (v >= 1) d = 3;
  else if (v >= 0.1) d = 4;
  else if (v >= 0.01) d = 5;
  else if (v > 0) d = Math.min(8, 2 - Math.floor(Math.log10(v)) + 2);
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmtUsd(n, { sign = false, d = 2, compact = false } = {}) {
  if (!isNum(n)) return "—";
  const v = Number(n);
  const abs = Math.abs(v);
  const body = compact && abs >= 10000
    ? "$" + Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(abs)
    : "$" + abs.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  if (v < 0) return MINUS + body;
  return (sign && v > 0 ? "+" : "") + body;
}

export function fmtSigned(n, d = 2) {
  if (!isNum(n)) return "—";
  const v = Number(n);
  return (v > 0 ? "+" : v < 0 ? MINUS : "") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmtPct(n, d = 2, sign = true) {
  if (!isNum(n)) return "—";
  const v = Number(n);
  return (v > 0 && sign ? "+" : v < 0 ? MINUS : "") + Math.abs(v).toFixed(d) + "%";
}

export function fmtCompact(n) {
  if (!isNum(n)) return "—";
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(Number(n));
}

export function tone(n) {
  if (!isNum(n) || Number(n) === 0) return "";
  return Number(n) > 0 ? "up" : "down";
}

export function parseTs(ts) {
  if (ts === null || ts === undefined || ts === "") return null;
  if (typeof ts === "number") return ts > 1e12 ? ts : ts * 1000;
  const t = Date.parse(ts);
  return Number.isFinite(t) ? t : null;
}

export function timeAgo(ts, now = Date.now()) {
  const t = parseTs(ts);
  if (t === null) return "—";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

export function fmtTime(ts, { date = true, seconds = false, utc = prefs.get().utc } = {}) {
  const t = parseTs(ts);
  if (t === null) return "—";
  const opts = { hour: "2-digit", minute: "2-digit", hour12: false };
  if (seconds) opts.second = "2-digit";
  if (date) { opts.month = "short"; opts.day = "numeric"; }
  if (utc) opts.timeZone = "UTC";
  return new Date(t).toLocaleString(undefined, opts) + (utc ? " UTC" : "");
}

export function fmtDur(seconds) {
  if (!isNum(seconds)) return "—";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h " + (m % 60) + "m";
  const d = Math.floor(h / 24);
  return d + "d " + (h % 24) + "h";
}

export function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function download(filename, content, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function toCSV(rows, columns) {
  const cell = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  return [columns.map((c) => cell(c.label)).join(","),
    ...rows.map((r) => columns.map((c) => cell(typeof c.value === "function" ? c.value(r) : r[c.value])).join(","))].join("\n");
}

// ── API ──
export class ApiError extends Error {}

export async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError("Can't reach the TradeOS server — check your connection.");
  }
  if (res.status === 401) { location.href = "/login"; throw new ApiError("Signed out"); }
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || res.statusText || "Request failed";
    const err = new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

// ── preferences (per browser) ──
const PREF_KEY = "tradeos.v2.prefs";
const PREF_DEFAULTS = {
  theme: "dark", density: "comfortable", accent: 250, utc: false, sound: true, toasts: true,
  notify: false, notifyEvents: ["entry", "exit", "error", "bracket", "risk"], name: "Rohith",
  collapsed: false, chartTf: "15m", indicators: { ema9: true, ema20: true, ema200: false, volume: true },
  favorites: [], journalCols: null, speak: true,
};
let prefCache = null;
const prefListeners = new Set();
export const prefs = {
  get() {
    if (!prefCache) {
      try { prefCache = { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem(PREF_KEY) || "{}") }; }
      catch { prefCache = { ...PREF_DEFAULTS }; }
    }
    return prefCache;
  },
  set(patch) {
    prefCache = { ...prefs.get(), ...patch };
    try { localStorage.setItem(PREF_KEY, JSON.stringify(prefCache)); } catch { /* private mode */ }
    applyAppearance();
    prefListeners.forEach((fn) => fn(prefCache));
  },
  subscribe(fn) { prefListeners.add(fn); return () => prefListeners.delete(fn); },
};

export function applyAppearance() {
  const p = prefs.get();
  let theme = p.theme;
  if (theme === "system") theme = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  const root = document.documentElement;
  root.dataset.theme = theme;
  if (p.density === "compact") root.dataset.density = "compact"; else delete root.dataset.density;
  root.style.setProperty("--accent-h", p.accent);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#f4f5f8" : "#08090c");
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ── coins + labels ──
const COINS = {
  BTC: { glyph: "₿", color: "#f7931a", name: "Bitcoin" },
  ETH: { glyph: "Ξ", color: "#627eea", name: "Ethereum" },
  XRP: { glyph: "✕", color: "#3b4a5c", name: "XRP" },
  DOGE: { glyph: "Ð", color: "#c2a633", name: "Dogecoin" },
  ADA: { glyph: "₳", color: "#2a5ada", name: "Cardano" },
  SOL: { glyph: "◎", color: "linear-gradient(135deg,#9945ff,#14f195)", name: "Solana" },
  LTC: { glyph: "Ł", color: "#345d9d", name: "Litecoin" },
  BNB: { glyph: "B", color: "#e8b30b", name: "BNB" },
  AVAX: { glyph: "A", color: "#e84142", name: "Avalanche" },
  DOT: { glyph: "●", color: "#e6007a", name: "Polkadot" },
  LINK: { glyph: "⬡", color: "#2a5ada", name: "Chainlink" },
  MATIC: { glyph: "M", color: "#8247e5", name: "Polygon" },
  SHIB: { glyph: "S", color: "#e2431e", name: "Shiba Inu" },
  TRX: { glyph: "T", color: "#ef0027", name: "TRON" },
  SUI: { glyph: "S", color: "#4da2ff", name: "Sui" },
  PEPE: { glyph: "P", color: "#4c9540", name: "Pepe" },
};

export function baseOf(symbol) {
  return String(symbol || "").replace(/^1000/, "").replace(/(USDT|USD|INR)$/, "") || String(symbol || "?");
}

export function coinMeta(symbol) {
  const base = baseOf(symbol);
  const known = COINS[base];
  if (known) return { base, ...known };
  let h = 0;
  for (const c of base) h = (h * 31 + c.charCodeAt(0)) % 360;
  return { base, glyph: base.slice(0, 1), color: `hsl(${h} 55% 45%)`, name: base };
}

const STRATEGY_LABELS = {
  donchian_breakout: "Donchian Breakout", mean_reversion: "Mean Reversion", btc_bms: "BTC BMS",
  darkknight_sol: "DarkKnight SOL", time_series_momentum: "Time-Series Momentum", ema_crossover: "EMA Crossover",
  mastermind: "Mastermind", paused: "Paused", supertrend: "Supertrend", bb_squeeze_breakout: "BB Squeeze Breakout",
  mean_reversion_scalp: "Mean Reversion Scalp", ny_4h_orb: "NY 4H Opening Range", range_fakeout_reversal: "Range Fakeout Reversal",
  rsi2_connors: "RSI-2 Connors", btc_trend_pullback: "Trend Pullback", combo: "Combo", manual: "Manual",
};
export function strategyLabel(name) {
  if (!name) return "—";
  return STRATEGY_LABELS[name] || String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const PARAM_INFO = {
  fallback_tp_mult: ["Take-profit", "× stop distance", "Live, the exchange take-profit sits this many stop-distances away. It is the real reward:risk."],
  entry_period: ["Entry channel", "bars", "Breakout of the highest high / lowest low of this many closed candles."],
  exit_period: ["Exit channel", "bars", "Trailing channel exit — used in research backtests; live, the exchange bracket owns the exit."],
  volume_mult: ["Volume filter", "× average", "Breakout candle volume must exceed this multiple of its 20-bar average."],
  atr_period: ["ATR period", "bars", "Lookback for the Average True Range used to size the stop."],
  atr_stop_mult: ["Stop distance", "× ATR", "Stop-loss sits this many ATRs from entry."],
  use_adx_filter: ["ADX trend filter", "", "Only trade breakouts when ADX shows a trending market."],
  adx_period: ["ADX period", "bars", ""],
  adx_threshold: ["ADX threshold", "", "Minimum ADX for an entry when the trend filter is on."],
  resolution: ["Timeframe", "", "Candle timeframe this strategy reads."],
  rr: ["Reward : risk", "R", ""],
  risk_reward: ["Reward : risk", "R", ""],
  tp_r: ["Take-profit", "R", ""],
};
export function paramInfo(key) {
  const known = PARAM_INFO[key];
  if (known) return { label: known[0], unit: known[1], hint: known[2] };
  return { label: String(key).replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()), unit: "", hint: "" };
}

export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

export function uid() { return Math.random().toString(36).slice(2, 10); }

export function sum(arr, f = (x) => x) { return arr.reduce((a, b) => a + (Number(f(b)) || 0), 0); }

export function groupBy(arr, key) {
  const out = {};
  for (const x of arr) { const k = typeof key === "function" ? key(x) : x[key]; (out[k] = out[k] || []).push(x); }
  return out;
}

export function sameValue(a, b) {
  if (typeof a === "number" || typeof b === "number") return Number(a) === Number(b);
  return JSON.stringify(a) === JSON.stringify(b);
}
