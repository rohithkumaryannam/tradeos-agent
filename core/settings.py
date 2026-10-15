"""Loads config.yaml + .env into a single typed settings object."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # dotenv optional; env vars may already be set

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RiskConfig:
    account_equity: float = 10000.0
    risk_per_trade_pct: float = 1.0
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0
    max_leverage: int = 5
    max_daily_loss_pct: float = 5.0


@dataclass
class Settings:
    mode: str = "PAPER"
    symbol: str = "BTCUSD"
    resolution: str = "5m"
    lookback: int = 200
    poll_seconds: int = 30
    strategy_name: str = "ny_range_fakeout"
    strategy_params: dict = field(default_factory=dict)
    risk: RiskConfig = field(default_factory=RiskConfig)
    log_level: str = "INFO"
    trade_log: str = "logs/trades.csv"
    api_key: str = ""
    api_secret: str = ""
    ai_narrate: bool = True   # AI one-line explanations in the live feed

    @classmethod
    def load(cls, path: str | Path = ROOT / "config.yaml") -> "Settings":
        with open(path, "r") as f:
            c = yaml.safe_load(f)

        risk = RiskConfig(**c.get("risk", {}))
        s = cls(
            mode=c.get("mode", "PAPER").upper(),
            symbol=c["market"]["symbol"],
            resolution=c["market"]["resolution"],
            lookback=c["market"].get("lookback", 200),
            poll_seconds=c["loop"].get("poll_seconds", 30),
            strategy_name=c["strategy"]["name"],
            strategy_params=c["strategy"].get("params", {}),
            risk=risk,
            log_level=c.get("logging", {}).get("level", "INFO"),
            trade_log=c.get("logging", {}).get("trade_log", "logs/trades.csv"),
            # .strip(): hosting dashboards (Render, Railway, ...) commonly add a
            # trailing newline when a value is pasted into a multi-line env var
            # box; an API key with a stray \n fails header validation outright.
            api_key=os.getenv("DELTA_API_KEY", "").strip(),
            api_secret=os.getenv("DELTA_API_SECRET", "").strip(),
            ai_narrate=c.get("ai", {}).get("narrate", True),
        )
        if s.mode == "DELTA_DEMO" and (not s.api_key or not s.api_secret):
            raise RuntimeError(
                "DELTA_DEMO mode needs DELTA_API_KEY and DELTA_API_SECRET in .env"
            )
        return s
