import { html, useState, useEffect } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Gauge, Field, NumberInput, Callout, confirmDialog, CoinIcon, cx } from "../lib/ui.js";
import { useStore, act, refreshConfig } from "../lib/store.js";
import { api, fmtUsd, fmtNum, isNum, sameValue, strategyLabel } from "../lib/util.js";

const GROUPS = [
  { title: "Portfolio limits", icon: "shield", sub: "Account-wide guardrails checked before every entry.", fields: [
    ["max_portfolio_risk_pct", "Portfolio stop risk", "%", "Combined entry-to-stop risk of all open positions, as % of equity. Entries that would exceed it are rejected."],
    ["max_daily_loss_pct", "Daily loss cap", "%", "Stops new entries for the rest of the UTC day once realized losses reach this share of the day's starting equity."],
    ["max_open_positions", "Position slots", "", "Most positions open at once across all markets."],
    ["max_same_direction_positions", "Same-direction positions", "", "Cap on simultaneous longs (or shorts) — crypto tends to move together."],
    ["max_leverage", "Max gross leverage", "×", "Total notional exposure ceiling as a multiple of equity."],
  ] },
  { title: "Trade defaults", icon: "target", sub: "Used when a strategy doesn't set its own stop/target, and pre-filled on manual orders.", fields: [
    ["stop_loss_pct", "Default stop-loss", "%", "Stop distance from entry."],
    ["take_profit_pct", "Default take-profit", "%", "Target distance from entry."],
    ["min_reward_risk", "Minimum reward : risk", "R", "Entries offering less than this are rejected."],
    ["max_stop_distance_pct", "Widest allowed stop", "%", "Rejects abnormally wide or malformed stops."],
  ] },
  { title: "Circuit breaker", icon: "zap", sub: "Automatic pause for a strategy on a losing streak.", fields: [
    ["max_consecutive_losses", "Losses in a row", "", "After this many consecutive losses a strategy pauses for the cooldown below."],
    ["strategy_cooldown_minutes", "Cooldown", "min", "How long the paused strategy waits before trading again."],
  ] },
];

function unlimitedHint(key, v) {
  if (!isNum(v)) return "";
  if (["max_open_positions", "max_same_direction_positions", "max_leverage", "max_consecutive_losses"].includes(key) && Number(v) >= 999) return "effectively unlimited";
  if (["max_daily_loss_pct", "max_portfolio_risk_pct"].includes(key) && Number(v) >= 99) return "effectively unlimited";
  return "";
}

export default function Risk() {
  const st = useStore();
  const s = st.snap;
  const cfg = st.config;
  const [draft, setDraft] = useState({});
  const [caps, setCaps] = useState({});
  const [busy, setBusy] = useState(false);
  useEffect(() => { refreshConfig(); }, []);
  if (!cfg) return html`<div class="stack"><div class="skeleton" style="height:150px"></div><div class="skeleton" style="height:420px"></div></div>`;
  const risk = cfg.risk, fileRisk = cfg.risk_config, limits = cfg.risk_limits;
  const val = (k) => (k in draft ? draft[k] : risk[k]);
  const capVal = (t) => (t in caps ? caps[t] : risk.strategy_risk_pct[t]);
  const changes = [];
  for (const g of GROUPS) for (const [k, label, unit] of g.fields) if (k in draft && !sameValue(draft[k], risk[k])) changes.push([label, risk[k] + (unit === "%" ? "%" : ""), draft[k] + (unit === "%" ? "%" : "")]);
  if ("account_equity" in draft && !sameValue(draft.account_equity, risk.account_equity)) changes.push(["Paper starting balance", fmtUsd(risk.account_equity), fmtUsd(draft.account_equity)]);
  for (const [t, v] of Object.entries(caps)) if (!sameValue(v, risk.strategy_risk_pct[t])) changes.push([`Risk · ${t}`, risk.strategy_risk_pct[t] + "%", v + "%"]);

  const pf = s?.portfolio || {};
  const dayCap = isNum(s?.equity) ? ((s.equity - (s.realized_today || 0)) * (risk.max_daily_loss_pct / 100)) : null;
  const dayUsed = isNum(dayCap) && isNum(pf.daily_loss_remaining) ? Math.max(0, dayCap - pf.daily_loss_remaining) : 0;
  const guards = Object.entries(s?.strategy_guards || {}).filter(([, g]) => !g.enabled);

  const save = async () => {
    const body = {};
    for (const [k, v] of Object.entries(draft)) body[k] = Number(v);
    if (Object.keys(caps).length) body.strategy_risk_pct = Object.fromEntries(Object.entries(caps).map(([t, v]) => [t, Number(v)]));
    setBusy(true);
    const ok = await act(() => api("/api/config/risk", { method: "POST", body }), { ok: "Risk controls saved", okBody: `${changes.length} change${changes.length > 1 ? "s" : ""} · apply to new entries now`, fail: "Couldn't save risk controls" });
    setBusy(false);
    if (ok) { setDraft({}); setCaps({}); }
  };
  const reset = async () => {
    if (!(await confirmDialog({ title: "Reset risk controls to config.yaml?", tone: "danger", confirm: "Reset", body: "Every risk limit and per-coin risk cap goes back to the file defaults. Open positions aren't resized." }))) return;
    await act(() => api("/api/config/risk/reset", { method: "POST" }), { ok: "Risk controls reset to config.yaml" });
    setDraft({}); setCaps({});
  };
  const resetPaper = async () => {
    if (!(await confirmDialog({ title: "Reset the paper balance?", confirm: "Reset balance", body: `Paper equity goes back to ${fmtUsd(risk.account_equity)}. Requires flat paper positions.` }))) return;
    act(() => api("/api/paper/reset", { method: "POST" }), { ok: "Paper balance reset", refresh: false });
  };

  const renderField = ([k, label, unit, hint]) => {
    const [lo, hi, kind] = limits[k] || [0, 1e9, "float"];
    const v = val(k);
    const changed = k in draft && !sameValue(draft[k], risk[k]);
    const invalid = !isNum(v) || Number(v) < lo || Number(v) > hi || (kind === "int" && !Number.isInteger(Number(v)));
    const differs = !sameValue(risk[k], fileRisk[k]);
    return html`<${Field} label=${label} right=${differs ? `file default ${fileRisk[k]}` : unlimitedHint(k, risk[k])} hint=${hint} error=${invalid && v !== "" ? `${kind === "int" ? "Whole number " : ""}${lo}–${hi}` : ""}>
      <${NumberInput} value=${v} min=${lo} max=${hi} step=${kind === "int" ? 1 : "any"} suffix=${unit} changed=${changed} invalid=${invalid}
        onInput=${(x) => setDraft({ ...draft, [k]: x })} /></${Field}>`;
  };

  return html`<div class="stack">
    <div class="page-head"><div><h2>Risk controls</h2><p>Every limit the risk engine enforces. Changes apply to new entries immediately; open positions are never resized.</p></div>
      <div class="row"><${Button} icon="undo" onClick=${reset}>Reset to config.yaml</${Button}></div></div>

    <div class="grid g4">
      <div class="card"><${Gauge} value=${dayUsed} max=${dayCap || 1} label="Daily loss used" display=${isNum(dayCap) ? `${fmtUsd(dayUsed)} / ${fmtUsd(dayCap, { compact: true })}` : "—"} /></div>
      <div class="card"><${Gauge} value=${pf.risk_pct || 0} max=${risk.max_portfolio_risk_pct} label="Portfolio stop risk" display=${`${fmtNum(pf.risk_pct || 0, 2)}% / ${risk.max_portfolio_risk_pct}%`} /></div>
      <div class="card"><${Gauge} value=${pf.positions || 0} max=${risk.max_open_positions} label="Position slots" display=${`${pf.positions || 0} / ${fmtNum(risk.max_open_positions, 0)}`} /></div>
      <div class="card"><${Gauge} value=${pf.leverage || 0} max=${risk.max_leverage} label="Gross leverage" display=${`${fmtNum(pf.leverage || 0, 2)}× / ${fmtNum(risk.max_leverage, 0)}×`} /></div>
    </div>

    ${s && !s.entry_risk_ok && html`<${Callout} tone="warn" title="New entries are blocked">${s.entry_risk_reason}</${Callout}>`}
    ${guards.length > 0 && html`<${Callout} tone="warn" title="Circuit breaker active">${guards.map(([n, g]) => html`<div><b>${n}</b> — ${g.reason}</div>`)}</${Callout}>`}

    <div class="grid g-main-wide" style="align-items:start">
      <div class="stack">
        ${GROUPS.map((g) => html`<${Card} title=${g.title} sub=${g.sub} icon=${g.icon}><div class="grid g2">${g.fields.map(renderField)}</div></${Card}>`)}
      </div>
      <div class="stack">
        <${Card} title="Risk per trade, by coin" sub="Share of equity lost if the stop-loss is hit" icon="percent">
          <div class="stack">
            ${(cfg.slots || []).map((slot) => {
              const t = slot.tag, v = capVal(t), [lo, hi] = cfg.slot_risk_range || [0.05, 5];
              const changed = t in caps && !sameValue(caps[t], risk.strategy_risk_pct[t]);
              const shown = Math.min(2, hi);
              return html`<div class="stack-sm">
                <div class="row">${slot.symbols?.length === 1 && html`<${CoinIcon} symbol=${slot.symbols[0]} size="sm" />`}<b class="grow">${slot.symbols?.join(", ") || t}</b>
                  <span class="faint" style="font-size:12px">${strategyLabel(slot.name)}</span></div>
                <div class="row" style="gap:12px"><input type="range" min=${lo} max=${shown} step="0.05" value=${v} style=${{ "--fill": ((Math.min(v, shown) - lo) / (shown - lo)) * 100 + "%" }}
                  onInput=${(e) => setCaps({ ...caps, [t]: Number(e.currentTarget.value) })} />
                  <div style="width:104px"><${NumberInput} size="sm" value=${v} step="0.05" min=${lo} max=${hi} suffix="%" changed=${changed} onInput=${(x) => setCaps({ ...caps, [t]: x })} /></div></div>
                ${isNum(s?.equity) && html`<div class="faint" style="font-size:12px">≈ ${fmtUsd((s.equity * Number(v)) / 100)} per stopped-out trade</div>`}
              </div>`;
            })}
          </div>
        </${Card}>
        <${Card} title="Paper account" sub="Simulated balance used in Paper mode" icon="wallet">
          <div class="stack">${renderField(["account_equity", "Starting balance", "$", "Used when switching to Paper and when resetting the paper balance."])}
            <${Button} icon="refresh" onClick=${resetPaper} disabled=${s?.mode !== "PAPER"} title=${s?.mode !== "PAPER" ? "Available in Paper mode" : ""}>Reset paper balance</${Button}></div>
        </${Card}>
      </div>
    </div>

    ${changes.length > 0 && html`<div class="card" style="position:sticky;bottom:calc(16px + env(safe-area-inset-bottom));z-index:20;border-color:color-mix(in srgb,var(--accent) 45%,transparent);box-shadow:var(--shadow-lg)">
      <div class="card-body row wrap" style="gap:12px">
        <${Icon} name="edit" size=${18} class="accent" />
        <div class="grow" style="min-width:200px"><b>${changes.length} unsaved change${changes.length > 1 ? "s" : ""}</b>
          <div class="faint ellipsis" style="font-size:12.5px">${changes.map((c) => `${c[0]}: ${c[1]} → ${c[2]}`).join(" · ")}</div></div>
        <${Button} variant="ghost" onClick=${() => { setDraft({}); setCaps({}); }}>Discard</${Button}>
        <${Button} variant="primary" loading=${busy} onClick=${save}>Save changes</${Button}>
      </div></div>`}
  </div>`;
}
