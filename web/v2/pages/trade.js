import { html, useState, useEffect, useMemo } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Seg, Pnl, Price, CoinIcon, SideChip, Toggle, Field, NumberInput, Empty, Callout, ModalFrame, Popover, cx, Ago } from "../lib/ui.js";
import { useStore, store, slotFor, act, openModal, toast } from "../lib/store.js";
import { api, fmtPrice, fmtUsd, fmtPct, fmtNum, isNum, tone, coinMeta, strategyLabel, paramInfo, prefs, TIMEFRAMES, timeAgo, fmtDur } from "../lib/util.js";
import { CandleChart } from "../lib/charts.js";
import { protectionBadge, rMultiple, decisionText, openSlotEditor, FeedList } from "../lib/widgets.js";
import { controls, navigate, Link } from "../app.js";

function CoinTabs({ s, active }) {
  return html`<div class="coin-tabs">${Object.values(s?.markets || {}).map((m) => html`
    <button class=${cx("coin-tab", m.symbol === active && "on")} onClick=${() => navigate("/trade/" + m.symbol)}>
      <${CoinIcon} symbol=${m.symbol} size="sm" />
      <span><div class="ct-sym">${coinMeta(m.symbol).base}${m.position ? html` <span class=${cx("dot", m.position.side === "long" ? "up" : "down")} style="display:inline-block;vertical-align:2px;width:6px;height:6px"></span>` : ""}</div>
        <div class="ct-px"><${Price} value=${m.price} /> <span class=${tone(m.change_24h)}>${isNum(m.change_24h) ? fmtPct(m.change_24h, 2) : ""}</span></div></span>
    </button>`)}</div>`;
}

function PositionCard({ m }) {
  const p = m.position;
  if (!p) {
    return html`<${Card} title="Position" sub="Flat">
      <div class="stack-sm">
        <div class="row" style="gap:10px"><div class="engine-orb paused" style="width:34px;height:34px;border-radius:10px"><${Icon} name="clock" size=${16} /></div>
          <div class="grow" style="min-width:0"><div style="font-weight:600">No open position</div><div class="faint ellipsis" style="font-size:12.5px" title=${decisionText(m)}>${decisionText(m)}</div></div></div>
      </div>
    </${Card}>`;
  }
  const r = rMultiple(p, m.price);
  // progress of price between stop (0%) and target (100%)
  let prog = null;
  if (isNum(p.sl) && isNum(p.tp) && isNum(m.price)) {
    const span = p.tp - p.sl;
    prog = span ? Math.min(100, Math.max(0, ((m.price - p.sl) / span) * 100)) : null;
  }
  const entryPct = isNum(p.sl) && isNum(p.tp) && p.tp !== p.sl ? ((p.entry - p.sl) / (p.tp - p.sl)) * 100 : null;
  const slDist = isNum(p.sl) && isNum(m.price) ? ((p.sl - m.price) / m.price) * 100 : null;
  const tpDist = isNum(p.tp) && isNum(m.price) ? ((p.tp - m.price) / m.price) * 100 : null;
  return html`<${Card} title=${html`<span class="row" style="gap:8px">Position <${SideChip} side=${p.side} size=${p.size} /></span>`} glow
    actions=${html`<${Button} size="sm" variant="danger" onClick=${() => controls.closePosition(m.symbol)}>Close</${Button}>`}>
    <div class="stack">
      <div class="row between" style="align-items:flex-end">
        <div><div class="label">Unrealized</div><div style="font-size:26px;font-weight:740;letter-spacing:-.03em"><${Pnl} value=${p.unrealized} /></div></div>
        <div class="right"><div class="label">R multiple</div><div class=${cx("num", tone(r))} style="font-size:18px;font-weight:700">${isNum(r) ? (r > 0 ? "+" : "") + r.toFixed(2) + "R" : "—"}</div></div>
      </div>
      ${prog !== null && html`<div>
        <div style="position:relative;height:8px;border-radius:8px;background:linear-gradient(90deg,var(--down-soft),var(--surface-3) 40%,var(--up-soft))">
          ${entryPct !== null && html`<div title="Entry" style=${{ position: "absolute", left: entryPct + "%", top: "-3px", bottom: "-3px", width: "2px", background: "var(--text-3)", borderRadius: "2px" }}></div>`}
          <div title="Mark price" style=${{ position: "absolute", left: `calc(${prog}% - 7px)`, top: "-4px", width: "16px", height: "16px", borderRadius: "50%", background: "var(--surface)", border: "3px solid " + (Number(p.unrealized) >= 0 ? "var(--up)" : "var(--down)"), boxShadow: "var(--shadow-sm)", transition: "left .5s var(--ease)" }}></div>
        </div>
        <div class="row between" style="margin-top:8px;font-size:12px">
          <span class="down num">SL ${fmtPrice(p.sl)} <span class="faint">(${fmtPct(slDist)})</span></span>
          <span class="up num">TP ${fmtPrice(p.tp)} <span class="faint">(${fmtPct(tpDist)})</span></span></div>
      </div>`}
      <dl class="kv">
        <dt>Entry</dt><dd>${fmtPrice(p.entry)}</dd>
        <dt>Mark</dt><dd><${Price} value=${m.price} /></dd>
        <dt>Notional</dt><dd>${fmtUsd(p.notional)}</dd>
        ${p.liquidation && html`<dt>Liquidation</dt><dd class="warn">${fmtPrice(p.liquidation)}</dd>`}
        <dt>Protection</dt><dd>${protectionBadge(p.protection)}</dd>
        <dt>Opened</dt><dd>${p.entry_ts ? html`<${Ago} ts=${p.entry_ts} />` : "—"}</dd>
      </dl>
    </div>
  </${Card}>`;
}

function PendingCard({ m }) {
  if (!m.pending) return null;
  const sym = m.symbol;
  return html`<div class="card pad" style="border-color:color-mix(in srgb,var(--warn) 45%,transparent);background:var(--warn-soft)">
    <div class="row" style="gap:10px;margin-bottom:10px"><${Icon} name="zap" size=${18} class="warn" /><b class="grow">Signal awaiting confirmation</b><${Badge} tone="warn">${m.pending.action.replace("ENTER_", "")}</${Badge}></div>
    <div class="muted" style="font-size:13px;margin-bottom:12px">${m.pending.reason}</div>
    <div class="row"><${Button} variant="primary" class="grow" onClick=${() => act(() => api("/api/signal/confirm?symbol=" + sym, { method: "POST" }), { ok: "Signal confirmed", refresh: false })}>Confirm</${Button}>
      <${Button} onClick=${() => act(() => api("/api/signal/dismiss?symbol=" + sym, { method: "POST" }), { ok: "Signal dismissed", refresh: false })}>Dismiss</${Button}></div>
  </div>`;
}

function Ticket({ m, s }) {
  const risk = s?.risk || {};
  const slot = slotFor(m.symbol, s);
  const [side, setSide] = useState("long");
  const [riskPct, setRiskPct] = useState(null);
  const [sl, setSl] = useState(null);
  const [tp, setTp] = useState(null);
  const rp = riskPct ?? slot?.risk_pct ?? risk.risk_per_trade_pct ?? 0.25;
  const slp = sl ?? risk.stop_loss_pct ?? 1;
  const tpp = tp ?? risk.take_profit_pct ?? 2;
  const price = Number(m.price);
  const dir = side === "long" ? 1 : -1;
  const slPrice = price * (1 - dir * slp / 100);
  const tpPrice = price * (1 + dir * tpp / 100);
  const rr = slp > 0 ? tpp / slp : null;
  const cv = Number(m.contract_value) || 0;
  const equity = Number(s?.equity) || 0;
  const riskUsd = equity * rp / 100;
  const perContract = cv * slp / 100;
  const contracts = perContract > 0 ? Math.floor(riskUsd / perContract) : 0;
  const notional = contracts * cv;
  const blocked = m.position ? "A position is already open on this market."
    : !s?.running ? "Start the bot to trade." : s?.entries_paused ? "New entries are paused."
    : s?.execution_enabled === false ? s.execution_reason : m.data_status !== "fresh" ? "Market data is stale." : null;
  const rrLow = isNum(rr) && isNum(risk.min_reward_risk) && rr < risk.min_reward_risk;
  const ceiling = Number(risk.risk_per_trade_pct) || 5;
  const riskHigh = rp > ceiling + 1e-9;
  const tooWide = isNum(risk.max_stop_distance_pct) && slp > risk.max_stop_distance_pct;

  const review = () => openModal((close) => html`<${ModalFrame} title=${`${side === "long" ? "Buy / Long" : "Sell / Short"} ${m.symbol}`} icon=${side === "long" ? "arrowUpRight" : "arrowDownRight"}
      iconTone=${side === "long" ? "on" : "off"} sub="Market order, sized by the risk engine, with a stop-loss and take-profit attached." close=${close}
      foot=${html`<${Button} variant="ghost" onClick=${close}>Cancel</${Button}>
        <${Button} variant=${side === "long" ? "success solid" : "danger solid"} onClick=${async () => {
          const r = await act(() => api("/api/trade", { method: "POST", body: { symbol: m.symbol, side, risk_pct: rp, sl_pct: slp, tp_pct: tpp } }), { refresh: false, fail: "Order rejected" });
          close();
          if (r?.result) toast(r.result.event === "entry" ? `${m.symbol} ${side} opened` : "Order not placed", { body: r.result.detail, tone: r.result.event === "entry" ? "up" : "warn" });
        }}>Place ${side} order</${Button}>`}>
    <dl class="kv">
      <dt>Venue</dt><dd>${s?.mode === "DELTA_DEMO" ? "Delta testnet" : "Paper (simulated)"}</dd>
      <dt>Price (approx.)</dt><dd>${fmtPrice(price)}</dd>
      <dt>Size (est.)</dt><dd>${contracts} contracts · ${fmtUsd(notional)}</dd>
      <dt>Risk</dt><dd>${rp}% · ${fmtUsd(riskUsd)}</dd>
      <dt>Stop-loss</dt><dd class="down">${fmtPrice(slPrice)} (${slp}%)</dd>
      <dt>Take-profit</dt><dd class="up">${fmtPrice(tpPrice)} (${tpp}%)</dd>
      <dt>Reward : risk</dt><dd>${isNum(rr) ? rr.toFixed(2) + "R" : "—"}</dd>
    </dl>
    <div class="faint" style="font-size:12px;margin-top:12px">The risk engine re-checks every limit at submission and sizes from live equity, so the final size can differ slightly. Manual trades are journaled under “Manual”.</div>
  </${ModalFrame}>`, { size: "sm" });

  return html`<${Card} title="Manual order" sub="Market · risk-sized · always bracketed" class="ticket">
    <div class="stack">
      <${Seg} block value=${side} onChange=${setSide} options=${[{ value: "long", label: "Buy / Long", class: "long" }, { value: "short", label: "Sell / Short", class: "short" }]} />
      <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr));gap:10px">
        <${Field} label="Risk"><${NumberInput} size="sm" value=${rp} step="0.05" min="0.05" max=${ceiling} suffix="%" invalid=${riskHigh} onInput=${(v) => setRiskPct(v === "" ? null : Number(v))} /></${Field}>
        <${Field} label="Stop"><${NumberInput} size="sm" value=${slp} step="0.1" min="0.05" suffix="%" invalid=${tooWide} onInput=${(v) => setSl(v === "" ? null : Number(v))} /></${Field}>
        <${Field} label="Target"><${NumberInput} size="sm" value=${tpp} step="0.1" min="0.05" suffix="%" invalid=${rrLow} onInput=${(v) => setTp(v === "" ? null : Number(v))} /></${Field}>
      </div>
      <dl class="kv" style="font-size:12.5px">
        <dt>Stop / target</dt><dd><span class="down">${fmtPrice(slPrice)}</span> / <span class="up">${fmtPrice(tpPrice)}</span></dd>
        <dt>Size (est.)</dt><dd>${contracts ? `${contracts} ct · ${fmtUsd(notional, { compact: true })}` : "—"}</dd>
        <dt>Reward : risk</dt><dd class=${rrLow ? "down" : ""}>${isNum(rr) ? rr.toFixed(2) + "R" : "—"}</dd>
      </dl>
      ${rrLow && html`<div class="err down" style="font-size:12px">Below the ${risk.min_reward_risk}R minimum — the risk engine will reject it.</div>`}
      ${tooWide && html`<div class="err down" style="font-size:12px">Stop is wider than the ${risk.max_stop_distance_pct}% maximum.</div>`}
      ${riskHigh && html`<div class="err down" style="font-size:12px">Above the ${ceiling}% per-trade ceiling (the largest per-coin risk cap on the Risk page).</div>`}
      <${Button} block size="lg" variant=${side === "long" ? "success solid" : "danger solid"} disabled=${!!blocked || rrLow || tooWide || riskHigh || !contracts} onClick=${review}>
        Review ${side === "long" ? "long" : "short"}</${Button}>
      ${blocked && html`<div class="faint center" style="font-size:12px">${blocked}</div>`}
    </div>
  </${Card}>`;
}

function StrategyCard({ m, s }) {
  const st = useStore();
  const slot = slotFor(m.symbol, s);
  if (!slot) return null;
  const defaults = (st.catalog || []).find((c) => c.name === slot.name)?.params || {};
  const changed = Object.entries(slot.params || {}).filter(([k, v]) => String(defaults[k]) !== String(v));
  const toggle = (on) => act(() => api("/api/strategy/sub/" + slot.tag, { method: "POST", body: { enabled: on } }), { ok: on ? `${m.symbol} trading on` : `${m.symbol} trading off` });
  return html`<${Card} title="Strategy" sub=${`slot ${slot.tag}`}
    actions=${html`<${Toggle} checked=${slot.enabled} onChange=${toggle} tone="up" title="Trading on/off for this coin" />`}>
    <div class="stack-sm">
      <div class="row" style="gap:10px"><div class="engine-orb on" style="width:34px;height:34px;border-radius:10px;background:var(--accent-soft);color:var(--accent);box-shadow:none"><${Icon} name="nodes" size=${16} /></div>
        <div class="grow"><b>${strategyLabel(slot.name)}</b><div class="faint" style="font-size:12.5px">${slot.resolution || "—"} candles · ${slot.risk_pct}% risk / trade</div></div></div>
      <div class="chips">
        ${Object.entries(slot.effective_params || {}).map(([k, v]) => html`<span class=${cx("chip", changed.some(([c]) => c === k) && "changed")}>${paramInfo(k).label} <b>${typeof v === "boolean" ? (v ? "on" : "off") : v}</b></span>`)}
      </div>
      <div class="row" style="margin-top:4px"><${Button} size="sm" icon="edit" class="grow" onClick=${() => openSlotEditor(slot)}>Edit</${Button}>
        <${Link} to=${"/backtest?symbol=" + m.symbol} class="btn sm"><${Icon} name="flask" size=${15} />Backtest</${Link}></div>
    </div>
  </${Card}>`;
}

export default function Trade({ params }) {
  const st = useStore();
  const s = st.snap;
  const symbols = Object.keys(s?.markets || {});
  const symbol = params.symbol && symbols.includes(params.symbol) ? params.symbol : (symbols.includes(prefs.get().lastSymbol) ? prefs.get().lastSymbol : symbols[0]);
  const [tf, setTf] = useState(prefs.get().chartTf || "15m");
  const [ind, setInd] = useState(prefs.get().indicators);
  const [tall, setTall] = useState(false);
  const [legend, setLegend] = useState(null);
  useEffect(() => { if (symbol) prefs.set({ lastSymbol: symbol }); }, [symbol]);
  useEffect(() => { if (s && params.symbol && !symbols.includes(params.symbol)) navigate("/trade/" + symbols[0], { replace: true }); }, [s && symbols.join()]);
  const m = s?.markets?.[symbol];
  const trades = useMemo(() => (st.trades || []).filter((t) => t.symbol === symbol), [st.trades, symbol]);
  if (!s) return html`<div class="stack"><div class="skeleton" style="height:58px"></div><div class="skeleton" style="height:520px"></div></div>`;
  if (!m) return html`<${Empty} icon="candles" title="No markets" body="Add a market on the Strategies page." />`;
  const meta = coinMeta(symbol);
  const setTfPersist = (v) => { setTf(v); prefs.set({ chartTf: v }); };
  const setIndPersist = (k, v) => { const next = { ...ind, [k]: v }; setInd(next); prefs.set({ indicators: next }); };
  const stats = (() => {
    const n = trades.length, w = trades.filter((t) => t.win).length;
    return { n, wr: n ? (w / n) * 100 : null, pnl: trades.reduce((a, t) => a + (Number(t.pnl) || 0), 0) };
  })();

  return html`<div class="stack">
    <${CoinTabs} s=${s} active=${symbol} />
    <div class="terminal">
      <div class="stack" style="min-width:0">
        <div class="card">
          <div class="card-head" style="flex-wrap:wrap;gap:12px">
            <${CoinIcon} symbol=${symbol} size="lg" />
            <div><div class="row" style="gap:8px"><h3 style="font-size:17px">${meta.name}</h3><span class="faint" style="font-size:12.5px">${symbol} · Perpetual</span></div>
              <div class="row" style="gap:10px"><span style="font-size:22px;font-weight:740;letter-spacing:-.03em"><${Price} value=${m.price} /></span>
                <span class=${cx("num", tone(m.change_24h))} style="font-weight:650">${isNum(m.change_24h) ? fmtPct(m.change_24h) + " 24h" : ""}</span></div></div>
            <div class="actions" style="flex-wrap:wrap">
              <${Seg} size="sm" value=${tf} onChange=${setTfPersist} options=${TIMEFRAMES} />
              <${Popover} width="220px" trigger=${({ toggle }) => html`<${Button} size="sm" icon="sliders" onClick=${toggle}>Indicators</${Button}>`}>
                <div class="p-head label">Overlays</div>
                ${[["ema9", "EMA 9", "#4aa8ff"], ["ema20", "EMA 20", "#f5a524"], ["ema200", "EMA 200", "#b48cff"], ["volume", "Volume", "var(--text-3)"]].map(([k, label, col]) => html`
                  <div class="p-head row"><span style=${{ width: "10px", height: "3px", borderRadius: "2px", background: col }}></span><span class="grow">${label}</span><${Toggle} checked=${ind[k]} onChange=${(v) => setIndPersist(k, v)} /></div>`)}
              </${Popover}>
              <${Button} size="sm" icon="maximize" onClick=${() => setTall(!tall)} title="Taller chart" />
            </div>
          </div>
          <div style="position:relative">
            <div class="chart-legend">${legend ? html`<span>O <b>${fmtPrice(legend.open)}</b></span><span>H <b>${fmtPrice(legend.high)}</b></span><span>L <b>${fmtPrice(legend.low)}</b></span><span>C <b class=${legend.close >= legend.open ? "up" : "down"}>${fmtPrice(legend.close)}</b></span>` : html`<span>${tf} · Delta</span>`}</div>
            <${CandleChart} symbol=${symbol} tf=${tf} livePrice=${m.price} position=${m.position} trades=${trades} indicators=${ind} height=${tall ? 620 : 460} onLegend=${setLegend} />
          </div>
        </div>
        <div class="grid g2">
          <${Card} title=${`${symbol} trade history`} sub=${stats.n ? `${stats.n} trades · ${stats.wr.toFixed(0)}% win · ${fmtUsd(stats.pnl, { sign: true })}` : "No closed trades yet"} flush
            actions=${html`<${Link} to=${"/journal?symbol=" + symbol} class="btn ghost sm">Journal</${Link}>`}>
            ${trades.length ? html`<div class="table-wrap" style="max-height:300px"><table class="t compact"><thead><tr><th>Closed</th><th>Side</th><th class="r">Entry → Exit</th><th class="r">P&L</th><th class="r">R</th></tr></thead>
              <tbody>${[...trades].reverse().slice(0, 30).map((t) => html`<tr><td class="muted">${timeAgo(t.exit_ts)}</td><td><${SideChip} side=${t.side} /></td>
                <td class="r num">${fmtPrice(t.entry)} → ${fmtPrice(t.exit)}</td><td class="r"><${Pnl} value=${t.pnl} /></td><td class=${cx("r num", tone(t.r))}>${isNum(t.r) ? t.r + "R" : "—"}</td></tr>`)}</tbody></table></div>`
            : html`<${Empty} icon="book" title="No trades on this market yet" />`}
          </${Card}>
          <${Card} title="Engine log" sub=${`Latest decision · ${m.decision?.event || "—"}`} flush>
            <div class="card-body" style="border-bottom:1px solid var(--border)"><div class="row" style="gap:10px;align-items:flex-start">
              <${Icon} name="bot" size=${16} class="faint" /><div class="grow" style="font-size:13px">${decisionText(m)}</div>
              <span class="faint" style="font-size:12px">${m.last_fetch ? timeAgo(m.last_fetch) : ""}</span></div>
              ${m.execution_issue && html`<div class="callout down" style="margin-top:10px"><${Icon} name="alert" size=${16} class="down c-icon" /><div>${m.execution_issue}</div></div>`}
            </div>
            <div style="max-height:236px;overflow-y:auto"><${FeedList} events=${st.events.filter((f) => (f.detail || "").includes(symbol))} limit=${20} /></div>
          </${Card}>
        </div>
      </div>
      <div class="stack" style="min-width:0">
        <${PendingCard} m=${m} />
        <${PositionCard} m=${m} />
        <${Ticket} m=${m} s=${s} key=${symbol} />
        <${StrategyCard} m=${m} s=${s} />
      </div>
    </div>
  </div>`;
}
