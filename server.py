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
import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.settings import Settings
from core.agent import Agent

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tradeos.server")

ROOT = Path(__file__).resolve().parent
settings = Settings.load()
agent = Agent(settings)

app = FastAPI(title="TradeOS Dashboard")


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
        log.info("bot started")

    def stop(self):
        self.running = False
        self.agent._feed("bot", "stopped")
        log.info("bot stopped")


runner = BotRunner(agent)


# ── REST ──
@app.get("/api/state")
def state():
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
        return {"mode": mode}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/auto/{flag}")
def set_auto(flag: str):
    agent.set_auto(flag.lower() in ("1", "true", "on", "yes"))
    return {"auto": agent.auto}


@app.post("/api/strategy/{name}")
def set_strategy(name: str):
    try:
        agent.set_strategy(name)
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
    print("\n  TradeOS dashboard → http://127.0.0.1:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
