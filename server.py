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
import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from core.settings import Settings
from core.agent import Agent, _RES_SECONDS
from core import state

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tradeos.server")

ROOT = Path(__file__).resolve().parent
settings = Settings.load()
agent = Agent(settings)

@asynccontextmanager
async def lifespan(app):
    _resume_from_state()
    yield
    runner._stop.set()


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


_LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>TradeOS — Sign in</title>
<style>
body{background:#070b12;color:#eaf1fb;font-family:system-ui,sans-serif;
  display:grid;place-items:center;height:100vh;margin:0}
form{background:#0f1624;border:1px solid #1c2740;border-radius:14px;padding:28px;
  width:280px;display:flex;flex-direction:column;gap:12px}
h1{font-size:16px;margin:0 0 4px}
input{background:#0b111c;border:1px solid #1c2740;border-radius:8px;color:#eaf1fb;
  padding:10px 12px;font-size:14px}
button{background:#4f8cff;border:0;border-radius:8px;color:#fff;padding:10px;
  font-weight:600;cursor:pointer}
.err{color:#ff4d5e;font-size:12px;margin:0}
</style></head><body>
<form method="post" action="/login">
<h1>TradeOS</h1>
<input type="password" name="token" placeholder="Access token" autofocus>
{error}
<button type="submit">Sign in</button>
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
    return HTMLResponse(_LOGIN_PAGE.replace("{error}", '<p class="err">Wrong token.</p>'), status_code=401)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not AUTH_TOKEN or request.url.path in ("/login",):
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

    def _loop(self):
        try:
            while not self._stop.is_set():
                try:
                    self.agent.evaluate()
                except Exception as e:
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
            log.info("bot stop requested")


runner = BotRunner(agent)


def _resume_from_state():
    """On boot, restore the last desired state so the bot keeps running across
    restarts/redeploys until the user explicitly stops it."""
    st = state.load()
    if not st:
        return
    try:
        if st.get("risk"):
            agent.update_risk_controls(st["risk"])
        if st.get("strategy"):
            agent.set_strategy(st["strategy"])
        if st.get("resolution") in _RES_SECONDS:
            agent.s.resolution = st["resolution"]
        agent._align_strategy_data()
        state.save(resolution=agent.s.resolution)
        if st.get("mode"):
            agent.set_mode(st["mode"])
        if "auto" in st:
            agent.set_auto(st["auto"])
        agent.pause_entries(bool(st.get("entries_paused", False)))
        if st.get("running"):
            runner.start()
            log.info("resumed running state from disk")
    except Exception as e:
        log.warning("could not resume state: %s", e)


# ── REST ──
@app.get("/api/state")
def get_state():
    return JSONResponse(agent.snapshot() | {"running": runner.running})


@app.post("/api/bot/start")
def bot_start():
    try:
        runner.start()
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return {"running": True}


@app.post("/api/bot/stop")
def bot_stop():
    runner.stop()
    return {"running": False}


@app.post("/api/entries/{action}")
def pause_entries(action: str):
    if action not in ("pause", "resume"):
        return JSONResponse({"error": "use pause or resume"}, status_code=400)
    paused = action == "pause"
    agent.pause_entries(paused)
    state.save(entries_paused=paused)
    return {"entries_paused": paused}


@app.post("/api/risk")
async def set_risk_controls(request: Request):
    try:
        values = await request.json()
        config = agent.update_risk_controls(values)
        persisted = {
            key: getattr(config, key) for key in (
                "max_portfolio_risk_pct", "max_open_positions",
                "max_same_direction_positions", "max_leverage",
                "max_daily_loss_pct", "strategy_risk_pct",
            )
        }
        state.save(risk=persisted)
        return {"risk": persisted}
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/mode/{mode}")
def set_mode(mode: str):
    mode = mode.upper()
    if mode not in ("PAPER", "DELTA_DEMO"):
        return JSONResponse({"error": "bad mode"}, status_code=400)
    try:
        agent.set_mode(mode)
        state.save(mode=mode)
        return {"mode": mode}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/auto/{flag}")
def set_auto(flag: str):
    agent.set_auto(flag.lower() in ("1", "true", "on", "yes"))
    state.save(auto=agent.auto)
    return {"auto": agent.auto}


@app.post("/api/strategy/{name}")
def set_strategy(name: str):
    try:
        agent.set_strategy(name)
        state.save(strategy=name, resolution=agent.s.resolution)
        return {"strategy": name, "resolution": agent.s.resolution}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/signal/confirm")
def confirm(symbol: Optional[str] = None):
    return {"result": agent.execute_pending(symbol).__dict__}


@app.post("/api/signal/dismiss")
def dismiss(symbol: Optional[str] = None):
    return {"result": agent.dismiss_pending(symbol).__dict__}


@app.post("/api/position/close")
def close(symbol: Optional[str] = None):
    return {"result": agent.force_close(symbol).__dict__}


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


@app.get("/api/research")
def strategy_research():
    path = ROOT / "data" / "strategy_comparison.json"
    if not path.exists():
        return {"status": "not_run", "results": [], "warning": "Run research.py to generate a historical comparison."}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return JSONResponse({"error": "research report unavailable"}, status_code=503)


@app.get("/api/strategies")
def strategy_catalog():
    from strategies.loader import available_strategies, load_strategy
    return {"strategies": [{"name": name, "params": strategy.params,
            "resolutions": getattr(strategy, "supported_resolutions", None),
            "required_candles": getattr(strategy, "required_candles", None)}
            for name in available_strategies()
            for strategy in [load_strategy(name, {})]]}


# ── WebSocket push ──
@app.websocket("/ws")
async def ws(socket: WebSocket):
    if AUTH_TOKEN and not _token_ok(socket.cookies.get(COOKIE_NAME)):
        await socket.close(code=4401)
        return
    await socket.accept()
    try:
        while True:
            snap = agent.snapshot() | {"running": runner.running}
            await socket.send_text(json.dumps(snap, default=str))
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws closed: %s", e)


# Every workspace route serves the same authenticated, live-data application shell.
UI_ROUTES = (
    "/", "/dashboard", "/bots", "/bots/new", "/bots/edit", "/markets",
    "/strategies", "/strategies/builder", "/backtest", "/backtest/results",
    "/trades", "/journal", "/positions", "/orders", "/risk", "/ai",
    "/analytics", "/health", "/feed", "/settings",
)

def index():
    return (ROOT / "web" / "index.html").read_text()


for route in UI_ROUTES:
    app.add_api_route(route, index, response_class=HTMLResponse, methods=["GET"], include_in_schema=False)

app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")


if __name__ == "__main__":
    # HOST/PORT from env so cloud hosts (Railway/Render/Fly) can bind correctly.
    # Local default stays 127.0.0.1 (not exposed); set HOST=0.0.0.0 to serve externally.
    host = os.environ.get("HOST", "127.0.0.1").strip()
    port = int(os.environ.get("PORT", "8000").strip())
    print(f"\n  TradeOS dashboard → http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
