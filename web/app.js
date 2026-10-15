// TradeOS dashboard — vanilla JS, canvas charts, WebSocket live state.
const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const signed = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + fmt(n));

let last = null;

// ── controls ──
async function post(url) { try { await fetch(url, { method: "POST" }); } catch (e) {} }
$("startBtn").onclick = () => post("/api/bot/start");
$("stopBtn").onclick = () => post("/api/bot/stop");
$("confirmBtn").onclick = () => post("/api/signal/confirm");
$("dismissBtn").onclick = () => post("/api/signal/dismiss");
$("closeBtn").onclick = () => { if (confirm("Close the open position at market?")) post("/api/position/close"); };
let aiBusy = false;
$("aiBtn").onclick = async () => {
  if (aiBusy) return;
  aiBusy = true;
  const out = $("aiText"), btn = $("aiBtn");
  btn.disabled = true; out.className = "mut"; out.textContent = "Analyzing…";
  try {
    const r = await fetch("/api/ai/analysis", { method: "POST" });
    const d = await r.json();
    out.textContent = d.text || "No response.";
    out.className = d.available === false ? "warn" : "";
    $("aiModel").textContent = d.model ? "— " + d.model : "";
  } catch (e) { out.textContent = "Request failed."; out.className = "down"; }
  aiBusy = false;
  btn.disabled = !(last && last.ai_available);
};
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
  sock.onmessage = (e) => { lastFrame = Date.now(); last = JSON.parse(e.data); render(last); };
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

// ── render ──
function render(s) {
  $("running").textContent = s.running ? "RUNNING" : "STOPPED";
  $("status-dot").className = "dot" + (s.running ? " on" : "");
  $("mode").textContent = s.mode;
  $("execmode").textContent = s.auto ? "AUTO" : "MANUAL";
  $("sympill").textContent = `${s.symbol} · ${s.resolution}`;
  const sel = $("stratSelect");
  if (document.activeElement !== sel) {
    const opts = (s.strategies || [s.strategy]).join(",");
    if (sel.dataset.opts !== opts) {
      sel.innerHTML = (s.strategies || [s.strategy]).map(n => `<option value="${n}">${n}</option>`).join("");
      sel.dataset.opts = opts;
    }
    sel.value = s.strategy;
  }
  $("venueState").textContent = s.venue || "—";
  $("priceSrc").textContent = s.price_source || "Delta feed";
  if (!aiBusy) $("aiBtn").disabled = !s.ai_available;
  if (!s.ai_available) $("aiModel").textContent = "— key not set";
  $("startBtn").disabled = s.running;
  $("stopBtn").disabled = !s.running;

  document.querySelectorAll("#modeToggle button").forEach(b =>
    b.classList.toggle("sel", b.dataset.mode === s.mode));
  document.querySelectorAll("#autoToggle button").forEach(b =>
    b.classList.toggle("sel", (b.dataset.auto === "true") === s.auto));

  $("price").textContent = fmt(s.price, 1);
  $("equity").textContent = s.equity == null ? "—" : "$" + fmt(s.equity);
  const es = $("equitySrc");
  if (s.equity_status === "live") { es.textContent = `Delta testnet · ${s.equity_asset} (live)`; es.className = "src up"; }
  else if (s.equity_status === "unavailable") { es.textContent = "balance unavailable"; es.className = "src down"; }
  else { es.textContent = "simulated (paper)"; es.className = "src"; }
  const tp = $("todaypnl"); tp.textContent = "$" + signed(s.realized_today);
  tp.className = "num " + (s.realized_today >= 0 ? "up" : "down");

  // pending
  const pb = $("pendingBox");
  if (s.pending) {
    pb.classList.add("show");
    $("pendAct").textContent = s.pending.action;
    $("pendReason").textContent = s.pending.reason;
  } else pb.classList.remove("show");

  // position
  if (s.position) {
    $("posEmpty").style.display = "none";
    $("posBox").style.display = "block";
    const p = s.position;
    $("pSide").textContent = p.side.toUpperCase();
    $("pSide").className = "v " + (p.side === "long" ? "up" : "down");
    $("pEntry").textContent = fmt(p.entry, 1);
    $("pSize").textContent = p.size + " contracts";
    $("pSL").textContent = fmt(p.sl, 1);
    $("pTP").textContent = fmt(p.tp, 1);
    $("pNot").textContent = "$" + fmt(p.notional);
    const u = $("pUnr"); u.textContent = "$" + signed(p.unrealized);
    u.className = "v " + ((p.unrealized || 0) >= 0 ? "up" : "down");
    const op = $("openpnl"); op.textContent = "$" + signed(p.unrealized);
    op.className = "num " + ((p.unrealized || 0) >= 0 ? "up" : "down");
    const prot = $("pProt"), pr = p.protection || "unknown";
    prot.textContent = pr === "local" ? "SL/TP enforced (local)"
      : pr === "unverified" ? "⚠ unverified on exchange"
      : pr === "none" ? "⚠ NONE" : pr;
    prot.className = "v " + (pr === "local" ? "up" : pr === "unverified" ? "warn" : "down");
    $("pRecon").textContent = p.reconciliation || "—";
  } else {
    $("posEmpty").style.display = "block";
    $("posBox").style.display = "none";
    $("openpnl").textContent = "—"; $("openpnl").className = "num";
  }

  // risk
  const r = s.risk;
  $("rRisk").textContent = r.risk_per_trade_pct + "%";
  $("rSLTP").textContent = r.stop_loss_pct + "% / " + r.take_profit_pct + "%";
  $("rLev").textContent = r.max_leverage + "x";
  $("rCap").textContent = r.max_daily_loss_pct + "%";
  const co = $("rCapOk");
  co.textContent = s.daily_cap_ok ? "OK" : "HALTED";
  co.className = "v " + (s.daily_cap_ok ? "up" : "down");

  renderAccount(s);
  renderFeed(s.feed);
  renderAnalytics(s.analytics);
  renderTrades(s.trades);
  drawRSI(s.chart);
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
    st.textContent = "— unavailable"; st.className = "sub down";
    body.className = "warn"; body.textContent = "Account data unavailable (check API key / IP whitelist).";
    return;
  }
  st.textContent = "— testnet (live)"; st.className = "sub up";
  body.className = ""; body.style.padding = "0";
  const bals = (s.account_balances || []).filter(b => (b.balance || 0) > 0);
  let html = `<div style="font-size:10.5px;letter-spacing:1px;color:var(--faint);text-transform:uppercase;margin:4px 0 6px">Wallet balances</div>`;
  html += bals.length ? bals.map(b =>
    `<div class="row"><span class="k">${b.asset}</span><span class="v">${fmt(b.balance, 4)}<span class="mut"> avail ${fmt(b.available, 4)}</span></span></div>`
  ).join("") : `<div class="mut" style="padding:6px 0">No non-zero balances.</div>`;

  const pos = s.exchange_positions || [];
  html += `<div style="font-size:10.5px;letter-spacing:1px;color:var(--faint);text-transform:uppercase;margin:12px 0 6px">Exchange positions</div>`;
  html += pos.length ? pos.map(p =>
    `<div class="row"><span class="k">${p.symbol}</span><span class="v">${p.size} @ ${fmt(p.entry, 1)}${p.unrealized != null ? ` <span class="${p.unrealized >= 0 ? "up" : "down"}">${signed(p.unrealized)}</span>` : ""}</span></div>`
  ).join("") : `<div class="mut" style="padding:6px 0">Flat — no open positions on the exchange.</div>`;
  body.innerHTML = html;
}

function renderFeed(feed) {
  $("feed").innerHTML = (feed || []).map(f =>
    `<div><span class="tag ${f.event}">${f.event}</span>
     <span class="mut">${(f.t || "").slice(11, 19)}</span>
     <span>${f.detail}</span></div>`).join("");
}

function dur(s) {
  if (s == null) return "—";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  return (s / 3600).toFixed(1) + "h";
}

function renderAnalytics(a) {
  a = a || {};
  $("perfSub").textContent = a.trades ? `— ${a.trades} trades` : "— no trades yet";
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

const STRAT_SHORT = { ny_range_fakeout: "NY fakeout", ema_crossover: "EMA 9/20/200", combo: "combo" };
function renderTrades(trades) {
  const tagCls = { TP: "entry", SL: "reject", manual: "mode", strategy: "exit", backtest: "bot" };
  $("trades").querySelector("tbody").innerHTML = (trades && trades.length ? [...trades].reverse() : []).map(t => {
    const cls = t.win ? "up" : "down";
    const tag = tagCls[t.reason] || "mode";
    const strat = STRAT_SHORT[t.strategy] || t.strategy || "—";
    return `<tr>
      <td>${(t.exit_ts || "").slice(11, 19)}</td>
      <td title="${t.strategy || ""}">${strat}</td>
      <td class="${t.side === "long" ? "up" : "down"}">${(t.side || "").toUpperCase()}</td>
      <td>${fmt(t.entry, 1)}</td>
      <td>${fmt(t.exit, 1)}</td>
      <td>${t.size}</td>
      <td class="${cls}">${signed(t.pnl)}</td>
      <td class="${cls}">${t.r == null ? "—" : (t.r >= 0 ? "+" : "") + t.r + "R"}</td>
      <td class="mut">${dur(t.duration_s)}</td>
      <td><span class="tag ${tag}">${t.reason}</span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="10" class="mut">No trades yet</td></tr>`;
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

function drawRSI(chart) {
  const cv = $("rsi"); const { ctx, w, h } = setup(cv);
  if (!chart || !chart.length) return;
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
  if (!curve || curve.length < 2) return;
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
