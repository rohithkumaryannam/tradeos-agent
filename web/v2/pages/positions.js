import { html, useState, useEffect } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Stat, Pnl, Empty, Callout, cx } from "../lib/ui.js";
import { useStore, openPositions, unrealizedTotal } from "../lib/store.js";
import { api, fmtUsd, fmtPrice, fmtNum, isNum } from "../lib/util.js";
import { PositionsTable } from "../lib/widgets.js";
import { controls } from "../app.js";

export default function Positions() {
  const st = useStore();
  const s = st.snap;
  const [orders, setOrders] = useState(null);
  const [ordersErr, setOrdersErr] = useState("");
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try { const r = await api("/api/orders"); setOrders(r); setOrdersErr(""); }
    catch (e) { setOrdersErr(e.message); }
    setLoading(false);
  };
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, []);

  const pos = openPositions(s);
  const unreal = unrealizedTotal(s);
  const pf = s?.portfolio || {};
  const exch = s?.exchange_positions || [];
  const known = new Set(pos.map((p) => p.symbol));
  const orphans = exch.filter((p) => !known.has(p.symbol));
  const longs = pos.filter((p) => p.side === "long").length;

  return html`<div class="stack">
    <div class="page-head"><div><h2>Positions & orders</h2><p>Everything open right now, how it's protected, and what's resting on the exchange.</p></div>
      <div class="row"><${Button} icon="refresh" onClick=${load} loading=${loading}>Refresh</${Button}>
        <${Button} variant="danger" icon="octagon" onClick=${controls.killSwitch}>Emergency controls</${Button}></div></div>

    <div class="grid g4">
      <${Stat} label="Open positions" icon="layers" value=${s ? `${pos.length}` : "—"} sub=${s ? `${longs} long · ${pos.length - longs} short · max ${fmtNum(s.risk?.max_open_positions, 0)}` : ""} />
      <${Stat} label="Unrealized P&L" icon="activity" value=${html`<${Pnl} value=${s ? unreal : null} />`} sub="Marked to the latest candle close" />
      <${Stat} label="Gross exposure" icon="scale" value=${s ? fmtUsd(pf.notional || 0) : "—"} sub=${isNum(pf.leverage) ? `${fmtNum(pf.leverage, 2)}× equity` : ""} />
      <${Stat} label="Stop risk" icon="shield" value=${s ? fmtUsd(pf.stop_risk || 0) : "—"} sub=${isNum(pf.risk_pct) ? `${fmtNum(pf.risk_pct, 2)}% of equity if every stop hits` : ""} />
    </div>

    ${orphans.length > 0 && html`<${Callout} tone="down" title="Exchange positions the bot doesn't track">
      Delta reports ${orphans.map((p) => `${p.symbol} (${p.size})`).join(", ")}. New entries are blocked until these are reconciled or closed on Delta.</${Callout}>`}

    <${Card} title="Open positions" sub=${pos.length ? `${fmtUsd(unreal, { sign: true })} unrealized` : ""} flush><${PositionsTable} s=${s} /></${Card}>

    <div class="grid g2">
      <${Card} title="Exchange orders" sub=${orders?.status === "simulated" ? "Paper mode — brackets are simulated locally" : "Resting stop-loss / take-profit orders on Delta"} flush
        actions=${html`<${Button} size="sm" variant="ghost" icon="refresh" onClick=${load} loading=${loading} />`}>
        ${ordersErr ? html`<div class="card-body"><${Callout} tone="warn">${ordersErr}</${Callout}></div>`
          : !orders ? html`<div class="card-body"><div class="skeleton" style="height:80px"></div></div>`
          : !orders.orders?.length ? html`<${Empty} icon="list" title=${orders.status === "simulated" ? "No exchange orders in paper mode" : "No open orders"} body="Each live position should show a stop-loss and a take-profit here." />`
          : html`<div class="table-wrap"><table class="t compact"><thead><tr><th>Market</th><th>Side</th><th>Type</th><th class="r">Price</th><th class="r">Size</th><th>State</th></tr></thead>
            <tbody>${orders.orders.map((o) => html`<tr><td><b>${o.symbol}</b></td><td><${Badge} tone=${o.side === "buy" ? "up" : "down"}>${o.side}</${Badge}></td>
              <td class="muted">${String(o.type || "").replace(/_/g, " ")}</td><td class="r num">${fmtPrice(o.price)}</td><td class="r num">${o.size}</td><td><${Badge}>${o.state}</${Badge}></td></tr>`)}</tbody></table></div>`}
      </${Card}>
      <${Card} title="Exchange positions" sub=${s?.mode === "DELTA_DEMO" ? "As reported by Delta (account-wide)" : "Paper mode — no exchange account"} flush>
        ${s?.mode !== "DELTA_DEMO" ? html`<${Empty} icon="globe" title="Simulated venue" body="Switch to Delta Demo in Settings to trade on the testnet account." />`
          : !exch.length ? html`<${Empty} icon="check" title="Flat on Delta" body="No positions reported by the exchange." />`
          : html`<div class="table-wrap"><table class="t compact"><thead><tr><th>Market</th><th class="r">Size</th><th class="r">Entry</th><th class="r">Liquidation</th><th>Tracked</th></tr></thead>
            <tbody>${exch.map((p) => html`<tr><td><b>${p.symbol}</b></td><td class=${cx("r num", Number(p.size) > 0 ? "up" : "down")}>${p.size}</td><td class="r num">${fmtPrice(p.entry)}</td>
              <td class="r num warn">${fmtPrice(p.liquidation)}</td><td>${known.has(p.symbol) ? html`<${Badge} tone="up">Yes</${Badge}>` : html`<${Badge} tone="down">No</${Badge}>`}</td></tr>`)}</tbody></table></div>`}
      </${Card}>
    </div>
  </div>`;
}
