import { html, useState, useEffect, useMemo } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Toggle, Price, CoinIcon, Empty, Field, Select, NumberInput, ModalFrame, Popover, Callout, confirmDialog, SideChip, Tabs, cx } from "../lib/ui.js";
import { useStore, store, act, openModal, refreshConfig, toast } from "../lib/store.js";
import { api, fmtUsd, fmtPct, fmtPrice, fmtCompact, isNum, tone, strategyLabel, paramInfo, coinMeta } from "../lib/util.js";
import { openSlotEditor, decisionText } from "../lib/widgets.js";
import { Link, navigate } from "../app.js";

function SlotCard({ slot, s, stats, catalog }) {
  const sym = slot.symbols?.[0];
  const m = s?.markets?.[sym];
  const defaults = catalog.find((c) => c.name === slot.name)?.params || {};
  const changed = new Set(Object.entries(slot.params || {}).filter(([k, v]) => String(defaults[k]) !== String(v)).map(([k]) => k));
  const toggle = (on) => act(() => api("/api/strategy/sub/" + encodeURIComponent(slot.tag), { method: "POST", body: { enabled: on } }),
    { ok: `${sym || slot.tag} trading ${on ? "on" : "off"}`, okBody: on ? "New entries allowed." : "No new entries; open positions keep their bracket." });
  const remove = async () => {
    if (!(await confirmDialog({ title: `Stop trading ${sym}?`, tone: "danger", confirm: "Remove market", typeToConfirm: sym,
      body: `${sym} and its strategy slot “${slot.tag}” are removed from the bot. Its trade history stays in the journal. You can add it back any time.` }))) return;
    act(() => api("/api/markets/" + encodeURIComponent(sym), { method: "DELETE" }), { ok: `${sym} removed`, fail: "Couldn't remove market" });
  };
  const pnl = stats?.total_pnl;
  return html`<div class=${cx("card slot-card hover", !slot.enabled && "off")} style=${!slot.enabled ? "opacity:.8" : ""}>
    <div class="sc-head">
      ${sym ? html`<${CoinIcon} symbol=${sym} />` : html`<div class="coin" style="background:var(--surface-3)">∗</div>`}
      <div class="grow" style="min-width:0">
        <div class="row" style="gap:8px"><b style="font-size:15px">${sym ? coinMeta(sym).base : slot.tag}</b>${m && html`<span class="faint num" style="font-size:12.5px"><${Price} value=${m.price} /></span>`}</div>
        <div class="faint ellipsis" style="font-size:12px">slot ${slot.tag}${slot.symbols?.length > 1 ? " · " + slot.symbols.join(", ") : ""}</div>
      </div>
      ${m?.position && html`<${SideChip} side=${m.position.side} size=${m.position.size} />`}
      <${Toggle} checked=${slot.enabled} onChange=${toggle} tone="up" title=${slot.enabled ? "Trading on" : "Trading off"} />
    </div>
    <div class="sc-body">
      <div class="row" style="gap:10px">
        <div class="engine-orb" style="width:36px;height:36px;border-radius:11px;background:var(--accent-soft);color:var(--accent)"><${Icon} name="nodes" size=${17} /></div>
        <div class="grow" style="min-width:0"><div style="font-weight:650">${strategyLabel(slot.name)}</div>
          <div class="faint" style="font-size:12.5px">${slot.resolution || "—"} · ${slot.risk_pct}% risk/trade${!slot.enabled ? " · paused" : ""}</div></div>
      </div>
      <div class="chips">${Object.entries(slot.effective_params || {}).map(([k, v]) => html`<span class=${cx("chip", changed.has(k) && "changed")} title=${paramInfo(k).hint}>${paramInfo(k).label} <b>${typeof v === "boolean" ? (v ? "on" : "off") : v}${paramInfo(k).unit === "× stop distance" ? "×" : ""}</b></span>`)}</div>
      <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr));gap:8px">
        <div class="card" style="padding:10px 12px;box-shadow:none;background:var(--surface-2)"><div class="faint" style="font-size:11.5px">Trades</div><b class="num">${stats?.trades ?? 0}</b></div>
        <div class="card" style="padding:10px 12px;box-shadow:none;background:var(--surface-2)"><div class="faint" style="font-size:11.5px">Win rate</div><b class="num">${isNum(stats?.win_rate) ? stats.win_rate + "%" : "—"}</b></div>
        <div class="card" style="padding:10px 12px;box-shadow:none;background:var(--surface-2)"><div class="faint" style="font-size:11.5px">Net P&L</div><b class=${cx("num", tone(pnl))}>${isNum(pnl) ? fmtUsd(pnl, { sign: true }) : "—"}</b></div>
      </div>
      ${m && html`<div class="faint ellipsis" style="font-size:12px" title=${decisionText(m)}><${Icon} name="clock" size=${12} /> ${decisionText(m)}</div>`}
    </div>
    <div class="sc-foot">
      <${Button} size="sm" variant="primary" icon="edit" onClick=${() => openSlotEditor(slot)}>Edit strategy</${Button}>
      ${sym && html`<${Link} to=${"/backtest?symbol=" + sym} class="btn sm"><${Icon} name="flask" size=${15} />Backtest</${Link}>`}
      <span class="grow"></span>
      <${Popover} width="210px" trigger=${({ toggle }) => html`<${Button} size="sm" variant="ghost" icon="more" onClick=${toggle} aria-label="More" />`}>
        ${(close) => html`${sym && html`<button class="p-item" onClick=${() => { close(); navigate("/trade/" + sym); }}><${Icon} name="candles" size=${16} />Open chart</button>`}
          ${sym && html`<button class="p-item" onClick=${() => { close(); navigate("/journal?symbol=" + sym); }}><${Icon} name="book" size=${16} />Trade history</button>`}
          <button class="p-item" onClick=${() => { close(); act(() => api("/api/strategy/sub/" + slot.tag, { method: "POST", body: { reset_params: true } }), { ok: "Parameters reset to defaults" }); }}><${Icon} name="undo" size=${16} />Reset parameters</button>
          ${sym && html`<div class="p-sep"></div><button class="p-item danger" onClick=${() => { close(); remove(); }}><${Icon} name="trash" size=${16} />Remove market</button>`}`}
      </${Popover}>
    </div>
  </div>`;
}

function AddMarket({ close }) {
  const st = useStore();
  const catalog = (st.catalog || []).filter((c) => c.name !== "combo");
  const [products, setProducts] = useState(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("donchian_breakout");
  const [risk, setRisk] = useState(0.25);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/products").then((r) => setProducts(r.products)).catch((e) => { setErr(e.message); setProducts([]); }); }, []);
  const list = (products || []).filter((p) => !p.trading && (!q || (p.symbol + " " + p.description).toLowerCase().includes(q.toLowerCase()))).slice(0, 60);
  const raw = q.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  const typed = raw && !/USDT?$/.test(raw) ? raw + "USD" : raw;
  const listed = products && !err ? products.some((p) => p.symbol === typed) : true;
  const already = (products || []).some((p) => p.symbol === typed && p.trading);
  const typedOk = typed && listed && !already;
  const submit = async () => {
    const sym = symbol || (typedOk ? typed : "");
    if (!sym) return;
    setBusy(true);
    const ok = await act(() => api("/api/markets", { method: "POST", body: { symbol: sym, strategy, risk_pct: Number(risk) } }),
      { ok: `${sym} added`, okBody: `${strategyLabel(strategy)} · evaluated from the next cycle`, fail: "Couldn't add market" });
    setBusy(false);
    if (ok) close();
  };
  return html`<${ModalFrame} title="Add a market" sub="Start trading another Delta perpetual with its own strategy slot." close=${close} icon="plus" iconTone="on"
    foot=${html`<${Button} variant="ghost" onClick=${close}>Cancel</${Button}><${Button} variant="primary" loading=${busy} disabled=${!(symbol || typedOk)} onClick=${submit}>Add ${symbol || (typedOk ? typed : "market")}</${Button}>`}>
    <div class="stack">
      <${Field} label="Market" hint=${err ? "Couldn't load the exchange list — type the exact symbol (e.g. LTCUSD)." : "Perpetual futures on Delta, sorted by 24h turnover."}>
        <div class="input-group"><span class="prefix"><${Icon} name="search" size=${15} /></span>
          <input class="input has-prefix" placeholder="Search e.g. LTC, AVAX, BNB…" value=${q} onInput=${(e) => { setQ(e.currentTarget.value); setSymbol(""); }} autofocus /></div>
      </${Field}>
      <div class="card" style="max-height:230px;overflow-y:auto;box-shadow:none">
        ${products === null ? html`<div class="card-body"><div class="skeleton" style="height:120px"></div></div>`
          : list.length ? list.map((p) => html`<button class=${cx("p-item")} style=${{ borderRadius: 0, borderBottom: "1px solid var(--border)", background: symbol === p.symbol ? "var(--accent-soft)" : "" }} onClick=${() => setSymbol(p.symbol)}>
              <${CoinIcon} symbol=${p.symbol} size="sm" /><span class="grow ellipsis"><b>${p.symbol}</b> <span class="faint" style="font-size:12px">${p.description}</span></span>
              <span class="num faint" style="font-size:12px">${p.price ? fmtPrice(p.price) : ""}</span>
              ${symbol === p.symbol && html`<${Icon} name="check" size=${16} class="accent" />`}</button>`)
          : html`<div class="empty" style="padding:22px"><span>${!typed ? "No markets found"
              : already ? `${typed} is already trading.`
              : !listed ? `${typed} isn't listed on this Delta venue (${products.length} perpetuals available).`
              : `“${typed}” will be checked against the exchange when you add it.`}</span></div>`}
      </div>
      <div class="grid g2">
        <${Field} label="Strategy"><${Select} value=${strategy} onChange=${setStrategy} options=${catalog.map((c) => ({ value: c.name, label: strategyLabel(c.name) }))} /></${Field}>
        <${Field} label="Risk per trade"><${NumberInput} value=${risk} step="0.05" min="0.05" max="5" suffix="%" onInput=${setRisk} /></${Field}>
      </div>
      <div class="faint" style="font-size:12px">${catalog.find((c) => c.name === strategy)?.description || ""}</div>
    </div>
  </${ModalFrame}>`;
}

export function openAddMarket() { openModal((close) => html`<${AddMarket} close=${close} />`); }

function Library({ catalog, slots }) {
  const [open, setOpen] = useState(null);
  return html`<div class="grid g-auto">${catalog.filter((c) => c.name !== "combo").map((c) => {
    const users = slots.filter((x) => x.name === c.name).flatMap((x) => x.symbols || []);
    return html`<div class="card pad stack-sm">
      <div class="row"><b class="grow">${strategyLabel(c.name)}</b>${users.length ? html`<${Badge} tone="accent">${users.length} live</${Badge}>` : html`<${Badge}>Idle</${Badge}>`}</div>
      <div class="muted" style="font-size:12.5px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:${open === c.name ? 99 : 3};-webkit-box-orient:vertical;overflow:hidden">${c.description || "No description."}</div>
      <div class="chips">${c.resolution && html`<span class="chip">${(c.resolutions || [c.resolution]).join(" · ")}</span>`}${users.map((u) => html`<span class="chip"><b>${u}</b></span>`)}</div>
      <div class="row"><${Button} size="sm" variant="ghost" onClick=${() => setOpen(open === c.name ? null : c.name)}>${open === c.name ? "Less" : "Details"}</${Button}><span class="grow"></span>
        <span class="faint" style="font-size:12px">${Object.keys(c.params || {}).length} params</span></div>
      ${open === c.name && html`<div class="chips">${Object.entries(c.params || {}).map(([k, v]) => html`<span class="chip">${paramInfo(k).label} <b>${String(v)}</b></span>`)}</div>`}
    </div>`;
  })}</div>`;
}

export default function Strategies({ search }) {
  const st = useStore();
  const s = st.snap;
  const [tab, setTab] = useState("coins");
  const slots = s?.slots || [];
  const catalog = st.catalog || [];
  const by = s?.analytics?.by_strategy || {};
  useEffect(() => { if (new URLSearchParams(search || "").get("add")) { openAddMarket(); history.replaceState({}, "", "/strategies"); } }, []);
  const enabled = slots.filter((x) => x.enabled).length;
  const inUse = [...new Set(slots.map((x) => x.name))];
  return html`<div class="stack">
    <div class="page-head"><div><h2>Strategies</h2><p>Choose what runs on every coin, tune it, size it, or switch it off — changes apply on the next entry and survive restarts.</p></div>
      <div class="row"><${Button} icon="download" onClick=${() => { location.href = "/api/config/export"; }}>Export config</${Button}>
        <${Button} variant="primary" icon="plus" onClick=${openAddMarket}>Add market</${Button}></div></div>
    <div class="row wrap" style="gap:8px">
      <${Badge} lg tone="accent">${slots.length} markets</${Badge}><${Badge} lg tone="up">${enabled} trading</${Badge}>
      ${slots.length - enabled > 0 && html`<${Badge} lg tone="warn">${slots.length - enabled} paused</${Badge}>`}
      ${inUse.map((n) => html`<${Badge} lg>${strategyLabel(n)}</${Badge}>`)}
    </div>
    <${Tabs} value=${tab} onChange=${setTab} tabs=${[{ value: "coins", label: "Per-coin control", count: slots.length }, { value: "library", label: "Strategy library", count: catalog.filter((c) => c.name !== "combo").length }]} />
    ${tab === "coins" ? (slots.length
      ? html`<div class="grid g-slots">${slots.map((slot) => html`<${SlotCard} key=${slot.tag} slot=${slot} s=${s} stats=${by[slot.tag]} catalog=${catalog} />`)}</div>`
      : s ? html`<${Empty} icon="nodes" title="No per-coin slots" body="This engine runs a single global strategy. Add a market to create per-coin slots." action=${html`<${Button} variant="primary" icon="plus" onClick=${openAddMarket}>Add market</${Button}>`} />`
        : html`<div class="grid g-auto">${[1, 2, 3].map(() => html`<div class="card" style="height:300px"><div class="skeleton" style="height:100%"></div></div>`)}</div>`)
    : html`<${Library} catalog=${catalog} slots=${slots} />`}
    <${Callout} tone="info" title="How live exits work">With an exchange bracket confirmed on Delta, the stop-loss and take-profit close trades — a strategy's own exit signal is ignored unless it opts in. For Donchian that makes “Take-profit × stop distance” the real reward:risk. Test changes in the <${Link} to="/backtest">Backtest Lab</${Link}> first.</${Callout}>
  </div>`;
}
