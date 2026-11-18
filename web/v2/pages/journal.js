import { html, useState, useMemo } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Seg, Select, Pnl, Empty, SideChip, DrawerFrame, CoinIcon, cx } from "../lib/ui.js";
import { useStore, openModal } from "../lib/store.js";
import { fmtUsd, fmtPrice, fmtDur, fmtTime, fmtNum, isNum, tone, strategyLabel, download, toCSV, parseTs } from "../lib/util.js";

const REASON = { SL: ["Stop-loss", "down"], TP: ["Take-profit", "up"], manual: ["Manual", ""], strategy: ["Strategy exit", "info"], backtest: ["Backtest end", ""] };

export function summarize(trades) {
  const n = trades.length;
  if (!n) return { n: 0 };
  const wins = trades.filter((t) => Number(t.pnl) >= 0);
  const losses = trades.filter((t) => Number(t.pnl) < 0);
  const gw = wins.reduce((a, t) => a + Number(t.pnl), 0);
  const gl = -losses.reduce((a, t) => a + Number(t.pnl), 0);
  const rs = trades.map((t) => t.r).filter(isNum).map(Number);
  const holds = trades.map((t) => t.duration_s).filter(isNum).map(Number);
  const pnls = trades.map((t) => Number(t.pnl) || 0);
  return {
    n, wins: wins.length, losses: losses.length, winRate: (wins.length / n) * 100,
    net: pnls.reduce((a, b) => a + b, 0), pf: gl ? gw / gl : null, avgR: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
    avgWin: wins.length ? gw / wins.length : null, avgLoss: losses.length ? -gl / losses.length : null,
    hold: holds.length ? holds.reduce((a, b) => a + b, 0) / holds.length : null, best: Math.max(...pnls), worst: Math.min(...pnls),
    expectancy: pnls.reduce((a, b) => a + b, 0) / n,
  };
}

function TradeDetail({ t, close }) {
  const [label, tn] = REASON[t.reason] || [t.reason, ""];
  return html`<${DrawerFrame} title=${html`<span class="row" style="gap:10px"><${CoinIcon} symbol=${t.symbol} size="sm" />${t.symbol} · ${t.side.toUpperCase()}</span>`}
    sub=${`${fmtTime(t.entry_ts)} → ${fmtTime(t.exit_ts)}`} close=${close}>
    <div class="stack">
      <div class="grid g2 keep-sm">
        <div class="card pad"><div class="label">P&L</div><div style="font-size:24px;font-weight:740"><${Pnl} value=${t.pnl} /></div><div class="faint" style="font-size:12.5px">${isNum(t.pnl_pct) ? (t.pnl_pct > 0 ? "+" : "") + t.pnl_pct + "% on notional" : ""}</div></div>
        <div class="card pad"><div class="label">R multiple</div><div class=${cx("num", tone(t.r))} style="font-size:24px;font-weight:740">${isNum(t.r) ? (t.r > 0 ? "+" : "") + t.r + "R" : "—"}</div><div class="faint" style="font-size:12.5px">held ${fmtDur(t.duration_s)}</div></div>
      </div>
      <dl class="kv">
        <dt>Strategy</dt><dd>${strategyLabel(t.strategy)} <span class="faint">(${t.strategy})</span></dd>
        <dt>Side · size</dt><dd>${t.side} · ${t.size} contracts</dd>
        <dt>Entry</dt><dd>${fmtPrice(t.entry)}</dd>
        <dt>Exit</dt><dd>${fmtPrice(t.exit)}</dd>
        <dt>Stop-loss</dt><dd class="down">${fmtPrice(t.sl)}</dd>
        <dt>Take-profit</dt><dd class="up">${fmtPrice(t.tp)}</dd>
        <dt>Exit reason</dt><dd><${Badge} tone=${tn}>${label}</${Badge}></dd>
      </dl>
      ${t.entry_note && html`<div class="stack-sm"><div class="label">Why it entered</div><div class="card pad" style="font-size:13px;box-shadow:none;background:var(--surface-2)">${t.entry_note}</div></div>`}
      ${t.exit_note && html`<div class="stack-sm"><div class="label">Why it exited</div><div class="card pad" style="font-size:13px;box-shadow:none;background:var(--surface-2)">${t.exit_note}</div></div>`}
      <div class="faint" style="font-size:12px">P&L is the bot's ledger value: price move × contracts, before exchange fees and funding.</div>
    </div>
  </${DrawerFrame}>`;
}

const COLS = [
  { key: "exit_ts", label: "Closed", sort: (t) => parseTs(t.exit_ts) || 0 },
  { key: "symbol", label: "Market", sort: (t) => t.symbol },
  { key: "strategy", label: "Strategy", sort: (t) => t.strategy },
  { key: "side", label: "Side", sort: (t) => t.side },
  { key: "entry", label: "Entry", r: true, sort: (t) => Number(t.entry) },
  { key: "exit", label: "Exit", r: true, sort: (t) => Number(t.exit) },
  { key: "size", label: "Size", r: true, sort: (t) => Number(t.size) },
  { key: "pnl", label: "P&L", r: true, sort: (t) => Number(t.pnl) },
  { key: "r", label: "R", r: true, sort: (t) => (isNum(t.r) ? Number(t.r) : -1e9) },
  { key: "duration_s", label: "Held", r: true, sort: (t) => Number(t.duration_s) || 0 },
  { key: "reason", label: "Exit", sort: (t) => t.reason },
];

export default function Journal({ search }) {
  const st = useStore();
  const all = st.trades;
  const qs = new URLSearchParams(search || "");
  const [range, setRange] = useState("all");
  const [sym, setSym] = useState(qs.get("symbol") || "");
  const [strat, setStrat] = useState("");
  const [side, setSide] = useState("");
  const [outcome, setOutcome] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState({ key: "exit_ts", dir: -1 });
  const [limit, setLimit] = useState(100);

  const symbols = useMemo(() => [...new Set((all || []).map((t) => t.symbol))].sort(), [all]);
  const strategies = useMemo(() => [...new Set((all || []).map((t) => t.strategy))].sort(), [all]);
  const rows = useMemo(() => {
    let list = all || [];
    if (range !== "all") { const cut = Date.now() - Number(range) * 86400000; list = list.filter((t) => (parseTs(t.exit_ts) || 0) >= cut); }
    if (sym) list = list.filter((t) => t.symbol === sym);
    if (strat) list = list.filter((t) => t.strategy === strat);
    if (side) list = list.filter((t) => t.side === side);
    if (outcome) list = list.filter((t) => (outcome === "win" ? Number(t.pnl) >= 0 : Number(t.pnl) < 0));
    if (q) { const needle = q.toLowerCase(); list = list.filter((t) => `${t.symbol} ${t.strategy} ${t.entry_note || ""} ${t.exit_note || ""} ${t.reason}`.toLowerCase().includes(needle)); }
    const col = COLS.find((c) => c.key === sort.key) || COLS[0];
    return [...list].sort((a, b) => { const x = col.sort(a), y = col.sort(b); return (x > y ? 1 : x < y ? -1 : 0) * sort.dir; });
  }, [all, range, sym, strat, side, outcome, q, sort]);
  const sum = summarize(rows);

  const exportCsv = () => download(`tradeos-trades-${new Date().toISOString().slice(0, 10)}.csv`, toCSV(rows, [
    { label: "entry_ts", value: "entry_ts" }, { label: "exit_ts", value: "exit_ts" }, { label: "symbol", value: "symbol" }, { label: "strategy", value: "strategy" },
    { label: "side", value: "side" }, { label: "size", value: "size" }, { label: "entry", value: "entry" }, { label: "exit", value: "exit" }, { label: "sl", value: "sl" },
    { label: "tp", value: "tp" }, { label: "pnl", value: "pnl" }, { label: "pnl_pct", value: "pnl_pct" }, { label: "r", value: "r" }, { label: "duration_s", value: "duration_s" },
    { label: "exit_reason", value: "reason" }, { label: "entry_note", value: "entry_note" }, { label: "exit_note", value: "exit_note" },
  ]), "text/csv");
  const open = (t) => openModal((close) => html`<div class="drawer" role="dialog"><${TradeDetail} t=${t} close=${close} /></div>`, { kind: "drawer" });
  const clear = () => { setRange("all"); setSym(""); setStrat(""); setSide(""); setOutcome(""); setQ(""); };
  const filtered = range !== "all" || sym || strat || side || outcome || q;

  return html`<div class="stack">
    <div class="page-head"><div><h2>Trade journal</h2><p>Every closed trade with why it entered, why it exited, and how it did.</p></div>
      <${Button} icon="download" onClick=${exportCsv} disabled=${!rows.length}>Export CSV</${Button}></div>
    <div class="card">
      <div class="card-body filters">
        <${Seg} size="sm" value=${range} onChange=${setRange} options=${[{ value: "1", label: "24h" }, { value: "7", label: "7D" }, { value: "30", label: "30D" }, { value: "90", label: "90D" }, { value: "all", label: "All" }]} />
        <div style="width:150px"><${Select} size="sm" value=${sym} onChange=${setSym} options=${[{ value: "", label: "All markets" }, ...symbols.map((x) => ({ value: x, label: x }))]} /></div>
        <div style="width:180px"><${Select} size="sm" value=${strat} onChange=${setStrat} options=${[{ value: "", label: "All strategies" }, ...strategies.map((x) => ({ value: x, label: x }))]} /></div>
        <${Seg} size="sm" value=${side} onChange=${setSide} options=${[{ value: "", label: "Both" }, { value: "long", label: "Long" }, { value: "short", label: "Short" }]} />
        <${Seg} size="sm" value=${outcome} onChange=${setOutcome} options=${[{ value: "", label: "All" }, { value: "win", label: "Wins" }, { value: "loss", label: "Losses" }]} />
        <div class="input-group grow" style="min-width:160px"><span class="prefix"><${Icon} name="search" size=${14} /></span>
          <input class="input sm has-prefix" placeholder="Search notes…" value=${q} onInput=${(e) => setQ(e.currentTarget.value)} /></div>
        ${filtered && html`<${Button} size="sm" variant="ghost" icon="x" onClick=${clear}>Clear</${Button}>`}
      </div>
      ${sum.n > 0 && html`<div class="summary-strip" style="border-top:1px solid var(--border)">
        <span>Trades<b>${sum.n}</b></span><span>Win rate<b>${sum.winRate.toFixed(1)}%</b></span>
        <span>Net<b class=${tone(sum.net)}>${fmtUsd(sum.net, { sign: true })}</b></span><span>Profit factor<b>${isNum(sum.pf) ? sum.pf.toFixed(2) : "—"}</b></span>
        <span>Avg R<b class=${tone(sum.avgR)}>${isNum(sum.avgR) ? sum.avgR.toFixed(2) : "—"}</b></span><span>Expectancy<b class=${tone(sum.expectancy)}>${fmtUsd(sum.expectancy, { sign: true })}</b></span>
        <span>Avg hold<b>${fmtDur(sum.hold)}</b></span><span>Best / worst<b><span class="up">${fmtUsd(sum.best, { sign: true })}</span> / <span class="down">${fmtUsd(sum.worst, { sign: true })}</span></b></span>
      </div>`}
      ${all === null ? html`<div class="card-body"><div class="skeleton" style="height:240px"></div></div>`
        : !rows.length ? html`<${Empty} icon="book" title=${all.length ? "No trades match these filters" : "No closed trades yet"} body=${all.length ? "Try widening the date range or clearing filters." : "Closed trades are recorded here automatically."} />`
        : html`<div class="table-wrap" style="max-height:calc(100vh - 330px)"><table class="t">
          <thead><tr>${COLS.map((c) => html`<th class=${cx("sortable", c.r && "r")} onClick=${() => setSort({ key: c.key, dir: sort.key === c.key ? -sort.dir : -1 })}>
            ${c.label}${sort.key === c.key ? (sort.dir < 0 ? " ↓" : " ↑") : ""}</th>`)}</tr></thead>
          <tbody>${rows.slice(0, limit).map((t) => {
            const [label, tn] = REASON[t.reason] || [t.reason, ""];
            return html`<tr class="clickable" onClick=${() => open(t)}>
              <td class="muted">${fmtTime(t.exit_ts)}</td>
              <td><div class="row" style="gap:8px"><${CoinIcon} symbol=${t.symbol} size="sm" /><b>${t.symbol}</b></div></td>
              <td class="muted ellipsis" style="max-width:150px" title=${t.strategy}>${t.strategy}</td>
              <td><${SideChip} side=${t.side} /></td>
              <td class="r num">${fmtPrice(t.entry)}</td><td class="r num">${fmtPrice(t.exit)}</td><td class="r num">${t.size}</td>
              <td class="r"><${Pnl} value=${t.pnl} /></td>
              <td class=${cx("r num", tone(t.r))}>${isNum(t.r) ? (t.r > 0 ? "+" : "") + t.r : "—"}</td>
              <td class="r muted">${fmtDur(t.duration_s)}</td>
              <td><${Badge} tone=${tn}>${label}</${Badge}></td></tr>`;
          })}</tbody></table></div>
          ${rows.length > limit && html`<div class="card-foot"><span class="muted grow">Showing ${limit} of ${rows.length}</span><${Button} size="sm" onClick=${() => setLimit(limit + 200)}>Show more</${Button}></div>`}`}
    </div>
  </div>`;
}
