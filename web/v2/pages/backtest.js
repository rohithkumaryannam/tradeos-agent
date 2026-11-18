import { html, useState, useEffect, useRef, useMemo } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Stat, Badge, Seg, Select, Field, NumberInput, Toggle, Empty, Callout, Meter, SideChip, Pnl, CoinIcon, confirmDialog, cx } from "../lib/ui.js";
import { useStore, slotFor, act, toast, toastError } from "../lib/store.js";
import { api, fmtUsd, fmtPct, fmtPrice, fmtNum, fmtDur, fmtTime, isNum, tone, strategyLabel, paramInfo, sameValue } from "../lib/util.js";
import { AreaChart } from "../lib/charts.js";

const PERIODS = [7, 30, 90, 180, 365];

function ParamFields({ defaults, vals, setVals, live }) {
  return html`<div class="param-grid">${Object.entries(defaults).map(([k, d]) => {
    const info = paramInfo(k);
    const v = vals[k] ?? d;
    const differsLive = live && k in live && !sameValue(live[k], v);
    if (typeof d === "boolean") return html`<div class="field"><span class="lbl">${info.label}</span><div class="row" style="height:38px"><${Toggle} checked=${!!v} onChange=${(x) => setVals({ ...vals, [k]: x })} label=${v ? "On" : "Off"} /></div></div>`;
    if (typeof d === "number") return html`<${Field} label=${info.label} right=${differsLive ? `live ${live[k]}` : ""}><${NumberInput} size="sm" value=${v} suffix=${info.unit} changed=${differsLive} step=${Number.isInteger(d) ? 1 : "any"} onInput=${(x) => setVals({ ...vals, [k]: x })} /></${Field}>`;
    return html`<${Field} label=${info.label}><input class="input sm" value=${v} onInput=${(e) => setVals({ ...vals, [k]: e.currentTarget.value })} /></${Field}>`;
  })}</div>`;
}

export default function Backtest({ search }) {
  const st = useStore();
  const s = st.snap;
  const catalog = (st.catalog || []).filter((c) => !["combo", "paused"].includes(c.name));
  const symbols = Object.keys(s?.markets || {});
  const qs = new URLSearchParams(search || "");
  const [symbol, setSymbol] = useState(qs.get("symbol") || "");
  const sym = symbol || symbols[0] || "";
  const slot = slotFor(sym, s);
  const [strategy, setStrategy] = useState("");
  const strat = strategy || (slot && slot.name !== "paused" ? slot.name : "donchian_breakout");
  const meta = catalog.find((c) => c.name === strat);
  const live = slot && slot.name === strat ? slot.effective_params : null;
  const [vals, setVals] = useState({});
  const [days, setDays] = useState(30);
  const [tf, setTf] = useState("");
  const [liveExits, setLiveExits] = useState(true);
  const [fee, setFee] = useState(5);
  const [slip, setSlip] = useState(2);
  const [equity, setEquity] = useState(10000);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const poll = useRef(null);

  // re-seed params when coin/strategy changes
  useEffect(() => { setVals(live ? { ...live } : { ...(meta?.params || {}) }); setTf(""); }, [sym, strat, meta ? 1 : 0]);
  useEffect(() => {
    api("/api/backtest").then((r) => {
      const running = (r.jobs || []).find((j) => j.status === "running");
      if (running) { setJob(running); watch(running.id); }
    }).catch(() => {});
    return () => clearTimeout(poll.current);
  }, []);

  function watch(id) {
    clearTimeout(poll.current);
    const tick = async () => {
      try {
        const j = await api("/api/backtest/" + id);
        setJob(j);
        if (j.status === "running") { poll.current = setTimeout(tick, 700); return; }
        if (j.status === "done" && j.result) {
          setResult(j.result);
          setHistory((h) => [{ id: j.id, at: Date.now(), r: j.result }, ...h.filter((x) => x.id !== j.id)].slice(0, 8));
          toast("Backtest finished", { body: `${j.result.symbol} · ${fmtPct(j.result.return_pct)} · ${j.result.analytics?.trades} trades`, tone: j.result.return_pct >= 0 ? "up" : "down", icon: "flask" });
        } else if (j.status === "error") toastError(new Error(j.error), "Backtest failed");
      } catch (e) { poll.current = setTimeout(tick, 1500); }
    };
    tick();
  }

  const run = async () => {
    const params = {};
    for (const [k, d] of Object.entries(meta?.params || {})) {
      const v = vals[k] ?? d;
      params[k] = typeof d === "boolean" ? !!v : typeof d === "number" ? Number(v) : v;
    }
    try {
      const r = await api("/api/backtest", { method: "POST", body: {
        symbol: sym, strategy: strat, params, days: Number(days), resolution: tf || undefined,
        bracket_owns_exit: liveExits, fee_bps: Number(fee), slippage_bps: Number(slip), equity: Number(equity),
        risk_pct: slot?.risk_pct } });
      setJob(r.job); setResult(null); watch(r.job.id);
    } catch (e) { toastError(e, "Couldn't start the backtest"); }
  };
  const cancel = () => job && api("/api/backtest/" + job.id, { method: "DELETE" }).then(() => setJob({ ...job, status: "cancelled" })).catch(() => {});

  const apply = async () => {
    if (!result || !slot) return;
    const req = result.request || {};
    const target = slotFor(req.symbol, s);
    if (!target) return toastError(new Error("That market has no strategy slot"), "Can't apply");
    const diffs = [];
    if (target.name !== req.strategy) diffs.push(`Strategy: ${strategyLabel(target.name)} → ${strategyLabel(req.strategy)}`);
    for (const [k, v] of Object.entries(req.params || {})) {
      const cur = target.name === req.strategy ? target.effective_params?.[k] : undefined;
      if (!sameValue(cur, v)) diffs.push(`${paramInfo(k).label}: ${cur ?? "—"} → ${v}`);
    }
    if (!diffs.length) return toast("Live settings already match this backtest", { tone: "info" });
    if (!(await confirmDialog({ title: `Apply to ${req.symbol} live?`, confirm: "Apply to live", tone: "success",
      body: html`<div class="stack-sm"><div>The next ${req.symbol} entry will use these settings. Open positions keep their current bracket.</div><div class="diff">${diffs.map((d) => html`<div class="diff-row" style="grid-template-columns:1fr"><span class="to">${d}</span></div>`)}</div></div>` }))) return;
    const defaults = catalog.find((c) => c.name === req.strategy)?.params || {};
    const overrides = Object.fromEntries(Object.entries(req.params || {}).filter(([k, v]) => !sameValue(v, defaults[k])));
    act(() => api("/api/strategy/sub/" + encodeURIComponent(target.tag), { method: "POST", body: { name: req.strategy, reset_params: true, params: overrides } }), { ok: `${req.symbol} updated from backtest` });
  };

  const running = job?.status === "running";
  const a = result?.analytics || {};
  const curve = useMemo(() => (result?.equity_samples || []).map((p) => ({ time: p.t, value: p.equity })), [result]);
  const resolutions = meta?.resolutions || (meta?.resolution ? [meta.resolution] : []);

  return html`<div class="stack">
    <div class="page-head"><div><h2>Backtest Lab</h2><p>Replay any strategy on recent Delta candles before you risk it live. Runs on the server at low priority — the live bot keeps its CPU.</p></div></div>
    <div class="grid g-lab">
      <div class="stack">
        <${Card} title="Setup" icon="sliders">
          <div class="stack">
            <div class="grid g2">
              <${Field} label="Market"><${Select} value=${sym} onChange=${(v) => { setSymbol(v); setStrategy(""); }} options=${symbols.map((x) => ({ value: x, label: x }))} /></${Field}>
              <${Field} label="Timeframe"><${Select} value=${tf || meta?.resolution || ""} onChange=${setTf} options=${resolutions.length ? resolutions : ["15m"]} /></${Field}>
            </div>
            <${Field} label="Strategy" hint=${slot ? html`Live on ${sym}: <b>${strategyLabel(slot.name)}</b>` : ""}>
              <${Select} value=${strat} onChange=${setStrategy} options=${catalog.map((c) => ({ value: c.name, label: strategyLabel(c.name) }))} />
            </${Field}>
            <${Field} label="Period"><${Seg} block size="sm" value=${days} onChange=${setDays} options=${PERIODS.map((d) => ({ value: d, label: d === 365 ? "1Y" : d + "D" }))} /></${Field}>
            ${meta && Object.keys(meta.params || {}).length > 0 && html`<div class="stack-sm"><div class="row between"><span class="label">Parameters</span>
              ${live && html`<${Button} size="sm" variant="ghost" icon="undo" onClick=${() => setVals({ ...live })}>Live values</${Button}>`}</div>
              <${ParamFields} defaults=${meta.params} vals=${vals} setVals=${setVals} live=${live} /></div>`}
            <div class="card" style="box-shadow:none;background:var(--surface-2)"><div class="card-body stack-sm">
              <div class="row"><div class="grow"><b style="font-size:13px">Live-faithful exits</b><div class="faint" style="font-size:12px">Only stop-loss / take-profit close trades, like live with Delta brackets.</div></div><${Toggle} checked=${liveExits} onChange=${setLiveExits} /></div>
            </div></div>
            <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr));gap:10px">
              <${Field} label="Fee/side"><${NumberInput} size="sm" value=${fee} step="0.5" suffix="bp" onInput=${setFee} /></${Field}>
              <${Field} label="Slippage"><${NumberInput} size="sm" value=${slip} step="0.5" suffix="bp" onInput=${setSlip} /></${Field}>
              <${Field} label="Equity"><${NumberInput} size="sm" value=${equity} step="1000" suffix="$" onInput=${setEquity} /></${Field}>
            </div>
            ${running
              ? html`<div class="stack-sm"><div class="row between" style="font-size:13px"><span class="muted">${job.stage}…</span><b class="num">${Math.round((job.progress || 0) * 100)}%</b></div>
                  <${Meter} value=${(job.progress || 0) * 100} tone="up" /><${Button} variant="ghost" size="sm" onClick=${cancel}>Cancel</${Button}></div>`
              : html`<${Button} variant="primary" size="lg" block icon="play" onClick=${run} disabled=${!sym || !meta}>Run backtest</${Button}>`}
          </div>
        </${Card}>
        ${history.length > 1 && html`<${Card} title="This session's runs" flush><div class="feed">${history.map((h) => html`<button class="p-item" style="border-radius:0;border-bottom:1px solid var(--border)" onClick=${() => setResult(h.r)}>
          <${CoinIcon} symbol=${h.r.symbol} size="sm" /><span class="grow ellipsis" style="font-size:12.5px"><b>${h.r.symbol}</b> ${strategyLabel(h.r.strategy)} · ${h.r.request?.days}d${h.r.params?.fallback_tp_mult ? ` · TP ${h.r.params.fallback_tp_mult}×` : ""}</span>
          <span class=${cx("num", tone(h.r.return_pct))} style="font-weight:650;font-size:12.5px">${fmtPct(h.r.return_pct)}</span></button>`)}</div></${Card}>`}
      </div>

      <div class="stack" style="min-width:0">
        ${!result ? html`<div class="card">${running ? html`<div class="empty" style="padding:70px 20px"><span class="spinner" style="width:28px;height:28px"></span><strong>Running ${strategyLabel(strat)} on ${sym}</strong><span>${job.stage} — ${Math.round((job.progress || 0) * 100)}%</span></div>`
            : html`<${Empty} icon="flask" title="Pick a setup and run it" body="Results show here: return, drawdown, win rate, every simulated trade, and a one-click way to apply the settings live." />`}</div>`
        : html`
          <div class="card pad row wrap" style="gap:14px">
            <${CoinIcon} symbol=${result.symbol} size="lg" />
            <div class="grow"><div style="font-size:16px;font-weight:700">${strategyLabel(result.strategy)} on ${result.symbol}</div>
              <div class="faint" style="font-size:12.5px">${result.resolution} · ${result.request?.days} days · ${fmtTime(result.evaluation_start * 1000, { date: true })} → ${fmtTime(result.evaluation_end * 1000, { date: true })} · ${result.bracket_owns_exit ? "live-faithful exits" : "strategy exits"}</div></div>
            ${slotFor(result.symbol, s) && html`<${Button} variant="success solid" icon="check" onClick=${apply}>Apply to ${result.symbol}</${Button}>`}
          </div>
          <div class="grid g3 keep-sm" style="grid-template-columns:repeat(auto-fill,minmax(min(170px,100%),1fr))">
            <${Stat} label="Return" value=${html`<span class=${tone(result.return_pct)}>${fmtPct(result.return_pct)}</span>`} sub=${`${fmtUsd(result.starting_equity, { compact: true })} → ${fmtUsd(result.final_equity, { compact: true })}`} />
            <${Stat} label="Max drawdown" value=${html`<span class="down">${fmtPct(-result.max_drawdown_pct)}</span>`} sub="Mark-to-market" />
            <${Stat} label="Trades" value=${a.trades ?? 0} sub=${`${a.wins ?? 0}W / ${a.losses ?? 0}L · ${fmtNum(result.rejected_entries, 0)} rejected`} />
            <${Stat} label="Win rate" value=${isNum(a.win_rate) ? a.win_rate + "%" : "—"} sub=${`avg ${isNum(a.avg_r) ? a.avg_r + "R" : "—"}`} />
            <${Stat} label="Profit factor" value=${a.profit_factor ?? (a.wins ? "∞" : "—")} sub=${html`fees ${fmtUsd(result.fees_paid, { compact: true })}`} />
            <${Stat} label="vs buy & hold" value=${html`<span class=${tone(result.buy_hold_spot_return_pct)}>${fmtPct(result.buy_hold_spot_return_pct)}</span>`} sub="Unlevered spot, before costs" />
          </div>
          <${Card} title="Equity" sub="Mark-to-market, sampled through the run"><${AreaChart} data=${curve} height=${280} tone=${result.return_pct >= 0 ? "up" : "down"} /></${Card}>
          <${Card} title="Simulated trades" sub=${`${result.trade_total} trades${result.trade_total > 500 ? " · latest 500 shown" : ""}`} flush>
            ${result.trades?.length ? html`<div class="table-wrap" style="max-height:420px"><table class="t compact"><thead><tr><th>Exit</th><th>Side</th><th class="r">Entry</th><th class="r">Exit</th><th class="r">P&L</th><th class="r">R</th><th class="r">Held</th><th>Reason</th></tr></thead>
              <tbody>${[...result.trades].reverse().map((t) => html`<tr><td class="muted">${fmtTime(t.exit_ts)}</td><td><${SideChip} side=${t.side} /></td>
                <td class="r num">${fmtPrice(t.entry)}</td><td class="r num">${fmtPrice(t.exit)}</td><td class="r"><${Pnl} value=${t.pnl} /></td>
                <td class=${cx("r num", tone(t.r))}>${isNum(t.r) ? t.r : "—"}</td><td class="r muted">${fmtDur(t.duration_s)}</td><td><${Badge} tone=${t.reason === "TP" ? "up" : t.reason === "SL" ? "down" : ""}>${t.reason}</${Badge}></td></tr>`)}</tbody></table></div>`
            : html`<${Empty} icon="flask" title="No trades in this window" body="The strategy never triggered — try a longer period." />`}
          </${Card}>
          <div class="faint" style="font-size:12px">${result.assumptions}${result.gaps_filled ? ` ${result.gaps_filled} empty bars were filled flat.` : ""}</div>`}
      </div>
    </div>
  </div>`;
}
