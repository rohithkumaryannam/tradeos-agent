"""Dashboard backtest lab: replay one strategy on one coin over recent history.

The parent side (start/get/recent) is called from server.py. Each job runs in
a separate, low-priority child process (`python -m core.backtest_lab`) so a
long replay never steals the live trading loop's CPU or GIL, and so the child
can be stripped of every credential: it has no Mongo URI, no Delta keys and no
AI keys, on top of running inside db.offline(). Market data comes from Delta's
public candle endpoint and is cached under data/research/cache/.

Results are research, not a promise: closed-bar signals, next-open fills,
fees and slippage on every fill, funding and liquidity not modelled.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "research" / "cache"
PUBLIC_API = "https://api.india.delta.exchange"
MAX_DAYS = 365
TIMEOUT_SECONDS = 20 * 60
_SECRET_ENV = ("MONGODB_URI", "DELTA_API_KEY", "DELTA_API_SECRET", "TRADEOS_TOKEN",
               "OPENROUTER_API_KEY", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN",
               "SMTP_PASSWORD", "TRADEOS_ALERT_WEBHOOK_URL", "DELTA_PROXY_URL")

_jobs: dict = {}
_lock = threading.Lock()


# ── parent side ───────────────────────────────────────────────────────────
def validate(raw: dict, *, risk: dict, default_risk_pct: float) -> dict:
    """Normalise a dashboard request; raises ValueError with a readable message."""
    from core.agent import Agent, strategy_meta
    from strategies.loader import available_strategies

    if not isinstance(raw, dict):
        raise ValueError("expected an object")
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not re.match(r"^[A-Z0-9]{2,24}$", symbol):
        raise ValueError("pick a market")
    strategy = str(raw.get("strategy") or "")
    if strategy in ("combo", "paused") or strategy not in available_strategies():
        raise ValueError(f"unknown strategy: {strategy or '—'}")
    params = Agent._clean_params(strategy, raw.get("params") or {})
    meta = strategy_meta(strategy)
    resolution = str(raw.get("resolution") or meta.get("resolution") or "15m")
    if meta.get("resolutions") and resolution not in meta["resolutions"]:
        raise ValueError(f"{strategy} supports {', '.join(meta['resolutions'])} candles")

    def number(name, default, low, high, cast=float):
        value = raw.get(name, default)
        try:
            value = float(default if value in (None, "") else value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(value) or not low <= value <= high or (cast is int and not value.is_integer()):
            raise ValueError(f"{name} must be between {low:g} and {high:g}")
        return cast(value)

    return {
        "symbol": symbol, "strategy": strategy, "params": params, "resolution": resolution,
        "days": number("days", 30, 1, MAX_DAYS, int),
        "fee_bps": number("fee_bps", 5, 0, 100),
        "slippage_bps": number("slippage_bps", 2, 0, 100),
        "equity": number("equity", 10_000, 100, 10_000_000),
        "risk_pct": number("risk_pct", default_risk_pct, 0.05, 5),
        "bracket_owns_exit": raw.get("bracket_owns_exit", True) is not False,
        "risk": {k: risk[k] for k in (
            "stop_loss_pct", "take_profit_pct", "min_reward_risk", "max_stop_distance_pct",
            "max_leverage", "max_daily_loss_pct", "max_portfolio_risk_pct",
            "max_open_positions", "max_same_direction_positions",
            "max_consecutive_losses", "strategy_cooldown_minutes") if k in risk},
        "label": str(raw.get("label") or "")[:60],
    }


def _public(job: dict, full: bool = False) -> dict:
    out = {k: v for k, v in job.items() if k not in ("result", "_proc")}
    if full:
        out["result"] = job.get("result")
    return out


def start(params: dict) -> dict:
    with _lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise ValueError("a backtest is already running — wait for it to finish")
        job = {"id": uuid.uuid4().hex[:10], "status": "running", "stage": "starting",
               "progress": 0.0, "params": params, "started_at": time.time(),
               "finished_at": None, "error": None, "result": None}
        _jobs[job["id"]] = job
        for old in sorted(_jobs.values(), key=lambda j: j["started_at"])[:-12]:
            _jobs.pop(old["id"], None)
    threading.Thread(target=_supervise, args=(job,), daemon=True).start()
    return _public(job)


def get(job_id: str) -> dict | None:
    job = _jobs.get(job_id)
    return _public(job, full=True) if job else None


def recent() -> list:
    return [_public(j) for j in sorted(_jobs.values(), key=lambda j: -j["started_at"])]


def cancel(job_id: str) -> bool:
    job = _jobs.get(job_id)
    proc = job and job.get("_proc")
    if not proc or job["status"] != "running":
        return False
    proc.kill()
    job.update(status="cancelled", stage="cancelled", finished_at=time.time())
    return True


def _supervise(job: dict):
    env = dict(os.environ)
    for key in _SECRET_ENV:
        env[key] = ""  # present-but-empty: load_dotenv() won't refill it from .env
    env["PYTHONUNBUFFERED"] = "1"

    def lower_priority():
        try:
            os.nice(15)
        except OSError:
            pass

    err = tempfile.TemporaryFile(mode="w+")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "core.backtest_lab", json.dumps(job["params"])],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=err, text=True,
            preexec_fn=lower_priority if os.name == "posix" else None,
        )
        job["_proc"] = proc
        timer = threading.Timer(TIMEOUT_SECONDS, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "progress" in msg:
                    job["progress"] = float(msg["progress"])
                if "stage" in msg:
                    job["stage"] = msg["stage"]
                if "result" in msg:
                    job.update(result=msg["result"], status="done", stage="done", progress=1.0)
                if "error" in msg:
                    job.update(error=msg["error"], status="error", stage="failed")
            proc.wait(timeout=30)
        finally:
            timer.cancel()
        if job["status"] == "running":
            err.seek(0)
            tail = err.read()[-400:].strip()
            job.update(status="error", stage="failed",
                       error=("timed out" if proc.returncode in (-9, 137) else tail or f"exit code {proc.returncode}"))
    except Exception as e:  # never let a lab failure touch the server
        job.update(status="error", stage="failed", error=str(e)[:400])
    finally:
        job.pop("_proc", None)
        job["finished_at"] = time.time()
        err.close()


# ── child side ────────────────────────────────────────────────────────────
class _ReplayClient:
    """Offline market data for the replay agent; no order or account methods."""
    offline_replay = True

    def __init__(self, contract_size: float):
        self.contract_size = contract_size

    def get_product(self, symbol):
        return {"contract_value": self.contract_size}

    def get_candles(self, *args):
        return []


def _load_candles(symbol: str, resolution: str, days: int, warmup_bars: int, emit) -> tuple:
    from core.agent import _RES_SECONDS
    from core.delta_client import DeltaClient

    seconds = _RES_SECONDS[resolution]
    end = int(time.time()) // seconds * seconds          # exclude the forming bar
    start = end - days * 86400 - warmup_bars * seconds
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}-{resolution}.json"
    cached = {}
    try:
        cached = json.loads(path.read_text())
    except (OSError, ValueError):
        cached = {}
    rows = {int(c["time"]): c for c in cached.get("candles", [])}
    contract_size = cached.get("contract_size")

    public = DeltaClient(base_url=PUBLIC_API)
    if not contract_size:
        try:  # size positions like the live (testnet) product when possible
            contract_size = float(DeltaClient().get_product(symbol).get("contract_value") or 0)
        except Exception:
            contract_size = 0
        if not contract_size:
            contract_size = float(public.get_product(symbol).get("contract_value") or 0)
    if not contract_size or not math.isfinite(contract_size):
        raise ValueError(f"{symbol}: no contract specification on Delta")

    have = sorted(t for t in rows if start <= t < end)
    fresh = time.time() - float(cached.get("fetched_at") or 0) < 15 * 60
    missing_head = not have or have[0] > start
    missing_tail = not have or have[-1] < end - seconds or not fresh
    ranges = []
    if missing_head:
        ranges.append((start, have[0] if have else end))
    if missing_tail and have:
        ranges.append((max(have[-1] - seconds * 2, start), end))
    total = sum(max(0, (b - a) // seconds) for a, b in ranges) or 1
    fetched = 0
    for a, b in ranges:
        for cursor in range(a, b, seconds * 1000):
            chunk_end = min(b, cursor + seconds * 1000)
            for candle in public.get_candles(symbol, resolution, cursor, chunk_end):
                stamp = int(candle["time"])
                if cursor <= stamp < chunk_end:
                    rows[stamp] = candle
            fetched += (chunk_end - cursor) // seconds
            emit({"stage": "downloading candles", "progress": round(min(fetched / total, 1) * 0.2, 4)})
    if ranges:
        keep = {t: c for t, c in rows.items() if t >= end - 400 * 86400}
        path.write_text(json.dumps({"symbol": symbol, "resolution": resolution,
                                    "contract_size": contract_size, "fetched_at": time.time(),
                                    "candles": [keep[t] for t in sorted(keep)]}))

    series = [rows[t] for t in sorted(rows) if start <= t < end]
    if len(series) < warmup_bars + 50:
        raise ValueError(f"{symbol}: not enough {resolution} history on Delta for this range")
    # Fill short gaps (bars with no trades) with flat candles so the replay
    # sees a continuous series, and report how many were synthetic.
    filled, gaps = [], 0
    for candle in series:
        if filled:
            prev = filled[-1]
            t = int(prev["time"]) + seconds
            while t < int(candle["time"]):
                close = float(prev["close"])
                filled.append({"time": t, "open": close, "high": close, "low": close,
                               "close": close, "volume": 0})
                gaps += 1
                t += seconds
        filled.append({"time": int(candle["time"]), "open": float(candle["open"]),
                       "high": float(candle["high"]), "low": float(candle["low"]),
                       "close": float(candle["close"]), "volume": float(candle.get("volume") or 0)})
    return filled, contract_size, gaps


def run(params: dict, emit) -> dict:
    from core import db
    from core.agent import Agent, strategy_meta
    from core.backtest import simulate
    from core.settings import RiskConfig, Settings
    from strategies.loader import load_strategy

    with db.offline():
        strategy, symbol, resolution = params["strategy"], params["symbol"], params["resolution"]
        required = int(getattr(load_strategy(strategy, params["params"]), "required_candles", 50) or 50)
        lookback = max(300, required + 5)
        emit({"stage": "downloading candles", "progress": 0.0})
        candles, contract_size, gaps = _load_candles(symbol, resolution, params["days"], lookback, emit)
        emit({"stage": "simulating", "progress": 0.2})
        risk = RiskConfig(account_equity=params["equity"], risk_per_trade_pct=params["risk_pct"],
                          **params.get("risk", {}))
        with tempfile.TemporaryDirectory(prefix="tradeos-lab-") as directory:
            settings = Settings(mode="PAPER", symbols=[symbol], resolution=resolution,
                                lookback=lookback, strategy_name=strategy,
                                strategy_params=params["params"], risk=risk, ai_narrate=False,
                                trade_log=str(Path(directory) / "trades.csv"))
            agent = Agent(settings, client=_ReplayClient(contract_size))
            report = simulate(
                agent, candles, warmup=lookback - 1, fee_bps=params["fee_bps"],
                slippage_bps=params["slippage_bps"], bracket_owns_exit=params["bracket_owns_exit"],
                progress=lambda done, total: emit({"progress": round(0.2 + 0.8 * done / total, 4)}),
            )
            trades = list(agent.trades)
    report.pop("assumptions", None)
    return {
        **report,
        "equity_samples": report.get("equity_samples", []),
        "trades": trades[-500:],
        "trade_total": len(trades),
        "gaps_filled": gaps,
        "params": {**strategy_meta(strategy)["defaults"], **params["params"]},
        "request": {k: v for k, v in params.items() if k != "risk"},
        "assumptions": ("Closed-bar signals, next-open fills, fees and adverse slippage on every "
                        "fill, stop-first when both brackets touch. "
                        + ("Exits only on stop-loss/take-profit, like live (exchange bracket owns the exit). "
                           if params["bracket_owns_exit"] else "Strategy exit signals honoured (research mode). ")
                        + "Funding, liquidation and liquidity are not modelled. Research only."),
    }


def _main():
    def emit(obj):
        sys.stdout.write(json.dumps(obj, default=str) + "\n")
        sys.stdout.flush()
    try:
        emit({"result": run(json.loads(sys.argv[1]), emit)})
    except Exception as e:
        emit({"error": str(e)[:500]})


if __name__ == "__main__":
    _main()
