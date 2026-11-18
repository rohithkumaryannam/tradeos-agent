import { html, useState, useMemo } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Stat, Badge, Seg, Pnl, cx, Ago, Skeleton } from "../lib/ui.js";
import { useStore, slotFor, openPositions, unrealizedTotal } from "../lib/store.js";
import { fmtUsd, fmtPct, fmtNum, isNum, prefs, strategyLabel, tone } from "../lib/util.js";
import { AreaChart } from "../lib/charts.js";
import { MarketCard, PositionsTable, FeedList, cumulativePnl } from "../lib/widgets.js";
import { controls, engineState, modeLabel, Link } from "../app.js";

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function EngineCard({ s }) {
  const st = engineState(s);
  const orb = !s ? "paused" : !s.running ? "off" : s.entries_paused || s.execution_enabled === false ? "paused" : "on";
  return html`<div class="card engine-card">
    <div class=${cx("engine-orb", orb)}><${Icon} name=${st.icon} size=${20} /></div>
    <div style="min-width:150px">
      <div class="row" style="gap:8px"><b style="font-size:15px">${st.label}</b><${Badge} tone=${s?.mode === "DELTA_DEMO" ? "accent" : ""}>${modeLabel(s?.mode)}</${Badge}></div>
      <div class="faint" style="font-size:12.5px;margin-top:3px">${s?.auto ? "Auto-execute" : "Manual confirm"} · cycle ${s?.health?.last_cycle ? html`<${Ago} ts=${s.health.last_cycle} />` : "—"}${s?.health?.cycle_ms ? ` · ${s.health.cycle_ms} ms` : ""}</div>
    </div>
    <div class="row" style="gap:6px;margin-left:auto">
      ${s?.running
        ? html`<${Button} size="sm" variant=${s.entries_paused ? "success" : ""} icon=${s.entries_paused ? "play" : "pause"} onClick=${s.entries_paused ? controls.resume : controls.pause}>${s.entries_paused ? "Resume" : "Pause"}</${Button}>
               <${Button} size="sm" variant="danger" icon="stop" onClick=${controls.stop}>Stop</${Button}>`
        : html`<${Button} size="sm" variant="success solid" icon="play" onClick=${controls.start} disabled=${!s}>Start bot</${Button}>`}
    </div>
  </div>`;
}

export default function Overview() {
  const st = useStore();
  const s = st.snap;
  const [range, setRange] = useState("30");
  const trades = st.trades || [];
  const curve = useMemo(() => {
    const all = cumulativePnl(trades);
    if (range === "all") return all;
    const cutoff = Date.now() / 1000 - Number(range) * 86400;
    const inRange = all.filter((p) => p.time >= cutoff);
    const base = all.filter((p) => p.time < cutoff).pop()?.value || 0;
    return inRange.map((p) => ({ time: p.time, value: Math.round((p.value - base) * 100) / 100 }));
  }, [trades, range]);

  const a = s?.analytics || {};
  const positions = openPositions(s);
  const unreal = unrealizedTotal(s);
  const todayKey = new Date().toISOString().slice(0, 10);
  const todayTrades = trades.filter((t) => String(t.exit_ts || "").slice(0, 10) === todayKey);
  // From the journal (survives restarts); the risk engine's own counter resets with the process.
  const todayPnl = st.trades ? todayTrades.reduce((acc, t) => acc + (Number(t.pnl) || 0), 0) : s?.realized_today;
  const eqStart = isNum(s?.equity) && isNum(todayPnl) ? s.equity - todayPnl : null;
  const markets = Object.values(s?.markets || {});
  const eqSpark = (s?.equity_curve || []).map((p) => p.equity);
  const cumSpark = cumulativePnl(trades).slice(-60).map((p) => p.value);
  const symbolPnl = Object.entries(a.by_symbol || {}).sort((x, y) => y[1] - x[1]);
  const maxAbs = Math.max(1, ...symbolPnl.map(([, v]) => Math.abs(v)));

  return html`<div class="stack">
    <div class="hero">
      <div class="hero-greet" style="align-self:center">
        <h2>${greeting()}, ${prefs.get().name || "trader"}</h2>
        <p>${s ? html`${markets.length} markets · ${positions.length ? `${positions.length} open position${positions.length > 1 ? "s" : ""}` : "no open positions"} · ${todayTrades.length} trade${todayTrades.length === 1 ? "" : "s"} closed today` : "Connecting to the trading engine…"}</p>
      </div>
      <${EngineCard} s=${s} />
    </div>

    <div class="kpis">
      <${Stat} label="Account equity" icon="wallet" value=${s ? fmtUsd(s.equity) : html`<${Skeleton} w="70%" h=${26} />`}
        sub=${s ? (s.equity_status === "live" ? html`<span class="dot up" style="width:6px;height:6px"></span> Delta testnet balance` : s.equity_status === "simulated" ? "Paper account" : "Account unavailable") : ""}
        spark=${eqSpark.length > 2 ? eqSpark : cumSpark} sparkTone=${tone(a.total_pnl) || "accent"} />
      <${Stat} label="Today's P&L" icon="calendar" value=${s ? html`<${Pnl} value=${todayPnl} />` : html`<${Skeleton} w="60%" h=${26} />`}
        sub=${s ? html`${isNum(eqStart) && eqStart > 0 ? html`<span class=${tone(todayPnl)}>${fmtPct((todayPnl / eqStart) * 100)}</span> · ` : ""}${todayTrades.length} closed · UTC day` : ""} />
      <${Stat} label="Open P&L" icon="activity" value=${s ? html`<${Pnl} value=${positions.length ? unreal : 0} />` : html`<${Skeleton} w="60%" h=${26} />`}
        sub=${s ? `${positions.length} open · ${fmtUsd(s.portfolio?.notional || 0, { compact: true })} exposure` : ""} />
      <${Stat} label="Win rate" icon="target" value=${isNum(a.win_rate) ? a.win_rate.toFixed(1) + "%" : "—"}
        sub=${a.trades ? html`<span class="up">${a.wins}W</span> / <span class="down">${a.losses}L</span> · PF ${a.profit_factor ?? "—"}` : "No closed trades yet"} />
      <${Stat} label="Net realized P&L" icon="trendUp" value=${html`<${Pnl} value=${a.total_pnl ?? null} />`}
        sub=${a.trades ? `${a.trades} trades · avg ${isNum(a.avg_r) ? (a.avg_r > 0 ? "+" : "") + a.avg_r + "R" : "—"}` : "Before fees & funding"} />
      <${Stat} label="Risk in use" icon="shield" value=${s?.portfolio?.risk_pct != null ? fmtNum(s.portfolio.risk_pct, 2) + "%" : "—"}
        sub=${s ? html`of ${s.risk?.max_portfolio_risk_pct}% stop budget · ${s.entry_risk_ok ? html`<span class="up">entries OK</span>` : html`<span class="warn" title=${s.entry_risk_reason}>blocked</span>`}` : ""} />
    </div>

    <div>
      <div class="row between" style="margin-bottom:10px"><h3 style="font-size:15px">Markets</h3><${Link} to="/strategies" class="btn ghost sm">Manage strategies <${Icon} name="chevronRight" size=${14} /></${Link}></div>
      <div class="grid g-auto">
        ${markets.length ? markets.map((m) => html`<${MarketCard} key=${m.symbol} m=${m} slot=${slotFor(m.symbol, s)} />`)
          : [1, 2, 3, 4, 5, 6].map(() => html`<div class="card market-card"><${Skeleton} h=${30} w="50%" /><${Skeleton} h=${22} w="60%" /><${Skeleton} h=${46} /></div>`)}
      </div>
    </div>

    <div class="grid g-main-wide">
      <${Card} title="Realized P&L" sub=${curve.length ? `${curve.length} trades in range · before fees & funding` : "Cumulative, from the trade journal"}
        actions=${html`<${Seg} size="sm" value=${range} onChange=${setRange} options=${[{ value: "7", label: "7D" }, { value: "30", label: "30D" }, { value: "90", label: "90D" }, { value: "all", label: "All" }]} />`}>
        <${AreaChart} data=${curve} height=${250} baseline />
      </${Card}>
      <${Card} title="P&L by market" sub="All closed trades" flush>
        <div class="card-body"><div class="bars">
          ${symbolPnl.length ? symbolPnl.map(([sym, v]) => html`<div class="bar-row"><span class="ellipsis" style="font-weight:600">${sym}</span>
            <div class="b-track"><div class="b-mid"></div><div class=${cx("b-fill", v >= 0 ? "pos" : "neg")} style=${{ width: (Math.abs(v) / maxAbs) * 50 + "%" }}></div></div>
            <span class=${cx("num right", tone(v))} style="font-weight:650">${fmtUsd(v, { sign: true })}</span></div>`)
          : html`<div class="muted center" style="padding:30px 0">No closed trades yet</div>`}
        </div></div>
      </${Card}>
    </div>

    <div class="grid g-main-wide">
      <${Card} title="Open positions" sub=${positions.length ? `${positions.length} open · ${fmtUsd(unreal, { sign: true })} unrealized` : ""} flush
        actions=${html`<${Link} to="/positions" class="btn ghost sm">Details</${Link}>`}><${PositionsTable} compact s=${s} /></${Card}>
      <${Card} title="Live activity" flush actions=${html`<${Link} to="/activity" class="btn ghost sm">All</${Link}>`}>
        <div style="max-height:360px;overflow-y:auto"><${FeedList} events=${st.events.length ? st.events : s?.feed} limit=${14} /></div>
      </${Card}>
    </div>
  </div>`;
}
