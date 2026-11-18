import { html, useState, useMemo } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Card, Stat, Seg, Select, Empty, Badge, cx } from "../lib/ui.js";
import { useStore } from "../lib/store.js";
import { fmtUsd, fmtNum, fmtDur, isNum, tone, parseTs, strategyLabel, groupBy } from "../lib/util.js";
import { AreaChart } from "../lib/charts.js";
import { cumulativePnl } from "../lib/widgets.js";
import { summarize } from "./journal.js";

function Bars({ items, fmt = (v) => fmtUsd(v, { sign: true }), labelWidth }) {
  const max = Math.max(1e-9, ...items.map((i) => Math.abs(i.value)));
  if (!items.length) return html`<div class="muted center" style="padding:24px 0">No data</div>`;
  return html`<div class="bars">${items.map((i) => html`<div class="bar-row" style=${labelWidth ? { gridTemplateColumns: `${labelWidth} minmax(0,1fr) 84px` } : null}>
    <span class="ellipsis" style="font-weight:600" title=${i.title || i.label}>${i.label}${i.sub ? html` <span class="faint" style="font-weight:500;font-size:11.5px">${i.sub}</span>` : ""}</span>
    <div class="b-track"><div class="b-mid"></div><div class=${cx("b-fill", i.value >= 0 ? "pos" : "neg")} style=${{ width: (Math.abs(i.value) / max) * 50 + "%" }}></div></div>
    <span class=${cx("num right", tone(i.value))} style="font-weight:650">${fmt(i.value)}</span></div>`)}</div>`;
}

function Histogram({ cols, height = 140 }) {
  const max = Math.max(1e-9, ...cols.map((c) => Math.abs(c.value)));
  return html`<div class="hist" style=${{ height: height + "px" }}>${cols.map((c) => html`<div class="h-col" title=${c.title}>
    <div class="h-bar" style=${{ height: (Math.abs(c.value) / max) * (height - 24) + "px", background: c.color || (c.value >= 0 ? "var(--up)" : "var(--down)"), opacity: c.dim ? 0.45 : 1 }}></div>
    <span class="h-lbl">${c.label}</span></div>`)}</div>`;
}

function Heatmap({ trades }) {
  const byDay = {};
  for (const t of trades) { const k = String(t.exit_ts || "").slice(0, 10); if (k) byDay[k] = (byDay[k] || 0) + (Number(t.pnl) || 0); }
  const max = Math.max(1e-9, ...Object.values(byDay).map(Math.abs));
  const today = new Date(); today.setUTCHours(0, 0, 0, 0);
  // From the first trading day (at least 8 weeks, at most ~1 year back).
  const first = Object.keys(byDay).sort()[0];
  const firstMs = first ? Date.parse(first + "T00:00:00Z") : today.getTime();
  const days = Math.min(371, Math.max(56, Math.round((today.getTime() - firstMs) / 86400000) + 7));
  const start = new Date(today.getTime() - (days - 1) * 86400000);
  start.setUTCDate(start.getUTCDate() - start.getUTCDay()); // align to Sunday
  const cells = [];
  for (let d = new Date(start); d <= today; d = new Date(d.getTime() + 86400000)) {
    const k = d.toISOString().slice(0, 10);
    const v = byDay[k];
    const a = isNum(v) ? 0.25 + 0.75 * Math.min(1, Math.abs(v) / max) : 0;
    const bg = !isNum(v) ? "var(--surface-3)" : v >= 0 ? `color-mix(in srgb, var(--up) ${Math.round(a * 100)}%, var(--surface-3))` : `color-mix(in srgb, var(--down) ${Math.round(a * 100)}%, var(--surface-3))`;
    cells.push(html`<i style=${{ background: bg }} title=${`${k}: ${isNum(v) ? fmtUsd(v, { sign: true }) : "no trades"}`}></i>`);
  }
  const active = Object.keys(byDay).length;
  const green = Object.values(byDay).filter((v) => v >= 0).length;
  return html`<div class="stack-sm"><div class="heat">${cells}</div>
    <div class="row between wrap"><span class="faint" style="font-size:12px">${active} trading days · ${green} green · ${active - green} red (UTC days)</span>
      <div class="heat-legend">Loss <i style="background:var(--down)"></i><i style="background:color-mix(in srgb,var(--down) 40%,var(--surface-3))"></i><i style="background:var(--surface-3)"></i><i style="background:color-mix(in srgb,var(--up) 40%,var(--surface-3))"></i><i style="background:var(--up)"></i> Profit</div></div></div>`;
}

function streaks(trades) {
  let bestW = 0, bestL = 0, w = 0, l = 0;
  for (const t of trades) {
    if (Number(t.pnl) >= 0) { w += 1; l = 0; } else { l += 1; w = 0; }
    bestW = Math.max(bestW, w); bestL = Math.max(bestL, l);
  }
  const last = trades[trades.length - 1];
  return { bestW, bestL, current: last ? (Number(last.pnl) >= 0 ? `${w}W` : `${l}L`) : "—" };
}

function maxDrawdown(curve) {
  let peak = 0, dd = 0;
  const under = curve.map((p) => { peak = Math.max(peak, p.value); dd = Math.min(dd, p.value - peak); return { time: p.time, value: Math.round((p.value - peak) * 100) / 100 }; });
  return { max: dd, under };
}

function insights(trades, sumAll) {
  const out = [];
  if (trades.length < 5) return out;
  const bySym = Object.entries(groupBy(trades, "symbol")).map(([k, v]) => [k, v.reduce((a, t) => a + Number(t.pnl), 0), v.length]).sort((a, b) => b[1] - a[1]);
  if (bySym.length > 1) {
    const [best, worst] = [bySym[0], bySym[bySym.length - 1]];
    out.push({ icon: "trendUp", tone: "up", text: html`<b>${best[0]}</b> is your best market (${fmtUsd(best[1], { sign: true })} over ${best[2]} trades); <b>${worst[0]}</b> is the weakest (${fmtUsd(worst[1], { sign: true })}).` });
  }
  const longs = trades.filter((t) => t.side === "long"), shorts = trades.filter((t) => t.side === "short");
  if (longs.length >= 3 && shorts.length >= 3) {
    const lp = longs.reduce((a, t) => a + Number(t.pnl), 0), sp = shorts.reduce((a, t) => a + Number(t.pnl), 0);
    out.push({ icon: "scale", tone: lp > sp ? "up" : "down", text: html`Longs ${fmtUsd(lp, { sign: true })} (${longs.length}) vs shorts ${fmtUsd(sp, { sign: true })} (${shorts.length}) — ${Math.abs(lp - sp) > 1 ? `${lp > sp ? "longs" : "shorts"} have carried the account.` : "roughly balanced."}` });
  }
  const quick = trades.filter((t) => isNum(t.duration_s) && t.duration_s <= 1200);
  if (quick.length >= 5) {
    const qp = quick.reduce((a, t) => a + Number(t.pnl), 0);
    if (qp < 0) out.push({ icon: "clock", tone: "warn", text: html`Trades closed within 20 minutes lost ${fmtUsd(qp)} across ${quick.length} trades — mostly stops hit soon after entry.` });
  }
  const byHour = groupBy(trades.filter((t) => t.entry_ts), (t) => new Date(parseTs(t.entry_ts)).getHours());
  const hours = Object.entries(byHour).filter(([, v]) => v.length >= 3).map(([h, v]) => [Number(h), v.reduce((a, t) => a + Number(t.pnl), 0)]);
  if (hours.length >= 3) {
    hours.sort((a, b) => b[1] - a[1]);
    out.push({ icon: "calendar", tone: "info", text: html`Best entry hour (your time): <b>${String(hours[0][0]).padStart(2, "0")}:00</b> (${fmtUsd(hours[0][1], { sign: true })}); worst: <b>${String(hours[hours.length - 1][0]).padStart(2, "0")}:00</b> (${fmtUsd(hours[hours.length - 1][1], { sign: true })}).` });
  }
  if (isNum(sumAll.pf) && sumAll.pf < 1) out.push({ icon: "alert", tone: "warn", text: html`Profit factor is ${sumAll.pf.toFixed(2)} — losses outweigh wins. Compare settings in the Backtest Lab before sizing up.` });
  return out;
}

export default function Analytics() {
  const st = useStore();
  const all = st.trades;
  const [range, setRange] = useState("all");
  const [sym, setSym] = useState("");
  const trades = useMemo(() => {
    let list = [...(all || [])].filter((t) => t.exit_ts).sort((a, b) => parseTs(a.exit_ts) - parseTs(b.exit_ts));
    if (range !== "all") { const cut = Date.now() - Number(range) * 86400000; list = list.filter((t) => parseTs(t.exit_ts) >= cut); }
    if (sym) list = list.filter((t) => t.symbol === sym);
    return list;
  }, [all, range, sym]);
  const symbols = useMemo(() => [...new Set((all || []).map((t) => t.symbol))].sort(), [all]);
  if (all === null) return html`<div class="stack"><div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:360px"></div></div>`;

  const sum = summarize(trades);
  const curve = cumulativePnl(trades);
  const dd = maxDrawdown(curve);
  const sk = streaks(trades);
  const spanDays = trades.length > 1 ? Math.max(1, (parseTs(trades[trades.length - 1].exit_ts) - parseTs(trades[0].exit_ts)) / 86400000) : 1;
  const bySym = Object.entries(groupBy(trades, "symbol")).map(([k, v]) => ({ label: k, value: v.reduce((a, t) => a + Number(t.pnl), 0), sub: `${v.length}` })).sort((a, b) => b.value - a.value);
  const byStrat = Object.entries(groupBy(trades, "strategy")).map(([k, v]) => ({ label: k, title: strategyLabel(k), value: v.reduce((a, t) => a + Number(t.pnl), 0), sub: `${v.length}` })).sort((a, b) => b.value - a.value);
  const hourCols = Array.from({ length: 24 }, (_, h) => {
    const v = trades.filter((t) => t.entry_ts && new Date(parseTs(t.entry_ts)).getHours() === h);
    const p = v.reduce((a, t) => a + Number(t.pnl), 0);
    return { label: h % 3 === 0 ? String(h).padStart(2, "0") : "", value: p, dim: !v.length, title: `${String(h).padStart(2, "0")}:00 · ${v.length} trades · ${fmtUsd(p, { sign: true })}` };
  });
  const buckets = [["< 20m", 0, 1200], ["20–60m", 1200, 3600], ["1–4h", 3600, 14400], ["4–12h", 14400, 43200], ["12–24h", 43200, 86400], ["> 1d", 86400, 1e12]];
  const holdItems = buckets.map(([label, lo, hi]) => { const v = trades.filter((t) => isNum(t.duration_s) && t.duration_s >= lo && t.duration_s < hi); return { label, value: v.reduce((a, t) => a + Number(t.pnl), 0), sub: `${v.length}` }; });
  const rBins = [[-99, -1.5, "≤-1.5"], [-1.5, -0.75, "-1"], [-0.75, -0.25, "-0.5"], [-0.25, 0.25, "0"], [0.25, 0.75, "0.5"], [0.75, 1.5, "1"], [1.5, 2.5, "2"], [2.5, 4, "3"], [4, 6, "5"], [6, 99, "6+"]];
  const rCols = rBins.map(([lo, hi, label]) => { const n = trades.filter((t) => isNum(t.r) && t.r >= lo && t.r < hi).length; return { label, value: n, color: lo >= 0.25 ? "var(--up)" : hi <= -0.25 ? "var(--down)" : "var(--text-3)", title: `${label}R: ${n} trades` }; });
  const reasons = Object.entries(groupBy(trades, "reason")).map(([k, v]) => ({ label: { SL: "Stop-loss", TP: "Take-profit", manual: "Manual", strategy: "Strategy exit" }[k] || k, value: v.reduce((a, t) => a + Number(t.pnl), 0), sub: `${v.length}` }));
  const sideStats = ["long", "short"].map((sd) => ({ sd, ...summarize(trades.filter((t) => t.side === sd)) }));
  const tips = insights(trades, sum);

  return html`<div class="stack">
    <div class="page-head"><div><h2>Analytics</h2><p>Performance from the trade journal. Ledger P&L — before exchange fees and funding.</p></div>
      <div class="row wrap"><div style="width:150px"><${Select} size="sm" value=${sym} onChange=${setSym} options=${[{ value: "", label: "All markets" }, ...symbols.map((x) => ({ value: x, label: x }))]} /></div>
        <${Seg} size="sm" value=${range} onChange=${setRange} options=${[{ value: "7", label: "7D" }, { value: "30", label: "30D" }, { value: "90", label: "90D" }, { value: "all", label: "All" }]} /></div></div>

    ${!sum.n ? html`<div class="card"><${Empty} icon="pie" title="No closed trades in this range" body="Analytics fill in as trades close." /></div>` : html`
    <div class="kpis">
      <${Stat} label="Net P&L" icon="dollar" value=${html`<span class=${tone(sum.net)}>${fmtUsd(sum.net, { sign: true })}</span>`} sub=${`${sum.n} trades · ${fmtNum(sum.n / spanDays, 1)}/day`} />
      <${Stat} label="Win rate" icon="target" value=${sum.winRate.toFixed(1) + "%"} sub=${html`<span class="up">${sum.wins}W</span> / <span class="down">${sum.losses}L</span>`} />
      <${Stat} label="Profit factor" icon="scale" value=${isNum(sum.pf) ? sum.pf.toFixed(2) : "∞"} sub=${html`payoff ${isNum(sum.avgWin) && isNum(sum.avgLoss) && sum.avgLoss ? (sum.avgWin / Math.abs(sum.avgLoss)).toFixed(2) : "—"}×`} />
      <${Stat} label="Expectancy" icon="trendUp" value=${html`<span class=${tone(sum.expectancy)}>${fmtUsd(sum.expectancy, { sign: true })}</span>`} sub=${`avg ${isNum(sum.avgR) ? sum.avgR.toFixed(2) : "—"}R per trade`} />
      <${Stat} label="Max drawdown" icon="trendDown" value=${html`<span class=${dd.max < 0 ? "down" : ""}>${fmtUsd(dd.max)}</span>`} sub="Realized, peak to trough" />
      <${Stat} label="Streaks" icon="activity" value=${sk.current} sub=${html`best <span class="up">${sk.bestW}W</span> · worst <span class="down">${sk.bestL}L</span>`} />
    </div>

    ${tips.length > 0 && html`<${Card} title="Insights" icon="sparkles" sub="Patterns spotted in this range">
      <div class="stack-sm">${tips.map((t) => html`<div class="row top" style="gap:10px"><span class=${cx("badge", t.tone)} style="width:26px;height:26px;padding:0;justify-content:center;border-radius:8px"><${Icon} name=${t.icon} size=${14} /></span><div style="font-size:13.5px;padding-top:3px">${t.text}</div></div>`)}</div>
    </${Card}>`}

    <div class="grid g2">
      <${Card} title="Cumulative P&L" sub=${`${fmtUsd(sum.net, { sign: true })} over ${Math.round(spanDays)} days`}><${AreaChart} data=${curve} height=${250} baseline /></${Card}>
      <${Card} title="Drawdown" sub="Distance below the running peak"><${AreaChart} data=${dd.under} height=${250} tone="down" /></${Card}>
    </div>

    <${Card} title="Daily P&L" sub="Each square is one UTC day" icon="calendar"><${Heatmap} trades=${trades} /></${Card}>

    <div class="grid g2">
      <${Card} title="By market" sub="Net P&L · trade count"><${Bars} items=${bySym} /></${Card}>
      <${Card} title="By strategy slot" sub="Net P&L · trade count"><${Bars} items=${byStrat} labelWidth="150px" /></${Card}>
    </div>

    <div class="grid g3">
      ${sideStats.map((x) => html`<${Card} title=${x.sd === "long" ? "Longs" : "Shorts"} icon=${x.sd === "long" ? "arrowUpRight" : "arrowDownRight"}>
        ${x.n ? html`<dl class="kv"><dt>Trades</dt><dd>${x.n}</dd><dt>Win rate</dt><dd>${x.winRate.toFixed(1)}%</dd><dt>Net</dt><dd class=${tone(x.net)}>${fmtUsd(x.net, { sign: true })}</dd>
          <dt>Profit factor</dt><dd>${isNum(x.pf) ? x.pf.toFixed(2) : "—"}</dd><dt>Avg R</dt><dd class=${tone(x.avgR)}>${isNum(x.avgR) ? x.avgR.toFixed(2) : "—"}</dd></dl>` : html`<div class="muted">No ${x.sd} trades</div>`}
      </${Card}>`)}
      <${Card} title="Exit reasons" icon="list"><${Bars} items=${reasons} /></${Card}>
    </div>

    <div class="grid g3">
      <${Card} title="P&L by entry hour" sub="Your local time"><${Histogram} cols=${hourCols} /></${Card}>
      <${Card} title="P&L by holding time" sub="Net P&L · trade count"><${Bars} items=${holdItems} labelWidth="70px" /></${Card}>
      <${Card} title="R-multiple distribution" sub="Trades per outcome bucket"><${Histogram} cols=${rCols} /></${Card}>
    </div>`}
  </div>`;
}
