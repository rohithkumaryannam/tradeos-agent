// TradeOS dashboard — vanilla JS, canvas charts, WebSocket live state.
// Trades multiple symbols concurrently; one set of detail panels (chart, RSI,
// Open Position, AI) follows whichever symbol is "active" (clicked in the
// Markets strip). Equity, risk, trade history and the feed are account-wide.
const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const signed = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + fmt(n));

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let last = null;
let activeSymbol = null;

// ── controls ──
let actionBusy = false;
function notify(message, error = false) {
  $("toastText").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  $("toast").hidden = false;
}
$("toastClose").onclick = () => { $("toast").hidden = true; };
async function post(url) {
  if (actionBusy) return;
  actionBusy = true;
  document.body.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(url, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.detail || `Request failed (${response.status})`);
    const outcomes = data.result ? (data.result.event ? [data.result] : Object.values(data.result)) : [];
    const failed = outcomes.filter(o => ["reject", "error"].includes(o.event));
    notify(failed.length ? failed.map(o => o.detail).join(" · ") : (outcomes[0]?.detail || "Control updated"), !!failed.length);
    const state = await fetch("/api/state");
    if (state.ok) { last = await state.json(); render(last); }
  } catch (e) { notify(e.message || "Connection failed", true); }
  finally { actionBusy = false; document.body.removeAttribute("aria-busy"); }
}
$("evaluateBtn").onclick = () => post("/api/evaluate");
$("pauseBtn").onclick = () => post("/api/entries/" + (last?.entries_paused ? "resume" : "pause"));
$("tradeFilter").onchange = () => renderTrades(last?.trades);
$("exportBtn").onclick = () => {
  const trades = (last?.trades || []).filter(t => !$("tradeFilter").value || t.symbol === $("tradeFilter").value);
  if (!trades.length) return notify("No completed trades to export.");
  const fields = ["entry_ts", "exit_ts", "symbol", "strategy", "side", "entry", "exit", "size", "pnl", "r", "reason"];
  const cell = v => '"' + String(typeof v === "string" && /^[=+@\-\t\r]/.test(v) ? "'" + v : v ?? "").replaceAll('"', '""') + '"';
  const csv = [fields.join(","), ...trades.map(t => fields.map(k => cell(t[k])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], {type:"text/csv;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url; link.download = "tradeos-journal.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const riskDialog = $("riskDialog");
$("riskControlsBtn").onclick = () => {
  if (!last?.risk) return;
  const r = last.risk;
  $("riskPortfolio").value = r.max_portfolio_risk_pct;
  $("riskPositions").value = r.max_open_positions;
  $("riskDirection").value = r.max_same_direction_positions;
  $("riskLeverage").value = r.max_leverage;
  $("riskDaily").value = r.max_daily_loss_pct;
  $("strategyRiskFields").innerHTML = Object.entries(r.strategy_risk_pct || {}).map(([name, value]) =>
    `<label class="risk-field">${esc(STRAT_SHORT[name] || name)}<input data-strategy="${esc(name)}" type="number" min="0.05" max="5" step="0.05" value="${esc(value)}" required></label>`
  ).join("");
  riskDialog.showModal();
};
$("riskCancel").onclick = () => riskDialog.close();
$("riskForm").onsubmit = async (event) => {
  event.preventDefault();
  if (actionBusy) return;
  const strategyCaps = {};
  $("strategyRiskFields").querySelectorAll("input[data-strategy]").forEach(input => {
    strategyCaps[input.dataset.strategy] = Number(input.value);
  });
  const payload = {
    max_portfolio_risk_pct: Number($("riskPortfolio").value),
    max_open_positions: Number($("riskPositions").value),
    max_same_direction_positions: Number($("riskDirection").value),
    max_leverage: Number($("riskLeverage").value),
    max_daily_loss_pct: Number($("riskDaily").value),
    strategy_risk_pct: strategyCaps,
  };
  actionBusy = true;
  try {
    const response = await fetch("/api/risk", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    riskDialog.close();
    notify("Risk controls applied to new entries.");
    const state = await fetch("/api/state");
    if (state.ok) { last = await state.json(); render(last); }
  } catch (error) {
    notify(error.message || "Could not update risk controls", true);
  } finally {
    actionBusy = false;
  }
};
function qs(sym) { return sym ? "?symbol=" + encodeURIComponent(sym) : ""; }
$("startBtn").onclick = () => post("/api/bot/start");
$("stopBtn").onclick = () => post("/api/bot/stop");
$("confirmBtn").onclick = () => post("/api/signal/confirm" + qs(activeSymbol));
$("dismissBtn").onclick = () => post("/api/signal/dismiss" + qs(activeSymbol));
$("closeBtn").onclick = () => { if (confirm(`Close the open ${activeSymbol} position at market?`)) post("/api/position/close" + qs(activeSymbol)); };
let aiBusy = false;
$("aiBtn").onclick = async () => {
  if (aiBusy) return;
  aiBusy = true;
  const out = $("aiText"), btn = $("aiBtn");
  btn.disabled = true; out.className = "mut"; out.textContent = "Analyzing…";
  try {
    const r = await fetch("/api/ai/analysis" + qs(activeSymbol), { method: "POST" });
    const d = await r.json();
    out.textContent = d.text || "No response.";
    out.className = d.available === false ? "warn" : "";
    $("aiModel").textContent = d.model ? "— " + d.model : "";
  } catch (e) { out.textContent = "Request failed."; out.className = "down"; }
  aiBusy = false;
  btn.disabled = !(last && last.ai_available);
};

// ── Jarvis: voice/chat command layer ──
// Browser-side speech only (the server has no mic/speaker) — Web Speech API,
// free and built into Chrome, no extra dependency. The LLM only ever
// PROPOSES an action (see /api/ai/command); nothing executes until the user
// explicitly clicks Confirm, which calls /api/ai/execute.
(function () {
  const jbtn = $("jarvisBtn"), jbox = $("jarvisBox"), jheard = $("jarvisHeard"),
        jreply = $("jarvisReply"), jconfirmBox = $("jarvisConfirm"),
        jconfirmBtn = $("jarvisConfirmBtn"), jcancelBtn = $("jarvisCancelBtn");
  if (!jbtn) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let pendingAction = null, recognizing = false, recognizer = null;

  function speak(text) {
    if (!synth || !text) return;
    try {
      synth.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.05;
      synth.speak(u);
    } catch (e) { /* speech synthesis is a nice-to-have, never block on it */ }
  }

  function describeAction(action) {
    if (!action) return "";
    const a = action.args || {};
    switch (action.name) {
      case "close_position": return `Close the ${esc(a.symbol)} position`;
      case "set_mode": return `Switch mode to ${esc(a.mode)}`;
      case "set_auto": return a.enabled ? "Turn auto-trading ON" : "Turn auto-trading OFF";
      case "pause_entries": return a.paused ? "Pause new entries" : "Resume new entries";
      case "set_strategy_risk_pct": return `Set ${esc(a.strategy)}'s risk per trade to ${esc(a.risk_pct)}%`;
      case "start_bot": return "Start the bot";
      case "stop_bot": return "Stop the bot";
      default: return action.name;
    }
  }

  async function sendToJarvis(text) {
    jbox.hidden = false;
    jheard.textContent = `"${text}"`;
    jreply.textContent = "…";
    jconfirmBox.hidden = true;
    pendingAction = null;
    try {
      const r = await fetch("/api/ai/command", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const d = await r.json();
      jreply.textContent = d.reply || "No response.";
      speak(d.reply);
      if (d.action) {
        pendingAction = d.action;
        jconfirmBtn.textContent = "Confirm: " + describeAction(d.action);
        jconfirmBox.hidden = false;
      }
    } catch (e) {
      jreply.textContent = "Request failed.";
    }
  }

  jconfirmBtn.onclick = async () => {
    if (!pendingAction) return;
    const action = pendingAction;
    pendingAction = null;
    jconfirmBox.hidden = true;
    jreply.textContent = "Working…";
    try {
      const r = await fetch("/api/ai/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(action),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "failed");
      const msg = "Done — " + describeAction(action) + ".";
      jreply.textContent = msg;
      speak(msg);
      const state = await fetch("/api/state");
      if (state.ok) { last = await state.json(); render(last); }
    } catch (e) {
      jreply.textContent = "Failed: " + (e.message || "unknown error");
      speak("That failed.");
    }
  };
  jcancelBtn.onclick = () => { pendingAction = null; jconfirmBox.hidden = true; jreply.textContent += " (cancelled)"; };

  if (!SR) {
    jbtn.title = "Voice input isn't supported in this browser — try Chrome.";
    jbtn.onclick = () => notify("Voice input needs Chrome or Edge on this device.", true);
    return;
  }
  recognizer = new SR();
  recognizer.lang = "en-US";
  recognizer.interimResults = false;
  recognizer.maxAlternatives = 1;
  recognizer.onresult = (e) => {
    const text = e.results[0][0].transcript;
    sendToJarvis(text);
  };
  recognizer.onerror = () => { recognizing = false; jbtn.classList.remove("listening"); };
  recognizer.onend = () => { recognizing = false; jbtn.classList.remove("listening"); };
  jbtn.onclick = () => {
    if (recognizing) { recognizer.stop(); return; }
    recognizing = true;
    jbtn.classList.add("listening");
    jbox.hidden = false;
    jheard.textContent = "Listening…";
    jreply.textContent = "";
    jconfirmBox.hidden = true;
    try { recognizer.start(); } catch (e) { recognizing = false; jbtn.classList.remove("listening"); }
  };
})();

document.querySelectorAll("#modeToggle button").forEach(b =>
  b.onclick = () => post("/api/mode/" + b.dataset.mode));
document.querySelectorAll("#autoToggle button").forEach(b =>
  b.onclick = () => post("/api/auto/" + b.dataset.auto));
$("stratSelect").onchange = (e) => post("/api/strategy/" + e.target.value);

// ── websocket with reconnect + feed-health tracking ──
let sock = null, lastFrame = 0, wsOpen = false;
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  sock = new WebSocket(`${proto}://${location.host}/ws`);
  sock.onopen = () => { wsOpen = true; };
  sock.onmessage = (e) => { try { last = JSON.parse(e.data); render(last); lastFrame = Date.now(); } catch (error) { notify("Dashboard update failed: " + error.message, true); } };
  sock.onclose = () => { wsOpen = false; updateFeedState(); setTimeout(connect, 1500); };
  sock.onerror = () => sock.close();
}
connect();

// Honest connection/staleness indicator: snapshots push every ~1s, so no frame
// for >5s means the feed is stale; a closed socket means offline.
function updateFeedState() {
  const dot = $("feed-dot"), lab = $("feedState");
  if (!dot || !lab) return;
  if (!wsOpen) { dot.className = "dot"; lab.textContent = "OFFLINE"; lab.className = "down"; return; }
  const age = Date.now() - lastFrame;
  if (!lastFrame || age > 5000) {
    dot.className = "dot";
    lab.textContent = lastFrame ? `STALE ${Math.round(age / 1000)}s` : "WAITING";
    lab.className = "warn";
  } else {
    dot.className = "dot on";
    lab.textContent = "LIVE";
    lab.className = "up";
  }
}
setInterval(updateFeedState, 1000);

// ── markets strip ──
function renderMarketsStrip(s) {
  const syms = s.symbols || [];
  const strip = $("marketsStrip");
  const positionsKnown = s.mode !== "DELTA_DEMO" || s.account_status === "live";
  strip.innerHTML = syms.map(sym => {
    const mk = (s.markets && s.markets[sym]) || {};
    const pos = mk.position;
    const posLine = pos
      ? `<span class="${pos.side === "long" ? "up" : "down"}">${pos.side.toUpperCase()} ${pos.size}c</span> ${signed(pos.unrealized)}`
      : positionsKnown ? "flat" : '<span class="warn">position unknown</span>';
    return `<button type="button" aria-pressed="${sym === activeSymbol}" class="mkt${sym === activeSymbol ? " active" : ""}" data-sym="${esc(sym)}">
      <div class="sym">${esc(sym)}${pos ? ' <span class="badge ' + (pos.side === "long" ? "up" : "down") + '">' + pos.side.toUpperCase() + "</span>" : ""}</div>
      <div class="px">${fmt(mk.price, 1)}</div>
      <div class="posline">${posLine}</div><div class="data-label ${mk.data_status === "fresh" ? "up" : "warn"}">${mk.data_status === "fresh" ? "● Fresh candles" : "○ Data not ready"}</div>
    </button>`;
  }).join("");
  strip.querySelectorAll(".mkt").forEach(el =>
    el.onclick = () => { activeSymbol = el.dataset.sym; if (last) render(last); });
}

// ── render ──
function render(s) {
  if (!activeSymbol || !(s.markets && s.markets[activeSymbol])) {
    activeSymbol = (s.symbols && s.symbols[0]) || s.symbol || null;
  }
  const mk = (s.markets && s.markets[activeSymbol]) || {};

  $("running").textContent = s.running ? (s.execution_enabled ? "RUNNING" : "STANDBY") : "STOPPED";
  $("status-dot").className = "dot" + (s.running ? " on" : "");
  $("mode").textContent = s.mode;
  $("execmode").textContent = s.auto ? "AUTO" : "MANUAL";
  $("sympill").textContent = `${(s.symbols || []).join(", ")} · ${s.resolution}`;
  const sel = $("stratSelect");
  if (document.activeElement !== sel) {
    const opts = (s.strategies || [s.strategy]).join(",");
    if (sel.dataset.opts !== opts) {
      sel.innerHTML = (s.strategies || [s.strategy]).map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
      sel.dataset.opts = opts;
    }
    sel.value = s.strategy;
  }
  const positionsKnown = s.mode !== "DELTA_DEMO" || s.account_status === "live";
  sel.disabled = !positionsKnown;
  sel.title = positionsKnown ? "Active strategy" : "Strategy changes are locked until Delta positions can be verified";
  $("venueState").textContent = s.venue || "—";
  if (!aiBusy) $("aiBtn").disabled = !s.ai_available;
  if (!s.ai_available) $("aiModel").textContent = "— key not set";
  $("startBtn").disabled = s.running;
  $("stopBtn").disabled = !s.running;

  document.querySelectorAll("#modeToggle button").forEach(b =>
    b.classList.toggle("sel", b.dataset.mode === s.mode));
  document.querySelectorAll("#autoToggle button").forEach(b =>
    b.classList.toggle("sel", (b.dataset.auto === "true") === s.auto));

  document.querySelectorAll(".research-select").forEach(button => {
    button.disabled = !positionsKnown || Object.values(s.markets || {}).some(m => m.position) || s.strategy === button.dataset.strategy;
    button.title = !positionsKnown ? "Locked until Delta positions can be verified"
      : button.disabled ? "Requires flat positions; selected strategy stays active"
      : "Changes strategy and timeframe; execution mode stays the same";
  });
  renderMarketsStrip(s);
  $("chartTitle").textContent = activeSymbol ? "Delta " + activeSymbol : "Delta";
  $("rsiSym").textContent = activeSymbol ? "— " + activeSymbol : "";
  $("posSym").textContent = activeSymbol ? "— " + activeSymbol : "";
  $("aiSym").textContent = activeSymbol ? "— " + activeSymbol + " " : "";

  $("equity").textContent = s.equity == null ? "—" : "$" + fmt(s.equity);
  const es = $("equitySrc");
  if (s.equity_status === "live") { es.textContent = `Delta testnet · ${s.equity_asset} (live)`; es.className = "src up"; }
  else if (s.equity_status === "unavailable") { es.textContent = "balance unavailable"; es.className = "src down"; }
  else { es.textContent = "simulated (paper)"; es.className = "src"; }
  const tp = $("todaypnl"); tp.textContent = "$" + signed(s.realized_today);
  tp.className = "num " + (s.realized_today >= 0 ? "up" : "down");

  // aggregate open P&L across every market
  const allPos = Object.values(s.markets || {}).map(m => m.position).filter(Boolean);
  if (allPos.length) {
    const sum = allPos.reduce((a, p) => a + (p.unrealized || 0), 0);
    const op = $("openpnl"); op.textContent = "$" + signed(sum);
    op.className = "num " + (sum >= 0 ? "up" : "down");
  } else {
    $("openpnl").textContent = "—"; $("openpnl").className = "num";
  }

  // pending (active symbol)
  const pb = $("pendingBox");
  if (mk.pending) {
    pb.classList.add("show");
    $("pendSym").textContent = activeSymbol;
    $("pendAct").textContent = mk.pending.action;
    $("pendReason").textContent = mk.pending.reason;
  } else pb.classList.remove("show");

  // open position (active symbol)
  if (mk.position) {
    $("posEmpty").style.display = "none";
    $("posBox").style.display = "block";
    const p = mk.position;
    $("pSide").textContent = p.side.toUpperCase();
    $("pSide").className = "v " + (p.side === "long" ? "up" : "down");
    $("pEntry").textContent = fmt(p.entry, 1);
    $("pSize").textContent = p.size + " contracts";
    $("pSL").textContent = fmt(p.sl, 1);
    $("pTP").textContent = fmt(p.tp, 1);
    $("pNot").textContent = "$" + fmt(p.notional);
    const u = $("pUnr"); u.textContent = "$" + signed(p.unrealized);
    u.className = "v " + ((p.unrealized || 0) >= 0 ? "up" : "down");
    const prot = $("pProt"), pr = p.protection || "unknown";
    prot.textContent = pr === "exchange" ? "✓ SL/TP on exchange"
      : pr === "local" ? "⚠ SL/TP enforced (local only)"
      : pr === "unverified" ? "⚠ unverified on exchange"
      : pr === "none" ? "⚠ NONE" : pr;
    prot.className = "v " + (pr === "exchange" ? "up" : pr === "local" ? "warn" : "down");
    $("pRecon").textContent = p.reconciliation || "—";
  } else {
    $("posEmpty").style.display = "block";
    $("posEmpty").textContent = positionsKnown
      ? "Flat — no open position"
      : "Position state unknown — waiting for Delta account access. Existing exchange protection continues independently.";
    $("posBox").style.display = "none";
  }

  // risk (shared across all symbols)
  const r = s.risk;
  const strategyCaps = Object.values(r.strategy_risk_pct || {});
  $("rRisk").textContent = strategyCaps.length
    ? `${Math.min(...strategyCaps)}–${Math.max(...strategyCaps)}% by strategy`
    : r.risk_per_trade_pct + "%";
  $("rSLTP").textContent = r.stop_loss_pct + "% / " + r.take_profit_pct + "%";
  $("rLev").textContent = r.max_leverage + "x";
  $("rCap").textContent = r.max_daily_loss_pct + "%";
  const co = $("rCapOk");
  co.textContent = s.daily_cap_ok && s.entry_risk_ok ? "OK" : "NEW ENTRIES BLOCKED";
  co.title = s.entry_risk_reason || s.daily_cap_reason || "";
  co.className = "v " + (s.daily_cap_ok && s.entry_risk_ok ? "up" : "down");

  const p = s.portfolio || {};
  $("grossExposure").textContent = p.known === false ? "—" : "$" + fmt(p.notional);
  $("exposureSrc").textContent = p.known === false ? "Position state unavailable"
    : `${p.positions || 0} positions · ${fmt(p.leverage, 2)}× gross leverage`;
  const riskBudget = $("riskBudget");
  riskBudget.textContent = p.known === false ? "—" : `${fmt(p.risk_pct)}% / ${r.max_portfolio_risk_pct}%`;
  riskBudget.className = "v " + (p.known !== false && p.risk_pct >= r.max_portfolio_risk_pct ? "down" : "");
  $("riskProgress").value = Math.min(100, (p.risk_pct || 0) / r.max_portfolio_risk_pct * 100);
  $("leverageBudget").textContent = p.known === false ? "—" : `${fmt(p.leverage)}× / ${r.max_leverage}×`;
  $("leverageProgress").value = Math.min(100, (p.leverage || 0) / r.max_leverage * 100);
  $("dailyBudget").textContent = p.known === false ? "—" : "$" + fmt(p.daily_loss_remaining);
  $("positionSlots").textContent = p.known === false ? "—" : `${p.positions || 0} / ${r.max_open_positions}`;
  $("pauseBtn").textContent = s.entries_paused ? "Resume new entries" : "Pause new entries";
  $("pauseBanner").hidden = !s.entries_paused;
  const ipBanner = $("ipBanner");
  if (s.mode === "DELTA_DEMO" && s.account_status === "unavailable" && s.account_error_code === "ip_not_whitelisted_for_api_key") {
    ipBanner.hidden = false;
    ipBanner.innerHTML = `Delta rejected this server's IP — new orders are blocked. Delta Testnet → API Keys → edit the existing key → whitelist <strong>${esc(s.account_client_ip)}</strong>. <button id="ipBannerBtn" class="ghost">Open account panel</button>`;
    $("ipBannerBtn").onclick = () => document.getElementById("accountDialog").showModal();
  } else {
    ipBanner.hidden = true;
  }
  $("cycleTime").textContent = s.health?.last_cycle ? `${s.health.last_cycle.slice(11,19)} UTC · ${s.health.cycle_ms} ms` : "Awaiting first cycle";
  $("marketHealth").innerHTML = Object.entries(s.markets || {}).map(([symbol, m]) => {
    const issue = !positionsKnown ? "Delta position state unavailable; new orders blocked"
      : m.execution_issue || (m.data_status !== "fresh" ? m.data_status : null);
    return `<div class="health-item"><strong><span>${esc(symbol)}</span><span class="${issue ? "warn" : "up"}">${issue ? "ATTENTION" : "DATA READY"}</span></strong><p>${esc(issue || m.decision?.detail || "Ready for evaluation")}</p></div>`;
  }).join("");
  const filter = $("tradeFilter");
  const symbols = s.symbols || [];
  if (filter.dataset.symbols !== symbols.join(",")) {
    const selected = filter.value;
    filter.innerHTML = '<option value="">All markets</option>' + symbols.map(sym => `<option value="${esc(sym)}">${esc(sym)}</option>`).join("");
    filter.value = symbols.includes(selected) ? selected : "";
    filter.dataset.symbols = symbols.join(",");
  }
  renderAccount(s);
  renderFeed(s.feed);
  renderAnalytics(s.analytics);
  renderStrategyPerf(s.analytics?.by_strategy, s.risk?.strategy_risk_pct, s.active_strategies);
  renderTrades(s.trades);
  const symTrades = (s.trades || []).filter(t => t.symbol === activeSymbol);
  drawPriceChart(mk.chart, mk.position, symTrades);
  drawRSI(mk.chart);
  drawEquity(s.equity_curve);
}

function renderAccount(s) {
  const st = $("acctState"), body = $("acctBody");
  if (s.mode !== "DELTA_DEMO") {
    st.textContent = ""; st.className = "sub";
    body.className = "mut"; body.style.padding = "8px 0";
    body.textContent = "Paper mode — no exchange account. Switch to Delta Demo.";
    return;
  }
  if (s.account_status === "unavailable") {
    st.textContent = "— recovering"; st.className = "sub warn";
    body.className = "warn";
    const retry = Math.max(0, Math.ceil((Number(s.account_retry_at || 0) * 1000 - Date.now()) / 1000));
    const ipHelp = s.account_error_code === "ip_not_whitelisted_for_api_key"
      ? `<div class="mut" style="margin-top:8px"><strong>Keep the same API key.</strong> Delta Testnet → API Keys → edit the existing key → whitelist <strong>${esc(s.account_client_ip)}</strong>. For a permanent setup, run TradeOS from one static public IP.</div>` : "";
    body.innerHTML = `<strong>New Delta orders are blocked safely.</strong>
      <div class="mut" style="margin-top:6px">${esc(s.account_error || "Delta account connection failed.")}</div>
      ${ipHelp}
      <div class="row" style="border:0;padding:12px 0 0"><span class="mut">Auto retry ${retry ? `in ${retry}s` : "due now"} · attempt ${esc(s.account_failures || 1)}</span>
      <button id="accountRetryBtn" class="ghost">Retry now</button></div>`;
    $("accountRetryBtn").onclick = () => post("/api/account/retry");
    return;
  }
  st.textContent = "— testnet (live)"; st.className = "sub up";
  body.className = ""; body.style.padding = "0";
  const bals = (s.account_balances || []).filter(b => (b.balance || 0) > 0);
  let html = `<div style="font-size:10.5px;letter-spacing:1px;color:var(--faint);text-transform:uppercase;margin:4px 0 6px">Wallet balances</div>`;
  html += bals.length ? bals.map(b =>
    `<div class="row"><span class="k">${esc(b.asset)}</span><span class="v">${fmt(b.balance, 4)}<span class="mut"> avail ${fmt(b.available, 4)}</span></span></div>`
  ).join("") : `<div class="mut" style="padding:6px 0">No non-zero balances.</div>`;

  const pos = s.exchange_positions || [];
  html += `<div style="font-size:10.5px;letter-spacing:1px;color:var(--faint);text-transform:uppercase;margin:12px 0 6px">Exchange positions</div>`;
  html += pos.length ? pos.map(p =>
    `<div class="row"><span class="k">${esc(p.symbol)}</span><span class="v">${esc(p.size)} @ ${fmt(p.entry, 1)}${p.unrealized != null ? ` <span class="${p.unrealized >= 0 ? "up" : "down"}">${signed(p.unrealized)}</span>` : ""}</span></div>`
  ).join("") : `<div class="mut" style="padding:6px 0">Flat — no open positions on the exchange.</div>`;
  body.innerHTML = html;
}

function renderFeed(feed) {
  $("feed").innerHTML = (feed || []).map(f =>
    `<div><span class="tag ${esc(f.event)}">${esc(f.event)}</span>
     <span class="mut">${esc((f.t || "").slice(11, 19))}</span>
     <span>${esc(f.detail)}</span></div>`).join("");
}

function dur(s) {
  if (s == null) return "—";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  return (s / 3600).toFixed(1) + "h";
}

function renderAnalytics(a) {
  a = a || {};
  $("perfSub").textContent = a.trades ? `— ${a.trades} trades (all markets)` : "— no trades yet";
  const pf = a.profit_factor == null ? "—" : a.profit_factor.toFixed(2);
  const tiles = [
    ["Win rate", a.win_rate == null ? "—" : a.win_rate + "%", a.win_rate >= 50 ? "up" : (a.win_rate == null ? "" : "down")],
    ["Net P&L", a.trades ? signed(a.total_pnl) : "—", (a.total_pnl || 0) >= 0 ? "up" : "down"],
    ["Profit factor", pf, (a.profit_factor >= 1 || a.profit_factor == null) ? "" : "down"],
    ["Avg R", a.avg_r == null ? "—" : (a.avg_r >= 0 ? "+" : "") + a.avg_r + "R", (a.avg_r || 0) >= 0 ? "up" : "down"],
    ["W / L", a.trades ? `${a.wins} / ${a.losses}` : "—", ""],
    ["Avg win", a.avg_win == null ? "—" : signed(a.avg_win), "up"],
    ["Avg loss", a.avg_loss == null ? "—" : signed(a.avg_loss), "down"],
    ["Best / Worst", a.trades ? `${signed(a.best)} / ${signed(a.worst)}` : "—", ""],
  ];
  $("perfGrid").innerHTML = tiles.map(([l, v, c]) =>
    `<div class="perf"><div class="l">${l}</div><div class="n ${c || ""}">${v}</div></div>`).join("");
}

const STRAT_SHORT = { ema_crossover: "EMA 9/20", donchian_breakout: "Donchian breakout", btc_trend_pullback: "Trend pullback", btc_bms: "BTC BMS", mean_reversion: "Mean reversion", time_series_momentum: "Time-series momentum", combo: "combo" };

function renderStrategyPerf(byStrategy, riskCaps, activeStrategies) {
  byStrategy = byStrategy || {}; riskCaps = riskCaps || {};
  const active = new Set(activeStrategies || []);
  const names = [...new Set([...Object.keys(riskCaps), ...Object.keys(byStrategy)])];
  const rows = names.map(name => [name, byStrategy[name] || { trades: 0, win_rate: null, total_pnl: 0, profit_factor: null, avg_r: null }])
    .sort((a, b) => b[1].trades - a[1].trades);
  $("stratPerf").querySelector("tbody").innerHTML = rows.length ? rows.map(([name, a]) => {
    const cls = a.total_pnl >= 0 ? "up" : "down";
    return `<tr>
      <td title="${esc(name)}">${esc(STRAT_SHORT[name] || name)}${active.has(name) ? ' <span class="up">✓ ACTIVE</span>' : ""}</td>
      <td>${riskCaps[name] == null ? "—" : fmt(riskCaps[name], 2) + "%"}</td>
      <td>${a.trades}</td>
      <td>${a.win_rate == null ? "—" : a.win_rate + "%"}</td>
      <td class="${a.trades ? cls : ""}">${a.trades ? signed(a.total_pnl) : "—"}</td>
      <td>${a.profit_factor == null ? "—" : a.profit_factor.toFixed(2)}</td>
      <td>${a.avg_r == null ? "—" : (a.avg_r >= 0 ? "+" : "") + a.avg_r + "R"}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="7">No trades yet.</td></tr>';
}
function renderTrades(trades) {
  trades = (trades || []).filter(t => !$("tradeFilter").value || t.symbol === $("tradeFilter").value);
  const tagCls = { TP: "entry", SL: "reject", manual: "mode", strategy: "exit", backtest: "bot" };
  $("trades").querySelector("tbody").innerHTML = (trades && trades.length ? [...trades].reverse() : []).map(t => {
    const cls = t.win ? "up" : "down";
    const tag = tagCls[t.reason] || "mode";
    const strat = STRAT_SHORT[t.strategy] || t.strategy || "—";
    return `<tr>
      <td>${esc((t.exit_ts || "").slice(11, 19))}</td>
      <td>${esc(t.symbol || "—")}</td>
      <td title="${esc(t.strategy || "")}">${esc(strat)}</td>
      <td class="${t.side === "long" ? "up" : "down"}">${esc((t.side || "").toUpperCase())}</td>
      <td>${fmt(t.entry, 1)}</td>
      <td>${fmt(t.exit, 1)}</td>
      <td>${t.size}</td>
      <td class="${cls}">${signed(t.pnl)}</td>
      <td class="${cls}">${t.r == null ? "—" : (t.r >= 0 ? "+" : "") + t.r + "R"}</td>
      <td class="mut">${dur(t.duration_s)}</td>
      <td><span class="tag ${tag}">${esc(t.reason)}</span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="11" class="mut">No trades yet</td></tr>`;
}

// ── charts ──
function setup(cv) {
  const dpr = window.devicePixelRatio || 1;
  if (!cv.dataset.baseH) cv.dataset.baseH = cv.height;
  const w = cv.clientWidth, h = +cv.dataset.baseH;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function drawPriceChart(chart, position, trades) {
  const cv = $("priceChart"); const { ctx, w, h } = setup(cv);
  if (!chart || !chart.length) { ctx.fillStyle = "#8294b4"; ctx.font = "12px system-ui"; ctx.textAlign = "center"; ctx.fillText("Run a cycle to load market data", w / 2, h / 2); return; }
  const pad = 10;
  // Percentile-clipped range (not raw min/max): a single outlier wick — or a
  // far-off liquidation price on a leveraged position — used to stretch the
  // whole y-axis so badly that normal price action elsewhere in the window
  // looked like a flat line by comparison. Clip the extreme 2% of wicks so
  // the visible candles stay readable; SL/TP (normally close to price) still
  // widen the range if needed, but liquidation (often 20-30%+ away) no
  // longer forces the whole chart to rescale around it.
  const sortedLows = chart.map(c => c.l).sort((a, b) => a - b);
  const sortedHighs = chart.map(c => c.h).sort((a, b) => a - b);
  const pct = (arr, p) => arr[Math.max(0, Math.min(arr.length - 1, Math.round((arr.length - 1) * p)))];
  const lo0 = pct(sortedLows, 0.02), hi0 = pct(sortedHighs, 0.98);
  const vals = [lo0, hi0];
  if (position) {
    [position.sl, position.tp].forEach(v => { if (v != null) vals.push(v); });
  }
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const rng = (hi - lo) || 1;
  const y = (p) => pad + (hi - p) / rng * (h - 2 * pad);
  const n = chart.length, cw = w / n;
  const xOf = (i) => i * cw + cw / 2;

  // candles
  chart.forEach((c, i) => {
    const x = xOf(i);
    const up = c.c >= c.o;
    ctx.strokeStyle = up ? "#19d08b" : "#ff4d5e";
    ctx.fillStyle = up ? "#19d08b" : "#ff4d5e";
    ctx.beginPath(); ctx.moveTo(x, y(c.h)); ctx.lineTo(x, y(c.l)); ctx.stroke();
    const bw = Math.max(1, cw * 0.6);
    const yo = y(c.o), yc = y(c.c);
    ctx.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo)));
  });

  // EMA lines
  const line = (key, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
    let started = false;
    chart.forEach((c, i) => {
      if (c[key] == null) return;
      const x = xOf(i);
      if (!started) { ctx.moveTo(x, y(c[key])); started = true; } else ctx.lineTo(x, y(c[key]));
    });
    ctx.stroke(); ctx.lineWidth = 1;
  };
  line("ema_fast", "#3b82f6");
  line("ema_slow", "#f0b90b");

  // SL/TP/entry/liquidation lines for the open position — styled like
  // Delta's own chart: a dashed line across the full width with a colored
  // pill tag (price + size + live $ for the entry line) near the right edge.
  const hline = (price, color, text, dash) => {
    if (price == null) return;
    const yy = y(price);
    ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.setLineDash(dash || [4, 3]);
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "bold 10px monospace";
    const tw = ctx.measureText(text).width;
    const bx = w - tw - 18, by = yy - 8;
    ctx.fillStyle = color; ctx.fillRect(bx, by, tw + 10, 16);
    ctx.fillStyle = "#0a0e17"; ctx.textAlign = "left";
    ctx.fillText(text, bx + 5, by + 11);
  };
  if (position) {
    const pnlTxt = position.unrealized != null ? ` ${signed(position.unrealized)}` : "";
    hline(position.entry, "#8294b4", `${position.size} ${fmt(position.entry, 1)}${pnlTxt}`);
    hline(position.sl, "#ff4d5e", "SL " + fmt(position.sl, 1));
    hline(position.tp, "#19d08b", "TP " + fmt(position.tp, 1));
    hline(position.liquidation, "#f7b733", "Liq " + fmt(position.liquidation, 1), [2, 2]);
  }

  // trade entry/exit markers — map each trade's timestamp to the nearest
  // visible candle so markers land even if the trade predates this window
  const idxForTs = (iso) => {
    const t = Date.parse(iso) / 1000;
    if (!isFinite(t) || t < chart[0].t || t > chart[chart.length - 1].t) return null;
    let best = 0, bestDiff = Infinity;
    chart.forEach((c, i) => {
      const d = Math.abs((c.t || 0) - t);
      if (d < bestDiff) { bestDiff = d; best = i; }
    });
    return best;
  };
  const entryTriangle = (x, yy, side) => {
    ctx.beginPath();
    if (side === "long") { ctx.moveTo(x, yy + 7); ctx.lineTo(x - 5, yy + 14); ctx.lineTo(x + 5, yy + 14); }
    else { ctx.moveTo(x, yy - 7); ctx.lineTo(x - 5, yy - 14); ctx.lineTo(x + 5, yy - 14); }
    ctx.closePath(); ctx.fill();
  };
  (trades || []).slice(-20).forEach(t => {
    const ei = idxForTs(t.entry_ts);
    if (ei != null) {
      ctx.fillStyle = t.side === "long" ? "#19d08b" : "#ff4d5e";
      entryTriangle(xOf(ei), y(t.entry), t.side);
    }
    const xi = idxForTs(t.exit_ts);
    if (xi != null) {
      const x = xOf(xi), yy = y(t.exit);
      ctx.fillStyle = t.win ? "#19d08b" : "#ff4d5e";
      ctx.beginPath(); ctx.arc(x, yy, 4, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#0a0e17"; ctx.lineWidth = 1.5; ctx.stroke();
    }
  });
  // the currently open position's own entry marker (not yet in trade history)
  if (position && position.entry_ts) {
    const ei = idxForTs(position.entry_ts);
    if (ei != null) {
      ctx.fillStyle = position.side === "long" ? "#19d08b" : "#ff4d5e";
      entryTriangle(xOf(ei), y(position.entry), position.side);
    }
  }

  // last price label
  const lastC = chart[chart.length - 1];
  const ly = Math.max(14, Math.min(h - 4, y(lastC.c) - 6));
  ctx.font = "11px monospace"; ctx.textAlign = "right";
  const label = fmt(lastC.c, 1);
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = "#0a0e17"; ctx.fillRect(w - tw - 10, ly - 11, tw + 8, 15);
  ctx.fillStyle = "#e6edf7"; ctx.fillText(label, w - 6, ly);
}

function drawRSI(chart) {
  const cv = $("rsi"); const { ctx, w, h } = setup(cv);
  if (!chart || !chart.length) { ctx.fillStyle = "#8294b4"; ctx.font = "12px system-ui"; ctx.textAlign = "center"; ctx.fillText("Run a cycle to load market data", w / 2, h / 2); return; }
  const y = (v) => 4 + (100 - v) / 100 * (h - 8);
  [30, 50, 70].forEach(lv => {
    ctx.strokeStyle = lv === 50 ? "#243150" : "#1c2740";
    ctx.beginPath(); ctx.moveTo(0, y(lv)); ctx.lineTo(w, y(lv)); ctx.stroke();
  });
  const cw = w / chart.length;
  ctx.strokeStyle = "#8b7cff"; ctx.lineWidth = 1.5; ctx.beginPath();
  let started = false;
  chart.forEach((c, i) => {
    if (c.rsi == null) return;
    const x = i * cw + cw / 2;
    if (!started) { ctx.moveTo(x, y(c.rsi)); started = true; } else ctx.lineTo(x, y(c.rsi));
  });
  ctx.stroke(); ctx.lineWidth = 1;
}

function drawEquity(curve) {
  const cv = $("eq"); const { ctx, w, h } = setup(cv);
  if (!curve || curve.length < 2) { ctx.fillStyle = "#8294b4"; ctx.font = "12px system-ui"; ctx.textAlign = "center"; ctx.fillText("Realized equity appears after completed trades", w / 2, h / 2); return; }
  const vals = curve.map(p => p.equity);
  const lo = Math.min(...vals), hi = Math.max(...vals), rng = (hi - lo) || 1;
  const pad = 6, y = (v) => pad + (hi - v) / rng * (h - 2 * pad);
  const cw = w / (curve.length - 1);
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#19d08b55"); grad.addColorStop(1, "#19d08b00");
  ctx.beginPath(); ctx.moveTo(0, h);
  curve.forEach((p, i) => ctx.lineTo(i * cw, y(p.equity)));
  ctx.lineTo((curve.length - 1) * cw, h); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  ctx.strokeStyle = "#19d08b"; ctx.lineWidth = 1.5; ctx.beginPath();
  curve.forEach((p, i) => i ? ctx.lineTo(i * cw, y(p.equity)) : ctx.moveTo(0, y(p.equity)));
  ctx.stroke(); ctx.lineWidth = 1;
}

window.addEventListener("resize", () => { if (last) render(last); });

// Historical reports are generated offline. Loading this view never trades.
async function loadResearch() {
  const button = $("researchRefresh");
  button.disabled = true;
  try {
    const response = await fetch("/api/research");
    if (!response.ok) throw new Error("Historical report unavailable. Restart the dashboard after installing the strategy update.");
    const data = await response.json();
    $("researchNote").textContent = data.warning || "Research only. Historical results do not establish future profits.";
    const rows = (data.results || []).filter(r => r.sample === "holdout");
    const first = rows[0];
    $("researchMeta").textContent = first
      ? `${first.resolution} candles · ${new Date(first.evaluation_start * 1000).toISOString().slice(0,10)} – ${new Date(first.evaluation_end * 1000).toISOString().slice(0,10)} · ${first.fee_bps_per_side} bps fee + ${first.slippage_bps_per_side} bps slippage per side · ${data.risk_per_trade_pct}% risk/trade, ${data.max_leverage}× leverage cap · drawdown at candle closes`
      : "Run: python research.py --download --days 180";
    $("researchResults").innerHTML = rows.map(r => `<tr>
      <td>${esc(r.symbol)}</td><td>${esc(STRAT_SHORT[r.strategy] || r.strategy)}</td>
      <td class="${r.return_pct >= 0 ? "up" : "down"}">${signed(r.return_pct)}%</td>
      <td>${fmt(r.max_drawdown_pct)}%</td><td title="${esc(r.sample_note)}">${r.analytics.trades}${r.analytics.trades < 30 ? " ⚠" : ""}</td>
      <td>${fmt(r.analytics.profit_factor)}</td></tr>`).join("") || '<tr><td colspan="6">No research results yet.</td></tr>';
  } catch (error) { $("researchNote").textContent = error.message; }
  finally { button.disabled = false; }
}
$("researchRefresh").onclick = loadResearch;
document.querySelectorAll(".research-select").forEach(button => {
  button.onclick = () => post("/api/strategy/" + button.dataset.strategy);
});
loadResearch();
