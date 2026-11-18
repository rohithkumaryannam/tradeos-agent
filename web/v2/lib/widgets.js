// Trading widgets shared by several pages.
import { html, useState, useMemo } from "./preact.js";
import { Icon } from "./icons.js";
import { Badge, Button, Card, CoinIcon, Empty, Pnl, Price, SideChip, Spark, cx, Ago, Field, NumberInput, Select, Toggle, Callout, DrawerFrame } from "./ui.js";
import { store, useStore, slotFor, openPositions, eventIcon, eventTitle, act, openModal, toast, refreshConfig } from "./store.js";
import { api, fmtPrice, fmtUsd, fmtPct, tone, coinMeta, strategyLabel, timeAgo, paramInfo, isNum, sameValue } from "./util.js";
import { navigate, controls, Link } from "../app.js";

export function protectionBadge(p) {
  if (!p) return null;
  if (p === "exchange") return html`<${Badge} tone="up" title="Stop-loss and take-profit are resting on Delta">On exchange</${Badge}>`;
  if (p === "local") return html`<${Badge} tone="warn" title="Enforced by the bot loop only while it runs">Local only</${Badge}>`;
  return html`<${Badge} tone="down">Unprotected</${Badge}>`;
}

export function rMultiple(p, price) {
  if (!p?.entry || !p?.sl || !isNum(price)) return null;
  const risk = Math.abs(p.entry - p.sl);
  if (!risk) return null;
  const move = p.side === "long" ? price - p.entry : p.entry - price;
  return move / risk;
}

export function PositionsTable({ compact, s: snapIn }) {
  const st = useStore();
  const s = snapIn || st.snap;
  const rows = openPositions(s);
  if (!s) return html`<div class="card-body"><div class="skeleton" style="height:90px"></div></div>`;
  if (!rows.length) return html`<${Empty} icon="layers" title="No open positions" body="When a strategy enters a trade it shows up here with its live P&L and protection." />`;
  return html`<div class="table-wrap"><table class=${cx("t", compact && "compact")}>
    <thead><tr><th>Market</th><th>Side</th><th class="r">Entry</th><th class="r">Mark</th><th class="r">Unrealized</th><th class="r hide-sm">R</th>
      ${!compact && html`<th class="r">Stop</th><th class="r">Target</th><th>Protection</th><th class="hide-sm">Opened</th>`}<th></th></tr></thead>
    <tbody>${rows.map((p) => {
      const r = rMultiple(p, p.price);
      return html`<tr class="clickable" onClick=${() => navigate("/trade/" + p.symbol)}>
        <td><div class="row" style="gap:9px"><${CoinIcon} symbol=${p.symbol} size="sm" /><b>${p.symbol}</b></div></td>
        <td><${SideChip} side=${p.side} size=${p.size} /></td>
        <td class="r num">${fmtPrice(p.entry)}</td>
        <td class="r"><${Price} value=${p.price} /></td>
        <td class="r"><${Pnl} value=${p.unrealized} /></td>
        <td class=${cx("r num hide-sm", tone(r))}>${isNum(r) ? (r > 0 ? "+" : "") + r.toFixed(2) + "R" : "—"}</td>
        ${!compact && html`<td class="r num down">${fmtPrice(p.sl)}</td><td class="r num up">${fmtPrice(p.tp)}</td><td>${protectionBadge(p.protection)}</td>
          <td class="muted hide-sm">${p.entry_ts ? timeAgo(p.entry_ts) : "—"}</td>`}
        <td class="r" onClick=${(e) => e.stopPropagation()}><${Button} size="sm" variant="danger" onClick=${() => controls.closePosition(p.symbol)}>Close</${Button}></td>
      </tr>`;
    })}</tbody></table></div>`;
}

export function FeedList({ events, limit = 12, filter }) {
  const list = (events || []).filter((f) => !filter || filter(f)).slice(0, limit);
  if (!list.length) return html`<${Empty} icon="activity" title="Quiet so far" body="Entries, exits, rejections and engine events stream in here live." />`;
  return html`<div class="feed">${list.map((f) => {
    const loss = f.event === "exit" && /PnL -/.test(f.detail || "");
    return html`<div class="feed-row">
      <div class=${cx("f-icon", f.event, loss && "loss")}><${Icon} name=${eventIcon(f.event)} size=${14} /></div>
      <div style="min-width:0"><div class="f-text">${f.detail}</div><div class="f-meta">${eventTitle(f.event)}</div></div>
      <div class="f-meta"><${Ago} ts=${f.t} /></div></div>`;
  })}</div>`;
}

export function decisionText(m) {
  const d = m?.decision;
  if (!d) return "Waiting for the first cycle";
  const detail = String(d.detail || "").replace(/^\[[^\]]+\]\s*/, "").replace(/^[a-z0-9_]+:\s+/, "");
  return detail || d.event;
}

export function MarketCard({ m, slot }) {
  const meta = coinMeta(m.symbol);
  const pos = m.position;
  const chg = m.change_24h;
  const off = slot && slot.enabled === false;
  return html`<div class=${cx("card hover clickable market-card", pos && "has-pos", off && "off")} onClick=${() => navigate("/trade/" + m.symbol)}
      role="link" tabindex="0" onKeyDown=${(e) => e.key === "Enter" && navigate("/trade/" + m.symbol)}>
    <div class="mc-top">
      <${CoinIcon} symbol=${m.symbol} />
      <div class="grow" style="min-width:0"><div class="mc-name">${meta.base}<span class="faint" style="font-weight:500;font-size:12px"> ${m.symbol}</span></div>
        <div class="faint ellipsis" style="font-size:12px">${slot ? strategyLabel(slot.name) : "—"}${slot?.effective_params?.fallback_tp_mult ? ` · TP ${slot.effective_params.fallback_tp_mult}×` : ""}</div></div>
      ${off ? html`<${Badge}>Off</${Badge}>` : m.data_status !== "fresh" ? html`<${Badge} tone="warn" title=${m.data_status}>Data</${Badge}>` : m.pending ? html`<${Badge} tone="warn" dot pulse>Signal</${Badge}>` : null}
    </div>
    <div class="row between" style="align-items:flex-end">
      <div class="mc-price"><${Price} value=${m.price} /></div>
      <span class=${cx("num", tone(chg))} style="font-weight:650;font-size:13px">${isNum(chg) ? fmtPct(chg) : "—"}<span class="faint" style="font-weight:500"> 24h</span></span>
    </div>
    <div class="mc-spark"><${Spark} values=${m.spark} tone=${chg > 0 ? "up" : chg < 0 ? "down" : "flat"} fill /></div>
    <div class="mc-foot">
      ${pos ? html`<${SideChip} side=${pos.side} size=${pos.size} /><span class="grow"></span><${Pnl} value=${pos.unrealized} />`
        : html`<${Icon} name="clock" size=${13} /><span class="ellipsis" title=${decisionText(m)}>${decisionText(m)}</span>`}
    </div>
  </div>`;
}

/** Edit one coin's strategy slot: strategy, params, risk, on/off. */
export function SlotEditor({ slot, close }) {
  const st = useStore();
  const catalog = (st.catalog || []).filter((c) => c.name !== "combo");
  const [name, setName] = useState(slot.name);
  const strat = catalog.find((c) => c.name === name);
  const defaults = strat?.params || {};
  const startParams = name === slot.name ? slot.effective_params : defaults;
  const [vals, setVals] = useState({ ...startParams });
  const [risk, setRisk] = useState(slot.risk_pct);
  const [enabled, setEnabled] = useState(slot.enabled);
  const [busy, setBusy] = useState(false);
  const symbol = slot.symbols?.[0];
  const hasPos = slot.symbols?.some((x) => st.snap?.markets?.[x]?.position);
  const range = st.config?.slot_risk_range || [0.05, 5];

  const changeStrategy = (n) => {
    setName(n);
    const c = catalog.find((x) => x.name === n);
    setVals(n === slot.name ? { ...slot.effective_params } : { ...(c?.params || {}) });
  };
  const diffs = [];
  if (name !== slot.name) diffs.push(["Strategy", strategyLabel(slot.name), strategyLabel(name)]);
  for (const [k, v] of Object.entries(vals)) {
    const before = name === slot.name ? slot.effective_params[k] : defaults[k];
    if (name === slot.name && !sameValue(before, v)) diffs.push([paramInfo(k).label, String(before), String(v)]);
  }
  if (!sameValue(risk, slot.risk_pct)) diffs.push(["Risk per trade", slot.risk_pct + "%", risk + "%"]);
  if (enabled !== slot.enabled) diffs.push(["Trading", slot.enabled ? "On" : "Off", enabled ? "On" : "Off"]);

  const save = async () => {
    // Store only real overrides (values that differ from the strategy's
    // defaults) on top of a reset, so future default improvements still apply.
    const params = {};
    for (const [k, v] of Object.entries(vals)) {
      const d = defaults[k];
      const val = typeof d === "boolean" ? !!v : typeof d === "number" ? Number(v) : v;
      if (!sameValue(val, d)) params[k] = val;
    }
    setBusy(true);
    const ok = await act(() => api("/api/strategy/sub/" + encodeURIComponent(slot.tag), {
      method: "POST", body: { name, reset_params: true, params, risk_pct: Number(risk), enabled },
    }), { ok: `${symbol || slot.tag} updated`, okBody: diffs.map((d) => `${d[0]}: ${d[2]}`).join(" · "), fail: "Couldn't save" });
    setBusy(false);
    if (ok) close();
  };
  const reset = async () => {
    setBusy(true);
    const ok = await act(() => api("/api/strategy/sub/" + encodeURIComponent(slot.tag), { method: "POST", body: { reset_params: true } }),
      { ok: "Parameters reset to strategy defaults" });
    setBusy(false);
    if (ok) close();
  };

  return html`<${DrawerFrame} title=${html`<span class="row" style="gap:10px">${symbol && html`<${CoinIcon} symbol=${symbol} size="sm" />`}${symbol || slot.tag} strategy</span>`}
      sub=${`Slot “${slot.tag}”${slot.symbols?.length > 1 ? " · " + slot.symbols.join(", ") : ""} · changes apply to the next entry`} close=${close}
      foot=${html`<${Button} variant="ghost" onClick=${reset} disabled=${busy || name !== slot.name} title="Clear overrides">Reset to defaults</${Button}>
        <span class="grow"></span><${Button} variant="ghost" onClick=${close}>Cancel</${Button}>
        <${Button} variant="primary" loading=${busy} disabled=${!diffs.length} onClick=${save}>Save${diffs.length ? ` ${diffs.length} change${diffs.length > 1 ? "s" : ""}` : ""}</${Button}>`}>
    <div class="stack">
      <div class="card pad row" style="gap:14px">
        <div class="grow"><b>Trading on this coin</b><div class="muted" style="font-size:12.5px">Off keeps the strategy and settings but takes no new entries. An open position still closes on its exchange bracket.</div></div>
        <${Toggle} checked=${enabled} onChange=${setEnabled} tone="up" />
      </div>
      <${Field} label="Strategy" hint=${strat?.description}>
        <${Select} value=${name} onChange=${changeStrategy} disabled=${hasPos}
          options=${catalog.map((c) => ({ value: c.name, label: strategyLabel(c.name) + (c.resolution ? ` · ${c.resolution}` : "") }))} />
      </${Field}>
      ${hasPos && html`<${Callout} tone="warn" title="Position open">You can tune parameters now; switching to a different strategy needs the position closed first.</${Callout}>`}
      ${name !== slot.name && html`<${Callout} tone="info">Switching strategy starts from ${strategyLabel(name)}'s defaults.</${Callout}>`}
      <${Field} label="Risk per trade" right=${`${range[0]}% – ${range[1]}% of equity`} hint="Share of account equity lost if the stop-loss is hit.">
        <div class="row" style="gap:12px"><input type="range" min=${range[0]} max=${Math.min(2, range[1])} step="0.05" value=${risk}
          style=${{ "--fill": ((risk - range[0]) / (Math.min(2, range[1]) - range[0])) * 100 + "%" }} onInput=${(e) => setRisk(Number(e.currentTarget.value))} />
          <div style="width:110px"><${NumberInput} value=${risk} min=${range[0]} max=${range[1]} step="0.05" suffix="%" onInput=${(v) => setRisk(v)} changed=${!sameValue(risk, slot.risk_pct)} /></div></div>
      </${Field}>
      ${Object.keys(vals).length > 0 && html`<div class="stack-sm"><div class="label">Parameters</div>
        <div class="param-grid">${Object.entries(vals).map(([k, v]) => {
          const info = paramInfo(k);
          const d = defaults[k];
          const changed = name === slot.name ? !sameValue(slot.effective_params[k], v) : false;
          const isDefault = sameValue(d, v);
          if (typeof d === "boolean") return html`<div class="field"><span class="lbl">${info.label}</span>
            <div class="row" style="height:38px"><${Toggle} checked=${!!v} onChange=${(x) => setVals({ ...vals, [k]: x })} label=${v ? "On" : "Off"} /></div>
            ${info.hint && html`<span class="hint">${info.hint}</span>`}</div>`;
          if (typeof d === "number") return html`<${Field} label=${info.label} right=${!isDefault ? `default ${d}` : ""} hint=${info.hint}>
            <${NumberInput} value=${v} suffix=${info.unit} changed=${changed} step=${Number.isInteger(d) ? 1 : "any"} onInput=${(x) => setVals({ ...vals, [k]: x })} /></${Field}>`;
          return html`<${Field} label=${info.label} hint=${info.hint}><input class=${cx("input", changed && "changed")} value=${v} onInput=${(e) => setVals({ ...vals, [k]: e.currentTarget.value })} /></${Field}>`;
        })}</div></div>`}
      ${diffs.length > 0 && html`<div class="stack-sm"><div class="label">Review changes</div><div class="diff">
        ${diffs.map(([k, a, b]) => html`<div class="diff-row"><span class="muted">${k}</span><span class="from num">${a}</span><${Icon} name="arrowRight" size=${14} class="faint" /><span class="to num">${b}</span></div>`)}</div></div>`}
    </div>
  </${DrawerFrame}>`;
}

export function openSlotEditor(slot) {
  openModal((close) => html`<div class="drawer wide" role="dialog" aria-label="Edit strategy"><${SlotEditor} slot=${slot} close=${close} /></div>`, { kind: "drawer" });
}

/** Cumulative realized P&L series from the trade ledger. */
export function cumulativePnl(trades) {
  let run = 0;
  return [...(trades || [])].filter((t) => t.exit_ts).sort((a, b) => Date.parse(a.exit_ts) - Date.parse(b.exit_ts))
    .map((t) => { run += Number(t.pnl) || 0; return { time: Date.parse(t.exit_ts) / 1000, value: Math.round(run * 100) / 100 }; });
}
