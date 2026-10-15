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

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from core.settings import Settings
from core.agent import Agent
from core import state

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tradeos.server")

ROOT = Path(__file__).resolve().parent
settings = Settings.load()
agent = Agent(settings)

app = FastAPI(title="TradeOS Dashboard")

# ── access control ──
# If TRADEOS_TOKEN is set, the whole app (dashboard + every API/WS call) requires
# it. Unset = no auth, same as before (local dev). This matters on any host
# reachable from the public internet: without it, anyone with the URL can
# start/stop the bot, switch to Delta Demo, or close your positions.
AUTH_TOKEN = os.environ.get("TRADEOS_TOKEN", "")
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

    def _loop(self):
        while self.running:
            try:
                self.agent.evaluate()
            except Exception as e:
                log.error("eval error: %s", e)
            time.sleep(self.agent.s.poll_seconds)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.agent._feed("bot", "started")
        state.save(running=True)
        log.info("bot started")

    def stop(self):
        self.running = False
        self.agent._feed("bot", "stopped")
        state.save(running=False)
        log.info("bot stopped")


runner = BotRunner(agent)


def _resume_from_state():
    """On boot, restore the last desired state so the bot keeps running across
    restarts/redeploys until the user explicitly stops it."""
    st = state.load()
    if not st:
        return
    try:
        if st.get("strategy"):
            agent.set_strategy(st["strategy"])
        if st.get("mode"):
            agent.set_mode(st["mode"])
        if "auto" in st:
            agent.set_auto(st["auto"])
        if st.get("running"):
            runner.start()
            log.info("resumed running state from disk")
    except Exception as e:
        log.warning("could not resume state: %s", e)


_resume_from_state()


# ── REST ──
@app.get("/api/state")
def get_state():
    return JSONResponse(agent.snapshot() | {"running": runner.running})


@app.post("/api/bot/start")
def bot_start():
    runner.start()
    return {"running": True}


@app.post("/api/bot/stop")
def bot_stop():
    runner.stop()
    return {"running": False}


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
        state.save(strategy=name)
        return {"strategy": name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/signal/confirm")
def confirm():
    return {"result": agent.execute_pending().__dict__}


@app.post("/api/signal/dismiss")
def dismiss():
    return {"result": agent.dismiss_pending().__dict__}


@app.post("/api/position/close")
def close():
    return {"result": agent.force_close().__dict__}


@app.post("/api/evaluate")
def evaluate_once():
    """Force one evaluation now (useful when the bot loop is stopped)."""
    return {"result": agent.evaluate().__dict__}


@app.post("/api/ai/analysis")
def ai_analysis():
    """On-demand Gemini read of current chart conditions."""
    return agent.ai_analysis()


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


# ── static dashboard ──
@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "web" / "index.html").read_text()


app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")


if __name__ == "__main__":
    # HOST/PORT from env so cloud hosts (Railway/Render/Fly) can bind correctly.
    # Local default stays 127.0.0.1 (not exposed); set HOST=0.0.0.0 to serve externally.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  TradeOS dashboard → http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
