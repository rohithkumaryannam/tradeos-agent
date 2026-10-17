"""Loads config.yaml + .env into a single typed settings object."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
import math

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
    max_portfolio_risk_pct: float = 3.0
    max_open_positions: int = 3
    min_reward_risk: float = 1.5
    max_stop_distance_pct: float = 5.0
    max_same_direction_positions: int = 2
    strategy_risk_pct: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        numeric = {name: value for name, value in vars(self).items()
                   if name != "strategy_risk_pct"}
        for name, value in numeric.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"risk.{name} must be a finite positive number")
        for name in ("max_open_positions", "max_same_direction_positions"):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"risk.{name} must be an integer")
        for name in ("risk_per_trade_pct", "stop_loss_pct", "max_daily_loss_pct", "max_portfolio_risk_pct"):
            if getattr(self, name) >= 100:
                raise ValueError(f"risk.{name} must be below 100")
        if not isinstance(self.strategy_risk_pct, dict):
            raise ValueError("risk.strategy_risk_pct must be a mapping")
        for strategy, value in self.strategy_risk_pct.items():
            if (not isinstance(strategy, str) or not strategy or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value <= 0 or value > self.risk_per_trade_pct):
                raise ValueError(
                    f"risk.strategy_risk_pct.{strategy} must be positive and no greater than risk_per_trade_pct"
                )

    def risk_for(self, strategy: str | None) -> float:
        return float(self.strategy_risk_pct.get(strategy, self.risk_per_trade_pct))


@dataclass
class Settings:
    mode: str = "PAPER"
    symbols: list = field(default_factory=lambda: ["BTCUSD"])
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
    def load(cls, path: str | Path | None = None, *, mode_override: str | None = None) -> "Settings":
        with open(path or ROOT / "config.yaml", "r") as f:
            c = yaml.safe_load(f)

        risk = RiskConfig(**c.get("risk", {}))
        sym = c["market"]["symbol"]
        symbols = sym if isinstance(sym, list) else [sym]
        s = cls(
            mode=(mode_override or c.get("mode", "PAPER")).upper(),
            symbols=symbols,
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
