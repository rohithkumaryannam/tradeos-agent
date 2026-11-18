import { html, useState, useEffect } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Stat, Empty, Callout, CoinIcon, cx, Ago } from "../lib/ui.js";
import { useStore, act, toast } from "../lib/store.js";
import { api, fmtUsd, fmtNum, fmtDur, timeAgo, isNum } from "../lib/util.js";
import { decisionText } from "../lib/widgets.js";
import { modeLabel } from "../app.js";

function Check({ ok, label, detail, icon }) {
  return html`<div class="row" style="gap:12px;padding:12px 0;border-bottom:1px solid var(--border)">
    <div class=${cx("engine-orb", ok === null ? "paused" : ok ? "on" : "off")} style="width:34px;height:34px;border-radius:10px"><${Icon} name=${icon} size=${16} /></div>
    <div class="grow"><b>${label}</b><div class="faint" style="font-size:12.5px">${detail}</div></div>
    <${Badge} tone=${ok === null ? "" : ok ? "up" : "down"} dot>${ok === null ? "n/a" : ok ? "Healthy" : "Problem"}</${Badge}>
  </div>`;
}

export default function System() {
  const st = useStore();
  const s = st.snap;
  const cfg = st.config;
  const [health, setHealth] = useState(null);
  const load = async () => {
    try { const r = await fetch("/healthz"); setHealth(await r.json()); } catch { setHealth(null); }
  };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  const c = health?.checks || {};
  const demo = s?.mode === "DELTA_DEMO";
  const uptime = s?.server?.started_at ? Date.now() / 1000 - s.server.started_at : null;
  const markets = Object.values(s?.markets || {});
  return html`<div class="stack">
    <div class="page-head"><div><h2>System</h2><p>Health of the trading loop, exchange account, database and data feeds.</p></div>
      <div class="row"><a class="btn" href="/docs" target="_blank" rel="noopener"><${Icon} name="external" size=${15} />API docs</a><a class="btn" href="/classic"><${Icon} name="grid" size=${15} />Classic dashboard</a></div></div>

    <div class="kpis">
      <${Stat} label="Status" icon="activity" value=${health ? (health.status === "ok" ? html`<span class="up">Healthy</span>` : html`<span class="down">Degraded</span>`) : "—"} sub=${health?.error || "All checks passing"} />
      <${Stat} label="Last cycle" icon="refresh" value=${s?.health?.last_cycle ? timeAgo(s.health.last_cycle) : "—"} sub=${s?.health?.cycle_ms ? `${s.health.cycle_ms} ms per cycle` : ""} />
      <${Stat} label="Loop interval" icon="clock" value=${s?.engine ? s.engine.poll_seconds + "s" : "—"} sub=${s?.engine ? `${s.engine.lookback} candles · ${s.engine.resolution} base` : ""} />
      <${Stat} label="Uptime" icon="server" value=${isNum(uptime) ? fmtDur(uptime) : "—"} sub=${s?.server?.version ? `build ${s.server.version}` : ""} />
      <${Stat} label="Data stream" icon="wifi" value=${st.ws === "live" ? html`<span class="up">Live</span>` : html`<span class="warn">${st.ws}</span>`} sub=${st.frameGap ? `${(st.frameGap / 1000).toFixed(1)}s between frames` : ""} />
      <${Stat} label="Venue" icon="globe" value=${modeLabel(s?.mode)} sub=${s?.venue || ""} />
    </div>

    <div class="grid g2" style="align-items:start">
      <${Card} title="Health checks" sub="Same checks your uptime monitor sees at /healthz" actions=${html`<${Button} size="sm" variant="ghost" icon="refresh" onClick=${load} />`}>
        <div style="margin-top:-12px">
          <${Check} ok=${c.running ?? null} icon="power" label="Trading loop running" detail=${s?.running ? "The bot evaluates every market each cycle." : "Stopped — start it from the engine menu."} />
          <${Check} ok=${c.loop ?? null} icon="refresh" label="Loop fresh" detail=${health?.last_cycle_at ? `Last successful cycle ${timeAgo(health.last_cycle_at)}` : "No cycle yet"} />
          <${Check} ok=${demo ? c.delta ?? null : null} icon="wallet" label="Delta account" detail=${demo ? (s?.account_status === "live" ? "Balances and positions polling normally." : s?.account_error || "Unavailable") : "Not used in Paper mode"} />
          <${Check} ok=${cfg?.credentials?.mongo ? c.mongo ?? null : null} icon="database" label="MongoDB" detail=${cfg?.credentials?.mongo ? "Trades, state and change history are mirrored." : "Not configured — local files only"} />
          <${Check} ok=${s ? s.execution_enabled !== false : null} icon="lock" label="Execution role" detail=${s?.execution_reason || "—"} />
        </div>
      </${Card}>

      <${Card} title="Delta account" sub=${demo ? "Testnet wallet, polled every ~20 s" : "Paper mode — simulated balance"}
        actions=${demo && html`<${Button} size="sm" icon="refresh" onClick=${() => act(() => api("/api/account/retry", { method: "POST" }), { ok: "Account refresh requested", refresh: false })}>Retry</${Button}>`}>
        ${!demo ? html`<dl class="kv"><dt>Paper equity</dt><dd>${fmtUsd(s?.equity)}</dd><dt>Starting balance</dt><dd>${fmtUsd(s?.start_equity)}</dd></dl>`
          : html`<div class="stack">
            ${s?.account_status !== "live" && html`<${Callout} tone="down" title="Account unavailable">${s?.account_error || "No response from Delta."}
              ${s?.account_error_code === "ip_not_whitelisted_for_api_key" && html`<div style="margin-top:8px">Whitelist <b class="mono">${s.account_client_ip}</b> on the existing API key (Delta Testnet → API Keys → edit). <${Button} size="sm" icon="copy" onClick=${() => { navigator.clipboard?.writeText(s.account_client_ip || ""); toast("IP copied", { tone: "up" }); }}>Copy IP</${Button}></div>`}</${Callout}>`}
            <dl class="kv"><dt>Status</dt><dd><${Badge} tone=${s?.account_status === "live" ? "up" : "down"} dot>${s?.account_status}</${Badge}></dd>
              <dt>Equity (USD)</dt><dd>${fmtUsd(s?.equity)}</dd>${s?.account_failures ? html`<dt>Recent failures</dt><dd class="warn">${s.account_failures}</dd>` : ""}</dl>
            ${(s?.account_balances || []).length > 0 && html`<div class="table-wrap"><table class="t compact"><thead><tr><th>Asset</th><th class="r">Balance</th><th class="r">Available</th></tr></thead>
              <tbody>${s.account_balances.filter((b) => Number(b.balance)).map((b) => html`<tr><td><b>${b.asset}</b></td><td class="r num">${fmtNum(b.balance, 4)}</td><td class="r num">${fmtNum(b.available, 4)}</td></tr>`)}</tbody></table></div>`}
          </div>`}
      </${Card}>
    </div>

    <${Card} title="Market data" sub="Per-market feed freshness and the engine's latest decision" flush>
      ${markets.length ? html`<div class="table-wrap"><table class="t"><thead><tr><th>Market</th><th>Feed</th><th>Last fetch</th><th>Decision</th><th class="hide-sm">Detail</th></tr></thead>
        <tbody>${markets.map((m) => html`<tr><td><div class="row" style="gap:8px"><${CoinIcon} symbol=${m.symbol} size="sm" /><b>${m.symbol}</b></div></td>
          <td>${m.data_status === "fresh" ? html`<${Badge} tone="up" dot>Fresh</${Badge}>` : html`<${Badge} tone="warn" title=${m.data_status}>Stale</${Badge}>`}${m.execution_issue ? html` <${Badge} tone="down" title=${m.execution_issue}>Exec issue</${Badge}>` : ""}</td>
          <td class="muted">${m.last_fetch ? timeAgo(m.last_fetch * 1000) : "—"}</td><td><${Badge}>${m.decision?.event || "—"}</${Badge}></td>
          <td class="muted hide-sm ellipsis" style="max-width:520px" title=${decisionText(m)}>${decisionText(m)}</td></tr>`)}</tbody></table></div>`
        : html`<${Empty} icon="server" title="Waiting for the first cycle" />`}
    </${Card}>
  </div>`;
}
