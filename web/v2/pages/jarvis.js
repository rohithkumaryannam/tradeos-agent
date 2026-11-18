import { html, useState, useEffect, useRef } from "../lib/preact.js";
import { Icon } from "../lib/icons.js";
import { Button, Card, Badge, Select, Toggle, Callout, cx } from "../lib/ui.js";
import { useStore, store, act, toast, toastError, refreshConfig } from "../lib/store.js";
import { api, prefs, strategyLabel, paramInfo, timeAgo, fmtUsd } from "../lib/util.js";

const KEY = "tradeos.v2.jarvis";
const SUGGEST = ["How are we doing today?", "Which coin is performing best?", "What's open right now?", "Pause new entries", "Set XRP take-profit to 8x", "Turn off trading on SOL"];
const DANGER = new Set(["close_position", "close_all_positions", "stop_bot", "remove_market", "set_mode"]);

export function describeAction(a) {
  const x = a?.args || {};
  switch (a?.name) {
    case "close_position": return `Close the ${x.symbol} position at market`;
    case "close_all_positions": return "Close ALL open positions at market";
    case "set_mode": return `Switch the venue to ${x.mode === "DELTA_DEMO" ? "Delta Demo" : "Paper"}`;
    case "set_auto": return x.enabled ? "Turn on auto-execution" : "Switch to manual confirmation";
    case "pause_entries": return x.paused ? "Pause new entries" : "Resume new entries";
    case "set_coin_strategy": return `Run ${strategyLabel(x.strategy)} on ${x.symbol}`;
    case "set_coin_params": return `${x.symbol}: ` + Object.entries(x.params || {}).map(([k, v]) => `${paramInfo(k).label} → ${v}`).join(", ");
    case "set_coin_enabled": return `${x.enabled ? "Turn on" : "Turn off"} trading on ${x.symbol}`;
    case "set_coin_risk": return `Set ${x.symbol} risk per trade to ${x.risk_pct}%`;
    case "set_strategy_risk_pct": return `Set ${x.strategy} risk per trade to ${x.risk_pct}%`;
    case "set_risk_limit": return `Set ${String(x.field || "").replace(/_/g, " ")} to ${x.value}`;
    case "set_engine": return "Engine: " + Object.entries(x).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => `${k.replace(/_/g, " ")} → ${v}`).join(", ");
    case "add_market": return `Start trading ${x.symbol}${x.strategy ? ` with ${strategyLabel(x.strategy)}` : ""}`;
    case "remove_market": return `Stop trading ${x.symbol} and remove it`;
    case "start_bot": return "Start the bot";
    case "stop_bot": return "Stop the bot";
    default: return a?.name || "Unknown action";
  }
}

function load() { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; } }
function save(msgs) { try { localStorage.setItem(KEY, JSON.stringify(msgs.slice(-60))); } catch { /* ignore */ } }

export function JarvisChat({ panel, close }) {
  const st = useStore();
  const ai = st.config?.ai;
  const [msgs, setMsgs] = useState(load);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("idle"); // idle | listening | thinking | speaking
  const [speak, setSpeak] = useState(prefs.get().speak !== false);
  const logRef = useRef(null);
  const recRef = useRef(null);
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  useEffect(() => { save(msgs); logRef.current && (logRef.current.scrollTop = logRef.current.scrollHeight); }, [msgs]);
  useEffect(() => () => { try { recRef.current?.abort(); window.speechSynthesis?.cancel(); } catch { /* ignore */ } }, []);

  const push = (m) => setMsgs((list) => [...list, { ...m, at: Date.now() }]);
  const say = (reply) => {
    if (!speak || !window.speechSynthesis || !reply) { setMode("idle"); return; }
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(reply);
      u.rate = 1.04;
      u.onstart = () => setMode("speaking");
      u.onend = u.onerror = () => setMode("idle");
      window.speechSynthesis.speak(u);
    } catch { setMode("idle"); }
  };

  const send = async (raw) => {
    const t = (raw ?? text).trim();
    if (!t || busy) return;
    setText(""); push({ who: "you", text: t }); setBusy(true); setMode("thinking");
    try {
      const r = await api("/api/ai/command", { method: "POST", body: { text: t } });
      push({ who: "bot", text: r.reply || "…" });
      if (r.action) push({ who: "action", action: r.action, status: "pending" });
      say(r.reply);
    } catch (e) { push({ who: "bot", text: "Sorry — " + e.message, error: true }); setMode("idle"); }
    setBusy(false);
  };

  const decide = async (idx, ok) => {
    const m = msgs[idx];
    if (!m || m.status !== "pending") return;
    setMsgs((list) => list.map((x, i) => (i === idx ? { ...x, status: ok ? "running" : "cancelled" } : x)));
    if (!ok) return;
    try {
      const r = await api("/api/ai/execute", { method: "POST", body: { name: m.action.name, args: m.action.args || {} } });
      setMsgs((list) => list.map((x, i) => (i === idx ? { ...x, status: "done", result: r.result } : x)));
      toast("Done", { body: describeAction(m.action), tone: "up", icon: "check" });
      refreshConfig();
    } catch (e) {
      setMsgs((list) => list.map((x, i) => (i === idx ? { ...x, status: "failed", error: e.message } : x)));
      toastError(e, "Jarvis couldn't do that");
    }
  };

  const listen = () => {
    if (!SR) { toast("Voice input isn't supported in this browser", { tone: "warn" }); return; }
    if (mode === "listening") { recRef.current?.stop(); return; }
    try {
      window.speechSynthesis?.cancel();
      const rec = new SR();
      rec.lang = navigator.language || "en-US"; rec.interimResults = true; rec.maxAlternatives = 1;
      let finalText = "";
      rec.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
        }
        setText((finalText + interim).trim());
      };
      rec.onerror = () => setMode("idle");
      rec.onend = () => { setMode((m) => (m === "listening" ? "idle" : m)); if (finalText.trim()) send(finalText); };
      recRef.current = rec; rec.start(); setMode("listening");
    } catch { setMode("idle"); }
  };

  const status = { idle: ai?.available ? "Ready" : "AI not configured", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" }[mode];

  return html`<div class=${cx("jarvis", panel && "panel", !panel && "card")}>
    <div class="j-head">
      <div class=${cx("orb", mode)}></div>
      <div class="grow" style="min-width:0"><div style="font-weight:700;font-size:15.5px">Jarvis</div>
        <div class="faint ellipsis" style="font-size:12.5px">${status}${ai?.model ? ` · ${ai.model}` : ""}</div></div>
      <${Toggle} checked=${speak} onChange=${(v) => { setSpeak(v); prefs.set({ speak: v }); if (!v) window.speechSynthesis?.cancel(); }} label=${html`<span class="hide-sm" style="font-size:12.5px">Voice replies</span>`} />
      <${Button} size="sm" variant="ghost" icon="trash" title="Clear conversation" onClick=${() => setMsgs([])} />
      ${close && html`<${Button} size="sm" variant="ghost" icon="x" onClick=${close} aria-label="Close" />`}
    </div>
    <div class="j-log" ref=${logRef}>
      ${!ai?.available && html`<${Callout} tone="warn" title="AI key missing">Set OPENROUTER_API_KEY (or GEMINI_API_KEY) in the server's .env to talk to Jarvis.</${Callout}>`}
      ${!msgs.length && html`<div class="empty" style="padding:30px 10px"><div class="orb" style="width:56px;height:56px"></div><strong>Ask anything, or tell me what to change</strong>
        <span>I can read the full trade history and adjust every setting — strategies, parameters, risk, markets, the bot itself. Every change waits for your confirmation.</span></div>`}
      ${msgs.map((m, i) => m.who === "action" ? html`<div class="action-card">
          <div class="row" style="gap:8px"><${Icon} name="zap" size=${16} class=${DANGER.has(m.action.name) ? "down" : "accent"} /><b class="grow">${describeAction(m.action)}</b>
            ${m.status === "done" && html`<${Badge} tone="up">Done</${Badge}>`}${m.status === "cancelled" && html`<${Badge}>Cancelled</${Badge}>`}${m.status === "failed" && html`<${Badge} tone="down">Failed</${Badge}>`}${m.status === "running" && html`<span class="spinner"></span>`}</div>
          ${m.error && html`<div class="down" style="font-size:12.5px">${m.error}</div>`}
          ${m.status === "pending" && html`<div class="row"><${Button} size="sm" variant=${DANGER.has(m.action.name) ? "danger solid" : "primary"} onClick=${() => decide(i, true)}>Confirm</${Button}>
            <${Button} size="sm" variant="ghost" onClick=${() => decide(i, false)}>Cancel</${Button}></div>`}
        </div>`
        : html`<div class=${cx("msg", m.who === "you" ? "you" : "bot")} style=${m.error ? "color:var(--down)" : ""}>${m.text}</div>`)}
      ${busy && html`<div class="msg bot"><span class="spinner" style="width:13px;height:13px"></span></div>`}
    </div>
    <div>
      ${!msgs.length && html`<div class="j-chips">${SUGGEST.map((q) => html`<button class="chip" style="cursor:pointer" onClick=${() => send(q)}>${q}</button>`)}</div>`}
      <div class="j-input">
        <${Button} variant=${mode === "listening" ? "danger solid" : ""} icon="mic" onClick=${listen} title=${SR ? "Speak" : "Voice not supported here"} aria-label="Voice input" />
        <textarea class="input" rows="1" placeholder=${mode === "listening" ? "Listening…" : "Message Jarvis…  (Enter to send)"} value=${text}
          onInput=${(e) => setText(e.currentTarget.value)} onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}></textarea>
        <${Button} variant="primary" icon="send" onClick=${() => send()} disabled=${!text.trim() || busy} aria-label="Send" />
      </div>
    </div>
  </div>`;
}

function Analysis() {
  const st = useStore();
  const syms = Object.keys(st.snap?.markets || {});
  const [sym, setSym] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const symbol = sym || syms[0] || "";
  const run = async () => {
    setBusy(true);
    try { setRes(await api("/api/ai/analysis?symbol=" + encodeURIComponent(symbol), { method: "POST" })); }
    catch (e) { toastError(e, "Analysis failed"); }
    setBusy(false);
  };
  return html`<${Card} title="AI market read" icon="sparkles" sub="On-demand analysis of the selected market and recent performance">
    <div class="stack">
      <div class="row"><div class="grow"><${Select} value=${symbol} onChange=${setSym} options=${syms} /></div><${Button} variant="primary" icon="sparkles" loading=${busy} onClick=${run} disabled=${!symbol}>Analyze</${Button}></div>
      ${res ? (res.available === false ? html`<${Callout} tone="warn">${res.text || "AI is not configured."}</${Callout}>`
        : html`<div style="font-size:13.5px;line-height:1.6;white-space:pre-wrap">${res.text}</div>
          <div class="faint" style="font-size:12px">${res.model || ""}${res.ts ? " · " + timeAgo(res.ts) : ""}${res.trade_count ? ` · based on ${res.trade_count} recent trades (${fmtUsd(res.net_pnl, { sign: true })})` : ""}</div>`)
        : html`<div class="muted" style="font-size:13px">Get a plain-English read of trend, momentum and how the bot has been doing on this market.</div>`}
    </div>
  </${Card}>`;
}

export default function JarvisPage() {
  return html`<div class="grid g-main-wide" style="align-items:start">
    <${JarvisChat} />
    <div class="stack">
      <${Analysis} />
      <${Card} title="What Jarvis can do" icon="info">
        <div class="stack-sm" style="font-size:13px">
          ${[["Answer", "Win rate, P&L by coin or strategy, open positions, risk usage — from the full trade history."],
            ["Tune strategies", "“Set ETH take-profit to 6x”, “Run mastermind on BTC”, “Turn off SOL”."],
            ["Risk", "“Max 3 positions”, “Risk 0.3% per trade on DOGE”, “Daily loss cap 5%”."],
            ["Markets", "“Start trading LTC with Donchian”, “Remove ADA”."],
            ["Engine", "Start/stop the bot, pause entries, switch Paper ↔ Delta Demo, close positions."]].map(([h, b]) => html`<div><b>${h}</b><div class="muted">${b}</div></div>`)}
          <div class="faint" style="font-size:12px">Every change is shown first and only happens after you confirm. All actions are recorded in Activity → Change history.</div>
        </div>
      </${Card}>
    </div>
  </div>`;
}
