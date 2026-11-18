#!/usr/bin/env python3
"""
TradeOS dashboard server.

  FastAPI backend wrapping the Agent:
    - a background thread runs the bot loop (start/stop controllable)
    - REST endpoints for state + control
    - a WebSocket that pushes a full snapshot every second

Run:  python server.py   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations
import asyncio
import copy
import hmac
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
import yaml

from core.settings import Settings
from core.agent import Agent, _RES_SECONDS, strategy_meta
from core import ai, alerts, audit, backtest_lab, state
from core import db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tradeos.server")

ROOT = Path(__file__).resolve().parent
settings = Settings.load()
CONFIG_REVISION = 20
# What config.yaml says, captured before any saved dashboard override is
# applied — the dashboard shows these as "file default" and can reset to them.
CONFIG_RISK = {
    key: getattr(settings.risk, key) for key in (
        "max_portfolio_risk_pct", "max_open_positions",
        "max_same_direction_positions", "max_leverage",
        "max_daily_loss_pct", "strategy_risk_pct",
    )
}
CONFIG_RISK_FULL = {**{key: getattr(settings.risk, key) for key in Agent.RISK_LIMITS},
                    "risk_per_trade_pct": settings.risk.risk_per_trade_pct,
                    "strategy_risk_pct": dict(settings.risk.strategy_risk_pct)}
CONFIG_ENGINE = {"poll_seconds": settings.poll_seconds, "lookback": settings.lookback,
                 "resolution": settings.resolution, "ai_narrate": bool(settings.ai_narrate)}
CONFIG_SYMBOLS = list(settings.symbols)
CONFIG_SUBS = copy.deepcopy(settings.strategy_params.get("subs"))
CONFIG_STRATEGY = settings.strategy_name
CONFIG_RESOLUTION = settings.resolution
STARTED_AT = time.time()


def _git_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=3).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


VERSION = _git_version()


def _preload_overrides(s: Settings):
    """Apply the saved market list, per-coin slots and engine settings BEFORE
    the agent is built, so every saved market gets its broker, strategy and
    open-trade history exactly like a config.yaml market would. Ignored when
    the saved state predates CONFIG_REVISION (it's discarded on resume)."""
    try:
        st = state.load()
    except Exception as e:
        log.warning("could not read saved state: %s", e)
        return
    if not st or st.get("config_revision") != CONFIG_REVISION:
        return
    symbols = st.get("symbols")
    if (isinstance(symbols, list) and symbols
            and all(isinstance(x, str) and re.match(r"^[A-Z0-9]{2,24}$", x) for x in symbols)):
        s.symbols = list(dict.fromkeys(symbols))
    subs = st.get("strategy_subs")
    if s.strategy_name == "combo" and isinstance(subs, list) and subs:
        s.strategy_params = {**s.strategy_params, "subs": subs}
    engine = st.get("engine") if isinstance(st.get("engine"), dict) else {}
    for key, (low, high) in Agent.ENGINE_LIMITS.items():
        value = engine.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            setattr(s, key, value)
    if isinstance(engine.get("ai_narrate"), bool):
        s.ai_narrate = engine["ai_narrate"]


_preload_overrides(settings)
agent = Agent(settings)

@asynccontextmanager
async def lifespan(app):
    _resume_from_state()
    try:
        yield
    finally:
        runner._stop.set()
        if os.getenv("MONGODB_URI", "").strip():
            db.release_execution_lease(runner.owner)


app = FastAPI(title="TradeOS Dashboard", lifespan=lifespan)

# ── access control ──
# If TRADEOS_TOKEN is set, the whole app (dashboard + every API/WS call) requires
# it. Unset = no auth, same as before (local dev). This matters on any host
# reachable from the public internet: without it, anyone with the URL can
# start/stop the bot, switch to Delta Demo, or close your positions.
AUTH_TOKEN = os.environ.get("TRADEOS_TOKEN", "").strip()
COOKIE_NAME = "tradeos_auth"


def _token_ok(token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token, AUTH_TOKEN)


_LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TradeOS — Sign in</title>
<meta name="theme-color" content="#08090c">
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/static/v2/icon.svg" type="image/svg+xml">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:#08090c;
  background-image:radial-gradient(900px 480px at 70% -120px,rgba(139,123,255,.16),transparent 70%),radial-gradient(700px 420px at 0% 110%,rgba(63,214,195,.08),transparent 70%);
  color:#eceef3;font:15px/1.5 Inter,system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.card{width:min(400px,100%);background:#101217;border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:30px;
  box-shadow:0 30px 80px -20px rgba(0,0,0,.7)}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:22px}
.brand b{font-size:20px;letter-spacing:-.02em}
h1{font-size:22px;margin:0 0 6px;letter-spacing:-.02em}
p{margin:0 0 22px;color:#a3a9b6;font-size:14px}
label{display:block;font-size:12.5px;font-weight:600;color:#a3a9b6;margin-bottom:7px}
.field{position:relative}
input{width:100%;height:46px;padding:0 46px 0 14px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:#151820;color:#eceef3;font:inherit;outline:none;transition:.15s}
input:focus{border-color:#8b7bff;box-shadow:0 0 0 3px rgba(139,123,255,.18)}
.eye{position:absolute;right:8px;top:7px;width:32px;height:32px;border:0;border-radius:8px;background:transparent;color:#6c7382;cursor:pointer}
.eye:hover{color:#eceef3;background:rgba(255,255,255,.06)}
button.go{width:100%;height:46px;margin-top:16px;border:0;border-radius:12px;background:#8b7bff;color:#fff;font:600 15px Inter,system-ui,sans-serif;cursor:pointer;box-shadow:0 10px 24px -10px rgba(139,123,255,.8);transition:.15s}
button.go:hover{background:#7a68f5}
.err{margin:12px 0 0;padding:10px 12px;border-radius:10px;background:rgba(242,85,111,.12);border:1px solid rgba(242,85,111,.35);color:#f2556f;font-size:13px}
.foot{margin-top:18px;font-size:12px;color:#6c7382;text-align:center}
</style></head><body>
<form class="card" method="post" action="/login">
<div class="brand"><svg width="36" height="36" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8b7bff"/><stop offset="1" stop-color="#3fd6c3"/></linearGradient></defs><rect width="32" height="32" rx="9" fill="url(#g)"/><path d="M7 21l5.5-6 4.5 4 8-9" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="10" r="2.2" fill="#fff"/></svg><b>TradeOS</b></div>
<h1>Welcome back</h1>
<p>Enter your access token to open the trading workspace.</p>
<label for="token">Access token</label>
<div class="field"><input id="token" type="password" name="token" placeholder="••••••••••••" autocomplete="current-password" autofocus required>
<button class="eye" type="button" aria-label="Show token" onclick="var i=document.getElementById('token');i.type=i.type==='password'?'text':'password'">
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg></button></div>
{error}
<button class="go" type="submit">Sign in</button>
<div class="foot">Sessions last 30 days on this device.</div>
</form></body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _LOGIN_PAGE.replace("{error}", "")


@app.post("/login")
def login_submit(token: str = Form("")):
    if _token_ok(token):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
        return resp
    return HTMLResponse(_LOGIN_PAGE.replace("{error}", '<div class="err">That token didn\'t match. Try again.</div>'), status_code=401)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.middleware("http")
async def revalidate_app_assets(request: Request, call_next):
    """The v2 app is plain ES modules; make browsers revalidate them (cheap
    304s) so a deploy never mixes old and new modules."""
    response = await call_next(request)
    if request.url.path.startswith("/static/v2/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Not sensitive, and browsers fetch the manifest without cookies.
_PUBLIC_PATHS = ("/login", "/healthz", "/manifest.webmanifest", "/static/v2/icon.svg",
                 "/static/v2/icon-maskable.svg")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not AUTH_TOKEN or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    if _token_ok(request.cookies.get(COOKIE_NAME)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


class BotRunner:
    """Runs agent.evaluate() on an interval in a background thread."""
    def __init__(self, agent: Agent):
        self.agent = agent
        self.running = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._control = threading.Lock()
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        # Desired mode loaded from persisted state.  A temporary Delta outage
        # must not silently downgrade a live bot to PAPER; retry restoration
        # while keeping execution fail-closed.
        self._resume_mode: str | None = None
        self._resume_next_at = 0.0
        self.owner = (os.getenv("TRADEOS_INSTANCE_ID") or os.getenv("RENDER_INSTANCE_ID")
                      or f"{socket.gethostname()}:{os.getpid()}")

    def _set_execution_role(self):
        role = os.getenv("TRADEOS_EXECUTION_ROLE", "auto").strip().lower()
        if self.agent.s.mode != "DELTA_DEMO":
            enabled, reason = True, "paper execution is local"
        elif role in ("read_only", "readonly", "standby"):
            enabled, reason = False, "configured read-only standby"
        elif role == "active":
            enabled, reason = True, "configured active executor"
        elif os.getenv("MONGODB_URI", "").strip():
            enabled = db.acquire_execution_lease(
                self.owner, max(30, int(self.agent.s.poll_seconds) * 4)
            )
            reason = ("owns the shared execution lease" if enabled
                      else "read-only standby; another instance owns the execution lease")
        else:
            # Preserve simple local/demo installs; production should use the
            # shared Mongo lease or an explicit role.
            enabled, reason = True, "single instance (no shared lease configured)"
        self.agent.execution_enabled = enabled
        self.agent.execution_reason = reason

    def _loop(self):
        try:
            while not self._stop.is_set():
                try:
                    if self._resume_mode and self.agent.s.mode != self._resume_mode:
                        now = time.time()
                        if now < self._resume_next_at:
                            self._stop.wait(min(self.agent.s.poll_seconds, self._resume_next_at - now))
                            continue
                        try:
                            self.agent.set_mode(self._resume_mode)
                            self._resume_mode = None
                            self.last_error = None
                            log.info("restored persisted mode %s", self.agent.s.mode)
                        except Exception as e:
                            self._resume_next_at = now + 30
                            self.last_error = str(e)[:300]
                            log.warning("mode restore pending (%s); retrying automatically", e)
                            self._stop.wait(self.agent.s.poll_seconds)
                            continue
                    self._set_execution_role()
                    self.agent.evaluate()
                    self.last_success_at = time.time()
                    self.last_error = None
                except Exception as e:
                    self.last_error = str(e)[:300]
                    log.error("eval error: %s", e)
                self._stop.wait(self.agent.s.poll_seconds)
        finally:
            self.running = False

    def start(self):
        with self._control:
            if self.running and not self._stop.is_set():
                return
            if self._thread and self._thread.is_alive():
                raise RuntimeError("previous cycle is stopping; wait before restarting")
            self._stop = threading.Event()
            self.running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            self.agent._feed("bot", "started")
            state.save(running=True)
            log.info("bot started")

    def stop(self):
        with self._control:
            self._stop.set()
            self.running = False
            self.agent._feed("bot", "stop requested; current cycle may finish")
            state.save(running=False)
            if os.getenv("MONGODB_URI", "").strip():
                db.release_execution_lease(self.owner)
            log.info("bot stop requested")


runner = BotRunner(agent)


def _resume_from_state():
    """On boot, restore the last desired state so the bot keeps running across
    restarts/redeploys until the user explicitly stops it. Each step is
    independent: one bad saved value must never stop the bot resuming."""
    st = state.load()
    if not st:
        return

    def step(name, fn):
        try:
            fn()
        except Exception as e:
            log.warning("could not restore %s: %s", name, e)

    try:
        if int(st.get("config_revision", 0)) < CONFIG_REVISION:
            # One-time migration: discard the silent legacy risk/strategy
            # overrides that caused config.yaml to stop reflecting reality.
            state.delete("risk", "strategy", "resolution", "strategy_subs", "symbols", "engine")
            st = state.load()
    except Exception as e:
        log.warning("could not check saved config revision: %s", e)
    if st.get("risk"):
        step("risk controls", lambda: agent.update_risk_settings(st["risk"], strict=False))
    if st.get("strategy"):
        step("strategy", lambda: agent.set_strategy(st["strategy"]))
    if st.get("strategy_subs"):
        step("per-coin strategies", lambda: agent.apply_strategy_subs(st["strategy_subs"]))

    def restore_resolution():
        if st.get("resolution") in _RES_SECONDS:
            agent.s.resolution = st["resolution"]
        agent._align_strategy_data()
        state.save(resolution=agent.s.resolution, config_revision=CONFIG_REVISION)
    step("timeframe", restore_resolution)

    def restore_mode():
        desired_mode = str(st["mode"]).upper()
        if desired_mode != agent.s.mode:
            try:
                agent.set_mode(desired_mode)
            except Exception as e:
                # Keep the desired mode and let the runner retry once the
                # exchange is reachable. Never evaluate live state in PAPER.
                runner._resume_mode = desired_mode
                runner._resume_next_at = time.time() + 30
                log.warning("Delta mode unavailable during boot; automatic restore scheduled: %s", e)
    if st.get("mode"):
        step("venue", restore_mode)
    if "auto" in st:
        step("execution mode", lambda: agent.set_auto(st["auto"]))
    step("entry pause", lambda: agent.pause_entries(bool(st.get("entries_paused", False))))
    if st.get("running"):
        def start():
            runner.start()
            log.info("resumed running state from disk")
        step("running state", start)


# ── persistence + change history ──
ENGINE_KEYS = ("poll_seconds", "lookback", "ai_narrate")
_runtime_cache = {"at": 0.0, "value": {}}


def _runtime_state() -> dict:
    """Saved state, cached briefly: every websocket frame needs it."""
    if time.time() - _runtime_cache["at"] > 5:
        _runtime_cache.update(at=time.time(), value=state.load())
    return _runtime_cache["value"]


def _persist(**extra):
    """Save EVERYTHING the dashboard can change, so any change — from a
    button, a form or Jarvis — survives restarts and redeploys."""
    state.save(
        risk=agent.risk_settings(),
        strategy_subs=agent.s.strategy_params.get("subs"),
        symbols=list(agent.markets),
        engine={key: getattr(agent.s, key) for key in ENGINE_KEYS},
        resolution=agent.s.resolution,
        config_revision=CONFIG_REVISION,
        **extra,
    )
    _runtime_cache["at"] = 0.0


def _audit(action: str, target: str = "", before=None, after=None, actor: str = "dashboard", detail: str = ""):
    try:
        audit.record(action, target=target, before=before, after=after, actor=actor, detail=detail)
    except Exception as e:  # history must never block a control action
        log.warning("audit record failed: %s", e)


def _bad(e: Exception, status: int = 400):
    return JSONResponse({"error": str(e)[:300]}, status_code=status)


# ── REST ──
@app.get("/api/state")
def get_state(lite: bool = False):
    return JSONResponse(json.loads(json.dumps(_snapshot(lite), default=str)))


def _snapshot(lite: bool = False):
    snap = agent.snapshot(lite) | {"running": runner.running}
    runtime = _runtime_state()
    override = runtime.get("risk") if runtime.get("config_revision") == CONFIG_REVISION else None
    differences = {
        key: {"config": CONFIG_RISK[key], "runtime": value}
        for key, value in (override or {}).items()
        if key in CONFIG_RISK and value != CONFIG_RISK[key]
    }
    snap["configuration"] = {
        "risk_source": "runtime_override" if override else "config.yaml",
        "risk_overrides": differences,
        "config_strategy": CONFIG_STRATEGY,
        "config_resolution": CONFIG_RESOLUTION,
    }
    snap["server"] = {"version": VERSION, "started_at": STARTED_AT,
                      "last_error": runner.last_error, "last_success_at": runner.last_success_at}
    return snap


@app.get("/healthz")
def healthz():
    now = time.time()
    stale_after = max(60, agent.s.poll_seconds * 4)
    checks = {
        "loop": (not runner.running) or bool(runner.last_success_at and now - runner.last_success_at <= stale_after),
        "delta": agent.s.mode != "DELTA_DEMO" or agent._acct.get("status") == "live",
        "mongo": not os.getenv("MONGODB_URI", "").strip() or db.available(),
    }
    # A process that is serving HTTP while the trading loop is stopped is not
    # healthy for uptime monitoring. This makes UptimeRobot catch silent stops.
    checks["running"] = runner.running
    healthy = all(checks.values())
    body = {"status": "ok" if healthy else "unhealthy", "checks": checks,
            "running": runner.running, "last_cycle_at": agent.last_cycle_at,
            "execution_enabled": agent.execution_enabled,
            "execution_reason": agent.execution_reason,
            "error": runner.last_error}
    return JSONResponse(body, status_code=200 if healthy else 503)


@app.post("/api/bot/start")
def bot_start():
    try:
        runner.start()
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    _audit("bot.start", before=False, after=True)
    return {"running": True}


@app.post("/api/bot/stop")
def bot_stop():
    runner.stop()
    _audit("bot.stop", before=True, after=False)
    return {"running": False}


@app.post("/api/entries/{action}")
def pause_entries(action: str):
    if action not in ("pause", "resume"):
        return JSONResponse({"error": "use pause or resume"}, status_code=400)
    paused = action == "pause"
    before = agent.entries_paused
    agent.pause_entries(paused)
    state.save(entries_paused=paused)
    _audit("entries." + action, before=before, after=paused)
    return {"entries_paused": paused}


@app.post("/api/risk")
async def set_risk_controls(request: Request):
    try:
        values = await request.json()
        before = agent.risk_settings()
        config = await run_in_threadpool(agent.update_risk_controls, values)
        persisted = {
            key: getattr(config, key) for key in (
                "max_portfolio_risk_pct", "max_open_positions",
                "max_same_direction_positions", "max_leverage",
                "max_daily_loss_pct", "strategy_risk_pct",
            )
        }
        _persist()
        _audit("risk.update", "risk", before, agent.risk_settings())
        return {"risk": persisted}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/risk/reset")
def reset_risk_controls():
    before = agent.risk_settings()
    agent.update_risk_controls(CONFIG_RISK)
    _persist()
    _audit("risk.reset", "risk", before, agent.risk_settings(), detail="reset to config.yaml")
    return {"risk": CONFIG_RISK, "source": "config.yaml"}


@app.post("/api/mode/{mode}")
def set_mode(mode: str):
    mode = mode.upper()
    if mode not in ("PAPER", "DELTA_DEMO"):
        return JSONResponse({"error": "bad mode"}, status_code=400)
    try:
        before = agent.s.mode
        agent.set_mode(mode)
        state.save(mode=mode)
        _audit("mode.set", "mode", before, mode)
        return {"mode": mode}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/auto/{flag}")
def set_auto(flag: str):
    before = agent.auto
    agent.set_auto(flag.lower() in ("1", "true", "on", "yes"))
    state.save(auto=agent.auto)
    _audit("execution.auto", "auto", before, agent.auto)
    return {"auto": agent.auto}


@app.post("/api/strategy/{name}")
def set_strategy(name: str):
    try:
        before = agent.s.strategy_name
        agent.set_strategy(name)
        state.save(strategy=name, resolution=agent.s.resolution,
                   config_revision=CONFIG_REVISION)
        _audit("strategy.global", "strategy", before, name)
        return {"strategy": name, "resolution": agent.s.resolution}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/strategy/sub/{tag}")
async def update_strategy_sub(tag: str, request: Request):
    """Self-service per-coin strategy/params/risk/on-off editing (combo
    `subs`), so changing what runs on a symbol never needs a config.yaml
    edit and a redeploy."""
    try:
        body = await request.json()
        before = next((x for x in agent.slots() if x["tag"] == tag), None)
        sub = await run_in_threadpool(
            lambda: agent.update_sub(tag, name=body.get("name"), params=body.get("params"),
                                     risk_pct=body.get("risk_pct"), enabled=body.get("enabled"),
                                     reset_params=bool(body.get("reset_params"))))
        _persist()
        after = next((x for x in agent.slots() if x["tag"] == tag), None)
        _audit("slot.update", tag, before, after)
        return {"sub": sub, "strategy_risk_pct": agent.s.risk.strategy_risk_pct}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/signal/confirm")
def confirm(symbol: Optional[str] = None):
    result = agent.execute_pending(symbol).__dict__
    _audit("signal.confirm", symbol or "", after=result)
    return {"result": result}


@app.post("/api/signal/dismiss")
def dismiss(symbol: Optional[str] = None):
    result = agent.dismiss_pending(symbol).__dict__
    _audit("signal.dismiss", symbol or "", after=result)
    return {"result": result}


@app.post("/api/position/close")
def close(symbol: Optional[str] = None):
    result = agent.force_close(symbol).__dict__
    _audit("position.close", symbol or "", after=result)
    return {"result": result}


@app.post("/api/evaluate")
def evaluate_once():
    """Force one evaluation cycle, across every symbol, now (useful when the
    bot loop is stopped)."""
    return {"result": {sym: o.__dict__ for sym, o in agent.evaluate().items()}}


@app.get("/api/chart")
def chart_data(symbol: str, resolution: str):
    """Chart timeframe is independent from the trading strategy timeframe."""
    if symbol not in agent.markets or resolution not in _RES_SECONDS:
        return JSONResponse({"error": "unknown market or timeframe"}, status_code=400)
    from core.indicators import ema, rsi, closes
    try:
        end = int(time.time())
        candles = agent.client.get_candles(symbol, resolution,
                    end - _RES_SECONDS[resolution] * 305, end)
        prices = closes(candles)
        fast, slow, trend, strength = ema(prices, 9), ema(prices, 20), ema(prices, 200), rsi(prices, 14)
        chart = [{"t": c["time"], "o": float(c["open"]), "h": float(c["high"]),
                  "l": float(c["low"]), "c": float(c["close"]),
                  "volume": float(c.get("volume") or 0), "ema_fast": fast[i],
                  "ema_slow": slow[i], "ema_trend": trend[i], "rsi": strength[i]}
                 for i, c in enumerate(candles)]
        return {"symbol": symbol, "resolution": resolution, "chart": chart[-160:]}
    except Exception:
        return JSONResponse({"error": "Chart feed unavailable; retry shortly"}, status_code=503)


@app.get("/api/orders")
def open_orders():
    with agent.lock:
        if agent.s.mode == "PAPER":
            return {"orders": [], "status": "simulated"}
        if agent._acct["status"] != "live":
            return JSONResponse({"error": "Delta account unavailable"}, status_code=503)
        try:
            orders = agent.client.get_orders()
            return {"orders": [{"id": o.get("id"),
                "symbol": o.get("product_symbol") or (o.get("product") or {}).get("symbol") or str(o.get("product_id")),
                "side": o.get("side"), "type": o.get("stop_order_type") or o.get("order_type"),
                "price": o.get("stop_price") or o.get("limit_price"),
                "size": o.get("size"), "state": o.get("state")}
                for o in orders if o.get("state") in ("open", "pending")], "status": "live"}
        except Exception:
            return JSONResponse({"error": "Could not verify open orders on Delta"}, status_code=503)


@app.post("/api/account/retry")
def retry_account():
    return agent.retry_account()


@app.post("/api/ai/analysis")
def ai_analysis(symbol: Optional[str] = None):
    """On-demand Gemini read of current chart conditions for one symbol."""
    return agent.ai_analysis(symbol)


@app.post("/api/ai/command")
async def ai_command(request: Request):
    """Voice/chat entry point ('Jarvis'): turns natural language into either
    a direct spoken answer, or a proposed action the dashboard must show the
    user and get an explicit confirm on before calling /api/ai/execute.
    This endpoint itself never changes anything."""
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    # The model call can take many seconds; keep the event loop (websocket
    # frames, every other request) free while it runs.
    return await run_in_threadpool(agent.jarvis_command, text)


@app.post("/api/ai/execute")
async def ai_execute(request: Request):
    """Perform one JARVIS_ACTIONS action. Only ever called after the user
    has confirmed the exact action jarvis_command proposed — never invoked
    directly from a raw transcript."""
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    name = body.get("name")
    args = body.get("args") or {}
    if name not in agent.JARVIS_ACTIONS:
        return JSONResponse({"error": "unknown action"}, status_code=400)
    try:
        if name == "start_bot":
            runner.start()
            result = {"running": True}
        elif name == "stop_bot":
            runner.stop()
            result = {"running": False}
        else:
            result = await run_in_threadpool(agent.jarvis_execute, name, args)
            if name == "set_mode":
                state.save(mode=agent.s.mode)
            elif name == "set_auto":
                state.save(auto=agent.auto)
            elif name == "pause_entries":
                state.save(entries_paused=agent.entries_paused)
            else:
                _persist()
        _audit("jarvis." + name, str(args.get("symbol") or args.get("field") or ""),
               after=json.loads(json.dumps(result, default=str)), actor="jarvis",
               detail=json.dumps(args, default=str)[:300])
        return {"result": json.loads(json.dumps(result, default=str))}
    except Exception as e:
        return JSONResponse({"error": str(e)[:300]}, status_code=400)


@app.get("/api/research")
def strategy_research():
    path = ROOT / "data" / "strategy_comparison.json"
    if not path.exists():
        return {"status": "not_run", "results": [], "warning": "Run research.py to generate a historical comparison."}
    try:
        report = json.loads(path.read_text())
        validation = ROOT / "data" / "ema_5m_validation.json"
        if validation.exists():
            report["validations"] = {"ema_5m": json.loads(validation.read_text())}
        return report
    except (ValueError, OSError):
        return JSONResponse({"error": "research report unavailable"}, status_code=503)


@app.get("/api/strategies")
def strategy_catalog():
    from strategies.loader import available_strategies
    out = []
    for name in available_strategies():
        try:
            meta = strategy_meta(name)
        except Exception as e:
            log.warning("strategy %s failed to load: %s", name, e)
            continue
        out.append({"name": name, "params": meta["defaults"],
                    "resolutions": meta["resolutions"] or None,
                    "resolution": meta["resolution"],
                    "required_candles": meta["required_candles"],
                    "description": meta["description"]})
    return {"strategies": out}


# ── control plane: everything editable from the dashboard ──
def _config_payload() -> dict:
    return {
        "engine": agent.engine_settings(), "engine_config": CONFIG_ENGINE,
        "engine_limits": {k: list(v) for k, v in Agent.ENGINE_LIMITS.items()},
        "risk": agent.risk_settings(), "risk_config": CONFIG_RISK_FULL,
        "risk_limits": {k: [lo, hi, "int" if t is int else "float"]
                        for k, (lo, hi, t) in Agent.RISK_LIMITS.items()},
        "slot_risk_range": list(Agent.SLOT_RISK_RANGE),
        "symbols": list(agent.markets), "symbols_config": CONFIG_SYMBOLS,
        "slots": agent.slots(), "slots_config": CONFIG_SUBS,
        "mode": agent.s.mode, "auto": agent.auto, "entries_paused": agent.entries_paused,
        "running": runner.running, "timeframes": list(_RES_SECONDS),
        "alerts": {"configured": alerts.configured(),
                   "webhook": bool(os.getenv("TRADEOS_ALERT_WEBHOOK_URL", "").strip()),
                   "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip()),
                   "email": bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("ALERT_EMAIL_TO", "").strip())},
        "ai": {"available": ai.available(), "model": ai.model_name()},
        "credentials": {"delta": bool(agent.s.api_key and agent.s.api_secret),
                        "mongo": bool(os.getenv("MONGODB_URI", "").strip()),
                        "auth": bool(AUTH_TOKEN)},
        "version": VERSION, "started_at": STARTED_AT, "config_revision": CONFIG_REVISION,
    }


@app.get("/api/config")
def get_config():
    return JSONResponse(json.loads(json.dumps(_config_payload(), default=str)))


@app.post("/api/config/engine")
async def set_engine(request: Request):
    try:
        body = await request.json()
        before = agent.engine_settings()
        after = await run_in_threadpool(agent.update_engine, body)
        _persist()
        _audit("engine.update", "engine", before, after)
        return {"engine": after}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return _bad(e)


@app.post("/api/config/risk")
async def set_risk_settings(request: Request):
    """Partial update of any risk field (the dashboard's full risk page)."""
    try:
        body = await request.json()
        before = agent.risk_settings()
        await run_in_threadpool(agent.update_risk_settings, body)
        _persist()
        after = agent.risk_settings()
        _audit("risk.update", "risk", before, after)
        return {"risk": after}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return _bad(e)


@app.post("/api/config/risk/reset")
def reset_risk_settings():
    try:
        before = agent.risk_settings()
        values = {k: v for k, v in CONFIG_RISK_FULL.items() if k in Agent.RISK_LIMITS}
        caps = {t: v for t, v in CONFIG_RISK_FULL["strategy_risk_pct"].items() if t in agent._slot_tags()}
        agent.update_risk_settings({**values, "strategy_risk_pct": caps}, strict=False)
        _persist()
        _audit("risk.reset", "risk", before, agent.risk_settings(), detail="reset to config.yaml")
        return {"risk": agent.risk_settings()}
    except ValueError as e:
        return _bad(e)


@app.post("/api/config/engine/reset")
def reset_engine():
    try:
        before = agent.engine_settings()
        after = agent.update_engine(CONFIG_ENGINE)
        _persist()
        _audit("engine.reset", "engine", before, after, detail="reset to config.yaml")
        return {"engine": after}
    except ValueError as e:
        return _bad(e)


@app.get("/api/config/export")
def export_config():
    """The live configuration as config.yaml, so dashboard changes can be
    committed back to the repo if wanted."""
    risk = agent.risk_settings()
    doc = {
        "mode": agent.s.mode,
        "market": {"symbol": list(agent.markets), "resolution": agent.s.resolution,
                   "lookback": agent.s.lookback},
        "loop": {"poll_seconds": agent.s.poll_seconds},
        "execution": {"shadow_paper": bool(agent.s.shadow_paper)},
        "strategy": {"name": agent.s.strategy_name, "params": agent.s.strategy_params},
        "risk": risk,
        "logging": {"level": agent.s.log_level, "trade_log": agent.s.trade_log},
        "ai": {"narrate": bool(agent.s.ai_narrate)},
    }
    header = (f"# TradeOS configuration exported from the dashboard\n"
              f"# version {VERSION} · {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
    body = header + yaml.safe_dump(json.loads(json.dumps(doc, default=str)), sort_keys=False)
    return PlainTextResponse(body, media_type="text/yaml",
                             headers={"Content-Disposition": "attachment; filename=tradeos-config.yaml"})


@app.post("/api/markets")
async def add_market(request: Request):
    try:
        body = await request.json()
        sub = await run_in_threadpool(
            lambda: agent.add_market(body.get("symbol"), strategy=body.get("strategy") or "paused",
                                     params=body.get("params"), risk_pct=body.get("risk_pct"),
                                     tag=body.get("tag")))
        _persist()
        _audit("market.add", sub["symbols"][0], None, sub)
        return {"slot": sub, "symbols": list(agent.markets)}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return _bad(e)


@app.delete("/api/markets/{symbol}")
def remove_market(symbol: str):
    try:
        before = agent.slot_for_symbol(symbol.upper())
        result = agent.remove_market(symbol)
        _persist()
        _audit("market.remove", symbol.upper(), before, None)
        return {**result, "symbols": list(agent.markets)}
    except ValueError as e:
        return _bad(e)


_products_cache = {"at": 0.0, "value": []}


@app.get("/api/products")
def products():
    """Delta perpetuals that can be added as markets (cached 10 minutes)."""
    if time.time() - _products_cache["at"] > 600 or not _products_cache["value"]:
        try:
            tickers = {t.get("symbol"): t for t in agent.client.get_tickers()}
            items = []
            for p in agent.client.get_products():
                sym = p.get("symbol")
                if not sym or p.get("contract_type") != "perpetual_futures":
                    continue
                t = tickers.get(sym) or {}
                items.append({"symbol": sym, "description": p.get("description") or "",
                              "contract_value": p.get("contract_value"),
                              "underlying": (p.get("underlying_asset") or {}).get("symbol"),
                              "price": t.get("mark_price") or t.get("close"),
                              "volume": t.get("turnover_usd") or t.get("volume")})
            items.sort(key=lambda x: -float(x.get("volume") or 0))
            _products_cache.update(at=time.time(), value=items)
        except Exception as e:
            if not _products_cache["value"]:
                return _bad(RuntimeError(f"exchange product list unavailable: {e}"), 503)
    trading = set(agent.markets)
    return {"products": [dict(p, trading=p["symbol"] in trading) for p in _products_cache["value"]]}


@app.post("/api/trade")
async def manual_trade(request: Request):
    """Manual market entry from the dashboard (risk-sized, always bracketed)."""
    try:
        body = await request.json()
        out = await run_in_threadpool(
            lambda: agent.manual_entry(body.get("symbol"), body.get("side"), risk_pct=body.get("risk_pct"),
                                       sl_pct=body.get("sl_pct"), tp_pct=body.get("tp_pct")))
        _audit("trade.manual", str(body.get("symbol") or ""), after=out.__dict__,
               detail=json.dumps({k: body.get(k) for k in ("side", "risk_pct", "sl_pct", "tp_pct")}))
        if out.event == "entry":
            return {"result": out.__dict__}
        return JSONResponse({"error": out.detail or "order not placed", "result": out.__dict__}, status_code=409)
    except (ValueError, RuntimeError, TypeError, json.JSONDecodeError) as e:
        return _bad(e)


@app.post("/api/positions/close-all")
async def close_all_positions(request: Request):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    if body.get("pause_entries"):
        await run_in_threadpool(agent.pause_entries, True)
        state.save(entries_paused=True)
    results = await run_in_threadpool(agent.close_all)
    _audit("positions.close_all", "all", after=results,
           detail="kill switch (entries paused)" if body.get("pause_entries") else "")
    return {"results": results, "entries_paused": agent.entries_paused}


@app.post("/api/paper/reset")
def reset_paper():
    try:
        equity = agent.reset_paper_equity()
        _audit("paper.reset", "equity", after=equity)
        return {"equity": equity}
    except ValueError as e:
        return _bad(e)


@app.get("/api/trades")
def all_trades():
    """The full in-memory trade ledger (journal + analytics)."""
    with agent.lock:
        trades = list(agent.trades)
    return {"trades": trades, "count": len(trades)}


@app.get("/api/audit")
def audit_log(limit: int = 200):
    return {"entries": audit.recent(limit)}


@app.post("/api/alerts/test")
def alerts_test():
    if not alerts.configured():
        return _bad(ValueError("no alert channel configured (set a webhook, Telegram or email in .env)"))
    alerts.notify("test", "alert channel check from the dashboard", cooldown=5)
    _audit("alerts.test")
    return {"sent": True}


@app.post("/api/backtest")
async def start_backtest(request: Request):
    try:
        body = await request.json()
        slot = agent.slot_for_symbol(str(body.get("symbol") or "").upper())
        params = backtest_lab.validate(body, risk=agent.risk_settings(),
                                       default_risk_pct=(slot or {}).get("risk_pct") or agent.s.risk.risk_per_trade_pct)
        job = backtest_lab.start(params)
        return {"job": job}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return _bad(e)


@app.get("/api/backtest")
def backtest_jobs():
    return {"jobs": backtest_lab.recent()}


@app.get("/api/backtest/{job_id}")
def backtest_job(job_id: str):
    job = backtest_lab.get(job_id)
    if not job:
        return _bad(ValueError("unknown backtest"), 404)
    return JSONResponse(json.loads(json.dumps(job, default=str)))


@app.delete("/api/backtest/{job_id}")
def cancel_backtest(job_id: str):
    return {"cancelled": backtest_lab.cancel(job_id)}


# ── WebSocket push ──
@app.websocket("/ws")
async def ws(socket: WebSocket):
    if AUTH_TOKEN and not _token_ok(socket.cookies.get(COOKIE_NAME)):
        await socket.close(code=4401)
        return
    await socket.accept()
    lite = socket.query_params.get("lite") == "1"
    try:
        while True:
            snap = await asyncio.get_running_loop().run_in_executor(None, _snapshot, lite)
            await socket.send_text(json.dumps(snap, default=str))
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws closed: %s", e)


# Every workspace route serves the same authenticated application shell; the
# app routes client-side. The previous dashboard stays available at /classic.
UI_ROUTES = (
    "/", "/dashboard", "/overview", "/trade", "/trade/{symbol}", "/markets",
    "/positions", "/orders", "/strategies", "/risk", "/journal", "/trades",
    "/analytics", "/backtest", "/ai", "/jarvis", "/activity", "/feed", "/system",
    "/health", "/settings", "/bots",
)


def index(symbol: str = ""):
    return HTMLResponse((ROOT / "web" / "v2" / "index.html").read_text(),
                        headers={"Cache-Control": "no-cache"})


def classic_index():
    return (ROOT / "web" / "index.html").read_text()


for route in UI_ROUTES:
    app.add_api_route(route, index, response_class=HTMLResponse, methods=["GET"], include_in_schema=False)
app.add_api_route("/classic", classic_index, response_class=HTMLResponse, methods=["GET"], include_in_schema=False)


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Served from the root so it can control every app route.
    return FileResponse(ROOT / "web" / "v2" / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(ROOT / "web" / "v2" / "manifest.webmanifest",
                        media_type="application/manifest+json")

app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")


if __name__ == "__main__":
    # HOST/PORT from env so cloud hosts (Railway/Render/Fly) can bind correctly.
    # Local default stays 127.0.0.1 (not exposed); set HOST=0.0.0.0 to serve externally.
    host = os.environ.get("HOST", "127.0.0.1").strip()
    port = int(os.environ.get("PORT", "8000").strip())
    print(f"\n  TradeOS dashboard → http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
