"""
The agent. Trades multiple symbols (e.g. BTCUSD, ETHUSD, SOLUSD) concurrently
against ONE shared account: equity, the daily loss cap and the trade ledger are
account-wide (matching the real Delta account, which has one balance), while
each symbol gets its own broker, strategy instance (so per-symbol state like an
armed setup or an owned position doesn't leak across markets), candles and
position.

Separates DECISION from EXECUTION so the dashboard can run it in auto mode
(execute immediately) or manual mode (surface a pending signal and wait for the
user to confirm), per symbol.

Key methods:
  evaluate()         -> runs one cycle for every symbol; returns {symbol: Outcome}
  execute_pending(s) -> in manual mode, act on symbol s's last pending signal
  snapshot()         -> full state dict for the UI / WebSocket
"""
from __future__ import annotations
import csv
import functools
import json
import logging
import os
import re
import threading
import time
import math
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from core.settings import RiskConfig, Settings
from core.delta_client import DeltaClient, DeltaError
from core.broker import PaperBroker, DeltaBroker
from core.indicators import ema, rsi, closes
from core.risk import RiskEngine
from core.trade_log import TradeLog
from core import ai, db, alerts
from strategies.base import Action, Signal
from strategies.loader import load_strategy, available_strategies

log = logging.getLogger("tradeos")

_RES_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}


@dataclass
class Outcome:
    event: str = "none"          # none | entry | exit | reject | pending | hold
    detail: str = ""
    signal: str = ""             # the Action string if pending


@dataclass
class Market:
    """Per-symbol state: its own broker, strategy instance, candles and
    pending signal. A strategy instance is NOT shared across symbols because a
    strategy can hold per-symbol state (an armed setup, "already traded today")."""
    symbol: str
    broker: object = None
    strategy: object = None
    last_candles: list = field(default_factory=list)
    open_trade: dict | None = None
    pending: Signal | None = None
    pending_price: float | None = None
    pending_at: float = 0.0
    last_fetch: float = 0.0
    last_error: str | None = None
    last_outcome: Outcome = field(default_factory=Outcome)
    last_entry_candle: float | None = None
    last_rejected_entry: tuple | None = None
    last_reconcile_fingerprint: tuple | None = None


@dataclass
class ShadowMarket:
    broker: object
    strategy: object
    open_trade: dict | None = None
    last_entry_candle: float | None = None


@dataclass
class StrategyLane:
    """One strategy's isolated paper account on one real market feed.

    Delta exposes one net position per product, so combo strategies cannot
    safely own separate live BTCUSD/ETHUSD positions.  Lanes remove that
    waiting constraint for strategy evaluation without creating conflicting
    exchange orders: every sub-strategy gets its own broker and risk ledger.
    """
    symbol: str
    tag: str
    resolution: str
    broker: object
    strategy: object
    risk: RiskEngine
    open_trade: dict | None = None
    last_entry_candle: float | None = None
    opened_market_candle: float | None = None
    last_action: str = "WARMING_UP"
    last_reason: str = "waiting for market data"
    last_evaluated: str | None = None
    error: str | None = None


def _round_price(p: float | None) -> float | None:
    """Round a price for storage/display, scaling decimals to its own
    magnitude. A flat round(p, 1) silently destroys sub-$1 prices — e.g.
    DOGE ~0.079 -> 0.1, or a micro-cap like SHIB's ~0.005 -> 0.0 — which
    corrupted the trade journal's entry/exit prices for any coin under $1."""
    if p is None:
        return None
    v = abs(p)
    d = 1 if v >= 100 else 2 if v >= 1 else 4 if v >= 0.01 else 6 if v >= 0.0001 else 8
    return round(p, d)


def _price_str(p: float | None) -> str:
    """Like _round_price, but for the persisted trade-log text fields
    (tlog.write): a flat f"{p:.1f}" was the ORIGINAL source of the bug above
    — it wrote "0.0" for SHIB's ~0.005 straight into the log, and that
    corrupted 0.0 got read back as the position's real entry price after
    every restart, producing wildly wrong realized P&L once the position
    closed (a -$93.90 "loss" that was actually a small real profit)."""
    r = _round_price(p)
    return "" if r is None else str(r)


def _as_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@functools.lru_cache(maxsize=None)
def strategy_meta(name: str) -> dict:
    """Defaults, timeframe and one-paragraph description of a strategy
    module, for the dashboard. Cached: modules don't change at runtime."""
    import importlib
    import inspect
    strategy = load_strategy(name, {})
    doc = inspect.getdoc(importlib.import_module(f"strategies.{name}")) or ""
    return {
        "defaults": dict(strategy.params),
        "resolution": getattr(strategy, "preferred_resolution", None),
        "resolutions": list(getattr(strategy, "supported_resolutions", None) or []),
        "required_candles": getattr(strategy, "required_candles", None),
        "description": " ".join(doc.split("\n\n")[0].split()),
    }


class Agent:
    def __init__(self, settings: Settings, client: DeltaClient | None = None, clock=None):
        self.s = settings
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.client = client or DeltaClient(settings.api_key, settings.api_secret)
        self.risk = RiskEngine(settings.risk, clock=self.clock)
        self.tlog = TradeLog(settings.trade_log)
        self._acct = {"balances": [], "positions": [], "usd": None,
                      "status": "n/a", "error": None, "code": None,
                      "client_ip": None, "ts": 0.0, "retry_at": 0.0,
                      "failures": 0}

        self.markets: dict[str, Market] = {sym: Market(sym) for sym in settings.symbols}
        for m in self.markets.values():
            self._build_strategy(m)
            self._build_broker(m)
        self._align_strategy_data()
        self.shadow_risk = RiskEngine(replace(settings.risk), clock=self.clock)
        self.shadow_markets: dict[str, ShadowMarket] = {}
        self.strategy_lanes: dict[str, StrategyLane] = {}
        self._lane_candle_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._build_shadow_execution()
        self.shadow_trades: list[dict] = []

        # structured completed-trade ledger (persists via the CSV log)
        self.trades: list[dict] = []
        self.trade_history_limit = 5000
        self._load_trades_from_log()

        self.auto = True                 # auto-execute vs manual confirm
        self.entries_paused = False
        self.execution_enabled = True
        self.execution_reason = "single local instance"
        self.last_cycle_at = None
        self.last_cycle_ms = None
        self.equity_curve: list[dict] = [
            {"t": self._now(), "equity": self.risk.equity}
        ]
        self.recent: list[dict] = []     # recent events for the UI feed
        if self.s.mode == "DELTA_DEMO":
            self._refresh_account(force=True)
            if self._acct["status"] == "live" and self._acct["usd"] is not None:
                self._reseed_equity(self._acct["usd"])
        log.info("Agent ready | mode=%s strategy=%s %s %s",
                 settings.mode, settings.strategy_name,
                 ",".join(settings.symbols), settings.resolution)

    # ── setup helpers ──
    def _contract_value_fn(self, symbol: str):
        """USD value of 1 contract, from Delta's own product spec (public
        endpoint — works without API keys). Unknown specs block sizing."""
        try:
            cv = float(self.client.get_product(symbol).get("contract_value", 0))
        except Exception as e:
            log.warning("could not fetch contract value for %s; entries blocked: %s", symbol, e)
            cv = 0
        return lambda price, _cv=cv: _cv * price

    def _build_broker(self, m: Market):
        if self.s.mode == "DELTA_DEMO":
            # One shared account refresh reconciles every broker. Avoid one
            # identical signed request per symbol when account access is down.
            m.broker = DeltaBroker(self.client, m.symbol, reconcile=False)
        else:
            m.broker = PaperBroker(self._contract_value_fn(m.symbol))

    def _build_strategy(self, m: Market):
        # _symbol/_fetch_candles let a strategy (e.g. combo, for a
        # multi-timeframe sub) pull its own candles at a resolution other
        # than the agent's shared one. Ordinary strategies accept and ignore
        # these via **kw (BaseStrategy stores unknown params, doesn't use
        # them), so this is safe to pass unconditionally.
        params = {**self.s.strategy_params, "_symbol": m.symbol, "_fetch_candles": self.fetch_candles}
        m.strategy = load_strategy(self.s.strategy_name, params)

    def _build_shadow_execution(self):
        """Build either the legacy single shadow or one lane per combo sub."""
        self.shadow_markets = {}
        self.strategy_lanes = {}
        self._lane_candle_cache = {}
        if not self.s.shadow_paper:
            return
        if self.s.strategy_name != "combo":
            for sym, market in self.markets.items():
                self.shadow_markets[sym] = ShadowMarket(
                    PaperBroker(lambda price, m=market: m.broker.contract_value(price)),
                    load_strategy(self.s.strategy_name,
                                  {**self.s.strategy_params, "_symbol": sym,
                                   "_fetch_candles": self.fetch_candles}),
                )
            return

        specs = self.s.strategy_params.get("subs") or []
        for sym, market in self.markets.items():
            for spec in specs:
                if isinstance(spec, str):
                    sub_name, params, tag, symbols = spec, {}, spec, None
                else:
                    sub_name = spec["name"]
                    params = spec.get("params") or {}
                    tag = spec.get("tag", sub_name)
                    symbols = spec.get("symbols")
                if symbols and sym not in symbols:
                    continue
                if isinstance(spec, dict) and spec.get("enabled", True) is False:
                    sub_name, params = "paused", {}
                strategy = load_strategy(sub_name, params)
                resolution = getattr(strategy, "preferred_resolution", self.s.resolution)
                key = f"{sym}:{tag}"
                if key in self.strategy_lanes:
                    raise RuntimeError(f"duplicate paper strategy lane: {key}")
                self.strategy_lanes[key] = StrategyLane(
                    symbol=sym,
                    tag=tag,
                    resolution=resolution,
                    broker=PaperBroker(lambda price, m=market: m.broker.contract_value(price)),
                    strategy=strategy,
                    risk=RiskEngine(replace(self.s.risk), clock=self.clock),
                )

    def reload_strategy(self):
        for m in self.markets.values():
            self._build_strategy(m)
        self._align_strategy_data()

    def _align_strategy_data(self):
        """Keep candle settings compatible with the active strategy."""
        strategies = [m.strategy for m in self.markets.values()]
        if not strategies:
            return
        self.s.lookback = max(
            self.s.lookback,
            max(getattr(strategy, "required_candles", 0) for strategy in strategies) + 5,
        )
        strategy = strategies[0]
        supported = getattr(strategy, "supported_resolutions", None)
        preferred = getattr(strategy, "preferred_resolution", None)
        if supported and self.s.resolution not in supported and preferred:
            log.warning("strategy %s does not support %s; using %s",
                        self.s.strategy_name, self.s.resolution, preferred)
            self.s.resolution = preferred

    def set_strategy(self, name: str):
        """Switch the active strategy (applies to every symbol) from the UI.
        Uses the strategy's own defaults (config params only apply to the
        config-selected strategy)."""
        with self.lock:
            if name not in available_strategies():
                raise RuntimeError(f"unknown strategy: {name}")
            if name == self.s.strategy_name:
                self._align_strategy_data()
                return
            if self.s.mode == "DELTA_DEMO" and self._acct["status"] != "live":
                raise RuntimeError(
                    "Delta account unavailable; cannot verify positions before changing strategy"
                )
            if any(m.broker.position for m in self.markets.values()):
                raise RuntimeError("close open positions before changing strategy")
            candidates = {sym: load_strategy(name, {"_symbol": sym, "_fetch_candles": self.fetch_candles})
                          for sym in self.markets}
            self.s.strategy_name = name
            self.s.strategy_params = {}
            for sym, m in self.markets.items():
                m.strategy = candidates[sym]
                m.pending = None
                m.last_candles = []
                m.last_entry_candle = None
            if self.s.shadow_paper:
                self._build_shadow_execution()
            self._align_strategy_data()
            self._feed("mode", f"strategy -> {name}")

    def set_mode(self, mode: str):
        with self.lock:
            if mode not in ("PAPER", "DELTA_DEMO"):
                raise RuntimeError("invalid trading mode")
            if mode == "DELTA_DEMO" and (not self.s.api_key or not self.s.api_secret):
                raise RuntimeError("Delta Demo requires API credentials")
            if mode == self.s.mode:
                return
            open_syms = [m.symbol for m in self.markets.values() if m.broker.position is not None]
            if open_syms:
                raise RuntimeError(f"close open position(s) first: {', '.join(open_syms)}")
            brokers = {sym: (DeltaBroker(self.client, sym, reconcile=False) if mode == "DELTA_DEMO"
                            else PaperBroker(self._contract_value_fn(sym)))
                       for sym in self.markets}
            self.s.mode = mode
            for sym, m in self.markets.items():
                m.broker = brokers[sym]
                m.pending = None
            self._feed("mode", f"switched to {mode}")
            # Re-base equity on the authoritative source for the new mode:
            # live Delta balance for DEMO, configured starting equity for PAPER.
            if mode == "DELTA_DEMO":
                self._refresh_account(force=True)
                if self._acct["status"] == "live" and self._acct["usd"] is not None:
                    self._reseed_equity(self._acct["usd"])
                    self._feed("mode", f"equity re-based on Delta balance "
                                        f"{self._acct['usd']:.2f} USD")
            else:
                self._reseed_equity(self.s.risk.account_equity)

    def set_auto(self, auto: bool):
        with self.lock:
            self.auto = auto
            for m in self.markets.values():
                m.pending = None
            self._feed("mode", "auto-execute ON" if auto else "manual confirm ON")

    def pause_entries(self, paused: bool):
        with self.lock:
            self.entries_paused = paused
            for m in self.markets.values():
                if m.pending and m.pending.action != Action.EXIT:
                    m.pending = None
            self._feed("risk", "new entries paused; exits remain active" if paused else "new entries resumed")

    def update_risk_controls(self, values: dict) -> RiskConfig:
        """Validate and atomically apply the dashboard's editable guardrails."""
        ranges = {
            "max_portfolio_risk_pct": (0.1, 99.9, float),
            "max_open_positions": (1, 1_000_000, int),
            "max_same_direction_positions": (1, 1_000_000, int),
            "max_leverage": (1, 1_000_000, int),
            "max_daily_loss_pct": (0.1, 99.9, float),
        }
        updates = {}
        for name, (low, high, cast) in ranges.items():
            raw = values.get(name)
            if isinstance(raw, bool):
                raise ValueError(f"{name} must be between {low} and {high}")
            try:
                value = cast(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be between {low} and {high}")
            if value < low or value > high or (cast is int and float(raw) != value):
                raise ValueError(f"{name} must be between {low} and {high}")
            updates[name] = value
        if updates["max_same_direction_positions"] > updates["max_open_positions"]:
            raise ValueError("same-direction positions cannot exceed total position slots")

        raw_caps = values.get("strategy_risk_pct")
        if not isinstance(raw_caps, dict) or set(raw_caps) != set(self.s.risk.strategy_risk_pct):
            raise ValueError("provide a risk cap for every configured strategy")
        caps = {}
        for strategy, raw in raw_caps.items():
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"invalid risk cap for {strategy}")
            if not math.isfinite(value) or value < 0.05 or value > 5:
                raise ValueError(f"{strategy} risk must be between 0.05% and 5%")
            caps[strategy] = value
        updates["strategy_risk_pct"] = caps
        updates["risk_per_trade_pct"] = max(
            caps.values(), default=self.s.risk.risk_per_trade_pct
        )

        new_config = replace(self.s.risk, **updates)
        with self.lock:
            self.s.risk = new_config
            self.risk.cfg = new_config
            self.shadow_risk.cfg = new_config
            for lane in self.strategy_lanes.values():
                lane.risk.cfg = new_config
            self._feed("risk", "risk controls updated from dashboard")
        return new_config

    # ── per-coin strategy assignment (combo `subs`) ──
    def update_sub(self, tag: str, *, name: str | None = None,
                    params: dict | None = None, risk_pct: float | None = None,
                    enabled: bool | None = None, reset_params: bool = False) -> dict:
        """Change which strategy a combo slot (identified by its stable `tag`,
        e.g. "donchian_xrp") runs, and/or its params, per-trade risk cap and
        on/off switch — the self-service version of what used to require a
        config.yaml edit and a redeploy. Changing the strategy class itself
        is blocked while its symbol(s) have an open position (same guard as
        set_strategy); params, risk_pct and enabled only affect future
        entries, so they're always safe to change live. A disabled slot
        keeps its strategy and params but takes no new entries (an open
        position still closes on its own exchange bracket)."""
        with self.lock:
            subs = list(self.s.strategy_params.get("subs") or [])
            idx = next((i for i, s in enumerate(subs) if self._sub_tag(s) == tag), None)
            if idx is None:
                raise ValueError(f"unknown strategy slot: {tag}")
            sub = dict(subs[idx]) if isinstance(subs[idx], dict) else {"name": subs[idx]}
            sub.setdefault("tag", tag)
            affected = sub.get("symbols") or list(self.markets)
            unknown = [sym for sym in affected if sym not in self.markets]
            if unknown:
                raise ValueError(f"unknown symbol(s): {unknown}")

            if name is not None and name != sub.get("name"):
                if name == "combo" or name not in available_strategies():
                    raise ValueError(f"unknown strategy: {name}")
                if any(self.markets[sym].broker.position for sym in affected):
                    raise ValueError("close the open position before changing this slot's strategy")
                sub["name"] = name
                sub["params"] = {}  # old strategy's params don't apply to the new one

            if reset_params:
                sub["params"] = {}
            if params is not None:
                cleaned = self._clean_params(sub["name"], params)
                sub["params"] = {**sub.get("params", {}), **cleaned}
            if enabled is not None:
                if _as_bool(enabled):
                    sub.pop("enabled", None)
                else:
                    sub["enabled"] = False

            pct = None
            if risk_pct is not None:
                pct = self._validate_risk_pct(risk_pct)

            subs[idx] = sub
            self.s.strategy_params = {**self.s.strategy_params, "subs": subs}
            for m in self.markets.values():
                self._build_strategy(m)
            self._align_strategy_data()
            if self.s.shadow_paper:
                self._build_shadow_execution()

            if pct is not None:
                caps = {**self.s.risk.strategy_risk_pct, tag: pct}
                self._apply_risk_config(replace(self.s.risk, strategy_risk_pct=caps,
                                                risk_per_trade_pct=max(caps.values())))

            state_note = "" if sub.get("enabled", True) is not False else " [disabled]"
            self._feed("strategy", f"{tag} -> {sub['name']}{state_note} {sub.get('params') or ''}".strip())
            return sub

    def apply_strategy_subs(self, subs: list):
        """Boot-time restore of a previously saved update_sub() result — the
        list was already validated when it was saved, so just swap it in and
        rebuild every market's strategy instance."""
        with self.lock:
            self.s.strategy_params = {**self.s.strategy_params, "subs": subs}
            for m in self.markets.values():
                self._build_strategy(m)
            self._align_strategy_data()

    # ── control plane: everything the dashboard can change ──
    ENGINE_LIMITS = {"poll_seconds": (5, 3600), "lookback": (50, 5000)}
    RISK_LIMITS = {
        # field: (low, high, type) — the dashboard renders its inputs from this
        "account_equity": (10, 1_000_000_000, float),
        "stop_loss_pct": (0.05, 50, float),
        "take_profit_pct": (0.05, 500, float),
        "min_reward_risk": (0.1, 50, float),
        "max_stop_distance_pct": (0.1, 50, float),
        "max_leverage": (1, 1_000_000, int),
        "max_daily_loss_pct": (0.1, 99.9, float),
        "max_portfolio_risk_pct": (0.1, 99.9, float),
        "max_open_positions": (1, 1_000_000, int),
        "max_same_direction_positions": (1, 1_000_000, int),
        "max_consecutive_losses": (1, 1_000_000, int),
        "strategy_cooldown_minutes": (1, 1_000_000, int),
    }
    SLOT_RISK_RANGE = (0.05, 5.0)
    _SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,24}$")

    @staticmethod
    def _sub_tag(sub) -> str:
        return sub.get("tag", sub.get("name")) if isinstance(sub, dict) else sub

    def _subs(self) -> list:
        return list(self.s.strategy_params.get("subs") or [])

    def _slot_tags(self) -> set:
        return {self._sub_tag(s) for s in self._subs()}

    def _apply_risk_config(self, new_config: RiskConfig, note: str | None = None):
        with self.lock:
            self.s.risk = new_config
            self.risk.cfg = new_config
            self.shadow_risk.cfg = new_config
            for lane in self.strategy_lanes.values():
                lane.risk.cfg = new_config
            if note:
                self._feed("risk", note)

    def _validate_risk_pct(self, raw) -> float:
        low, high = self.SLOT_RISK_RANGE
        try:
            pct = float(raw)
        except (TypeError, ValueError):
            raise ValueError("risk_pct must be a number")
        if isinstance(raw, bool) or not math.isfinite(pct) or pct < low or pct > high:
            raise ValueError(f"risk_pct must be between {low}% and {high:g}%")
        return pct

    @staticmethod
    def _clean_params(strategy: str, params: dict) -> dict:
        """Validate dashboard-supplied params against the strategy's own
        defaults: unknown names are rejected and values are coerced to the
        default's type."""
        if not isinstance(params, dict):
            raise ValueError("params must be a mapping")
        try:
            defaults = strategy_meta(strategy)["defaults"]
        except Exception as e:
            raise ValueError(f"cannot load strategy {strategy}: {e}")
        bad = set(params) - set(defaults)
        if bad:
            raise ValueError(f"unknown params for {strategy}: {sorted(bad)}")
        cleaned = {}
        for key, raw in params.items():
            default = defaults[key]
            if isinstance(default, bool):
                cleaned[key] = _as_bool(raw)
            elif isinstance(default, (int, float)):
                try:
                    num = float(raw)
                except (TypeError, ValueError):
                    raise ValueError(f"invalid value for {key}")
                if isinstance(raw, bool) or not math.isfinite(num):
                    raise ValueError(f"invalid value for {key}")
                if isinstance(default, int) and not num.is_integer():
                    raise ValueError(f"{key} must be a whole number")
                cleaned[key] = int(num) if isinstance(default, int) else num
            else:
                cleaned[key] = raw
        return cleaned

    def engine_settings(self) -> dict:
        return {"poll_seconds": self.s.poll_seconds, "lookback": self.s.lookback,
                "resolution": self.s.resolution, "ai_narrate": bool(self.s.ai_narrate)}

    def update_engine(self, values: dict) -> dict:
        """Loop interval, candle lookback, base timeframe and AI narration."""
        if not isinstance(values, dict):
            raise ValueError("expected an object")
        updates = {}
        for key, (low, high) in self.ENGINE_LIMITS.items():
            raw = values.get(key)
            if raw is None:
                continue
            try:
                num = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a whole number between {low} and {high}")
            if isinstance(raw, bool) or not math.isfinite(num) or not num.is_integer() or not low <= num <= high:
                raise ValueError(f"{key} must be a whole number between {low} and {high}")
            updates[key] = int(num)
        if values.get("resolution") is not None:
            res = str(values["resolution"])
            if res not in _RES_SECONDS:
                raise ValueError(f"unknown timeframe: {res}")
            updates["resolution"] = res
        if values.get("ai_narrate") is not None:
            updates["ai_narrate"] = _as_bool(values["ai_narrate"])
        with self.lock:
            for key, value in updates.items():
                setattr(self.s, key, value)
            self._align_strategy_data()
            self._lane_candle_cache = {}
            if updates:
                self._feed("mode", "engine settings updated: " +
                           ", ".join(f"{k}={v}" for k, v in updates.items()))
        return self.engine_settings()

    def risk_settings(self) -> dict:
        r = self.s.risk
        out = {name: getattr(r, name) for name in self.RISK_LIMITS}
        out["risk_per_trade_pct"] = r.risk_per_trade_pct
        out["strategy_risk_pct"] = dict(r.strategy_risk_pct)
        return out

    def update_risk_settings(self, values: dict, *, strict: bool = True) -> RiskConfig:
        """Partial update of ANY risk field (the older update_risk_controls
        needs the full editable set). strict=False is for restoring a saved
        override at boot: risk caps for slots that no longer exist are
        dropped instead of failing the whole restore."""
        if not isinstance(values, dict):
            raise ValueError("expected an object")
        updates = {}
        for name, (low, high, cast) in self.RISK_LIMITS.items():
            raw = values.get(name)
            if raw is None:
                continue
            kind = "a whole number " if cast is int else ""
            try:
                num = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be {kind}between {low:g} and {high:g}")
            if (isinstance(raw, bool) or not math.isfinite(num) or num < low or num > high
                    or (cast is int and not num.is_integer())):
                raise ValueError(f"{name} must be {kind}between {low:g} and {high:g}")
            updates[name] = cast(num)
        caps = dict(self.s.risk.strategy_risk_pct)
        raw_caps = values.get("strategy_risk_pct")
        if raw_caps is not None:
            if not isinstance(raw_caps, dict):
                raise ValueError("strategy_risk_pct must be a mapping")
            known = self._slot_tags() | set(caps)
            for tag, raw in raw_caps.items():
                if tag not in known:
                    if strict:
                        raise ValueError(f"unknown strategy slot: {tag}")
                    continue
                try:
                    caps[tag] = self._validate_risk_pct(raw)
                except ValueError:
                    if strict:
                        raise ValueError(f"{tag} risk must be between "
                                         f"{self.SLOT_RISK_RANGE[0]}% and {self.SLOT_RISK_RANGE[1]:g}%")
        if caps:
            updates["strategy_risk_pct"] = caps
            updates["risk_per_trade_pct"] = max(caps.values())
        total = updates.get("max_open_positions", self.s.risk.max_open_positions)
        same = updates.get("max_same_direction_positions", self.s.risk.max_same_direction_positions)
        if same > total:
            raise ValueError("same-direction positions cannot exceed total position slots")
        new_config = replace(self.s.risk, **updates)  # RiskConfig re-validates
        self._apply_risk_config(new_config, "risk controls updated")
        return new_config

    def slots(self) -> list[dict]:
        """Per-coin strategy assignments in a UI-friendly shape."""
        out = []
        for sub in self._subs():
            if not isinstance(sub, dict):
                sub = {"name": sub, "tag": sub}
            tag = self._sub_tag(sub)
            try:
                meta = strategy_meta(sub["name"])
            except Exception:
                meta = {"defaults": {}, "resolution": None}
            params = dict(sub.get("params") or {})
            out.append({
                "tag": tag, "name": sub["name"],
                "symbols": list(sub.get("symbols") or self.markets),
                "params": params,
                "effective_params": {**meta["defaults"], **params},
                "enabled": sub.get("enabled", True) is not False,
                "risk_pct": self.s.risk.risk_for(tag),
                "resolution": meta.get("resolution"),
            })
        return out

    def slot_for_symbol(self, symbol: str) -> dict | None:
        """The slot dedicated to exactly this symbol, else the first listing it."""
        slots = self.slots()
        return (next((s for s in slots if s["symbols"] == [symbol]), None)
                or next((s for s in slots if symbol in s["symbols"]), None))

    def add_market(self, symbol: str, *, strategy: str = "paused", params: dict | None = None,
                   risk_pct: float | None = None, tag: str | None = None) -> dict:
        """Start trading a new Delta perpetual with its own strategy slot."""
        symbol = str(symbol or "").strip().upper()
        if not self._SYMBOL_RE.match(symbol):
            raise ValueError("symbol must be 2-24 letters or digits, e.g. LTCUSD")
        if self.s.strategy_name != "combo":
            raise ValueError("adding markets needs the per-coin (combo) strategy setup")
        if strategy == "combo" or strategy not in available_strategies():
            raise ValueError(f"unknown strategy: {strategy}")
        cleaned = self._clean_params(strategy, params or {})
        pct = self._validate_risk_pct(0.25 if risk_pct is None else risk_pct)
        with self.lock:
            if symbol in self.markets:
                raise ValueError(f"{symbol} is already trading")
        # Exchange lookups happen outside the lock (the trading loop holds it
        # for a whole cycle).
        try:
            product = self.client.get_product(symbol)
            contract_value = float(product.get("contract_value") or 0)
        except Exception as e:
            raise ValueError(f"{symbol} was not found on the exchange ({str(e)[:120]})")
        if not math.isfinite(contract_value) or contract_value <= 0:
            raise ValueError(f"{symbol} has no usable contract specification")
        kind = product.get("contract_type")
        if kind and kind != "perpetual_futures":
            raise ValueError(f"{symbol} is a {kind} contract; only perpetual futures are supported")
        market = Market(symbol)
        self._build_broker(market)
        with self.lock:
            if symbol in self.markets:
                raise ValueError(f"{symbol} is already trading")
            base = re.sub(r"USDT?$", "", symbol).lower() or symbol.lower()
            wanted = re.sub(r"[^a-z0-9_]", "_", (tag or f"{strategy.split('_')[0]}_{base}").lower()).strip("_")
            taken = self._slot_tags() | set(self.s.risk.strategy_risk_pct)
            unique, n = wanted or base, 2
            while unique in taken:
                unique, n = f"{wanted}_{n}", n + 1
            sub = {"name": strategy, "tag": unique, "symbols": [symbol], "params": cleaned}
            self.s.strategy_params = {**self.s.strategy_params, "subs": self._subs() + [sub]}
            self.s.symbols = list(self.s.symbols) + [symbol]
            # Replace (never mutate) the dict: other threads may be iterating it.
            self.markets = {**self.markets, symbol: market}
            for m in self.markets.values():
                self._build_strategy(m)
            self._align_strategy_data()
            caps = {**self.s.risk.strategy_risk_pct, unique: pct}
            self._apply_risk_config(replace(self.s.risk, strategy_risk_pct=caps,
                                            risk_per_trade_pct=max(caps.values())))
            if self.s.shadow_paper:
                self._build_shadow_execution()
            self._feed("mode", f"market added: {symbol} -> {strategy} ({unique})")
            return sub

    def remove_market(self, symbol: str) -> dict:
        """Stop trading a symbol. Refused while anything is open on it."""
        symbol = str(symbol or "").strip().upper()
        with self.lock:
            m = self.markets.get(symbol)
            if m is None:
                raise ValueError(f"{symbol} is not trading")
            if len(self.markets) <= 1:
                raise ValueError("at least one market must remain")
            if m.broker.position is not None or m.open_trade:
                raise ValueError(f"close the open {symbol} position first")
            if self.s.mode == "DELTA_DEMO":
                if self._acct["status"] != "live":
                    raise ValueError("Delta account unavailable; cannot verify there is no exchange position")
                if any(p.get("symbol") == symbol for p in self._acct["positions"]):
                    raise ValueError(f"Delta still reports an open {symbol} position")
            subs, removed = [], []
            for sub in self._subs():
                listed = sub.get("symbols") if isinstance(sub, dict) else None
                if listed and symbol in listed:
                    rest = [x for x in listed if x != symbol]
                    if rest:
                        subs.append({**sub, "symbols": rest})
                    else:
                        removed.append(self._sub_tag(sub))
                else:
                    subs.append(sub)
            if self.s.strategy_name == "combo" and not subs:
                raise ValueError("cannot remove the last strategy slot")
            self.markets = {k: v for k, v in self.markets.items() if k != symbol}
            self.s.symbols = [x for x in self.s.symbols if x != symbol]
            if self.s.strategy_name == "combo":
                self.s.strategy_params = {**self.s.strategy_params, "subs": subs}
            for mk in self.markets.values():
                self._build_strategy(mk)
            self._align_strategy_data()
            remaining = {self._sub_tag(x) for x in subs}
            caps = {t: v for t, v in self.s.risk.strategy_risk_pct.items()
                    if t not in removed or t in remaining}
            if caps != self.s.risk.strategy_risk_pct:
                self._apply_risk_config(replace(
                    self.s.risk, strategy_risk_pct=caps,
                    risk_per_trade_pct=max(caps.values()) if caps else self.s.risk.risk_per_trade_pct))
            self.shadow_markets.pop(symbol, None)
            if self.s.shadow_paper:
                self._build_shadow_execution()
            self._feed("mode", f"market removed: {symbol}")
            return {"symbol": symbol, "removed_slots": removed}

    def manual_entry(self, symbol: str, side: str, *, risk_pct=None, sl_pct=None, tp_pct=None) -> Outcome:
        """Open a position by hand from the dashboard: market order, sized by
        the risk engine like any strategy entry, always with a stop-loss and
        take-profit, and subject to every portfolio limit. Attributed to the
        "manual" strategy so it never pollutes a coin's strategy stats."""
        side = str(side or "").lower()
        if side not in ("long", "short"):
            raise ValueError("side must be long or short")
        r = self.s.risk

        def pct(name, raw, default, low, high):
            if raw is None or raw == "":
                return float(default)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number")
            if isinstance(raw, bool) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low:g} and {high:g}")
            return value

        risk = pct("risk_pct", risk_pct, r.risk_per_trade_pct, *self.SLOT_RISK_RANGE)
        if risk > r.risk_per_trade_pct + 1e-9:
            # The risk engine rejects anything above the account's per-trade
            # ceiling (the largest per-coin risk cap); say so up front.
            raise ValueError(f"risk_pct can't exceed the per-trade ceiling of {r.risk_per_trade_pct:g}% "
                             f"(raise a coin's risk on the Risk page to lift it)")
        stop = pct("sl_pct", sl_pct, r.stop_loss_pct, 0.05, r.max_stop_distance_pct)
        target = pct("tp_pct", tp_pct, r.take_profit_pct, 0.05, 500)
        with self.lock:
            m = self._market(symbol)
            if m.broker.position is not None:
                return Outcome("reject", "position already open")
            if not m.last_candles:
                return Outcome("reject", "no market data yet; wait for the next cycle")
            issue = self._data_issue(m)
            if issue:
                return Outcome("reject", issue)
            price = float(m.last_candles[-1]["close"])
            if self.s.mode == "DELTA_DEMO":
                self._refresh_account(force=True)
            direction = 1 if side == "long" else -1
            sig = Signal(Action.ENTER_LONG if side == "long" else Action.ENTER_SHORT,
                         reason=f"manual {side} from dashboard (SL {stop:g}% / TP {target:g}%)",
                         stop_loss=price * (1 - direction * stop / 100),
                         take_profit=price * (1 + direction * target / 100),
                         strategy="manual")
            return self._open_from_signal(m, sig, price, risk_pct=risk, manual=True)

    def close_all(self) -> dict:
        """Market-close every open position (the dashboard's flatten button)."""
        results = {}
        with self.lock:
            for sym, m in list(self.markets.items()):
                if m.broker.position is not None:
                    out = self.force_close(sym)
                    results[sym] = {"event": out.event, "detail": out.detail}
        return results

    def reset_paper_equity(self) -> float:
        with self.lock:
            if self.s.mode != "PAPER":
                raise ValueError("only the paper account balance can be reset")
            if any(m.broker.position for m in self.markets.values()):
                raise ValueError("close paper positions before resetting the balance")
            self._reseed_equity(self.s.risk.account_equity)
            self._feed("mode", f"paper balance reset to {self.s.risk.account_equity:.2f}")
            return self.risk.equity

    _BAL_TTL = 20  # seconds between live account polls
    _ACCOUNT_MAX_BACKOFF = 300

    @staticmethod
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _refresh_account(self, force: bool = False):
        """DELTA_DEMO only: pull the full testnet account — every wallet asset
        balance plus live exchange positions across ALL symbols — cached ~20s
        so the signed endpoints aren't hammered. Fail-closed: on any error,
        mark the account unavailable rather than showing stale/fabricated data."""
        if self.s.mode != "DELTA_DEMO":
            self._acct = {"balances": [], "positions": [], "usd": None,
                          "status": "n/a", "error": None, "code": None,
                          "client_ip": None, "ts": self.clock(), "retry_at": 0.0,
                          "failures": 0}
            return
        now = self.clock()
        if not force and (now < self._acct.get("retry_at", 0)
                          or (self._acct["status"] == "live"
                              and now - self._acct["ts"] < self._BAL_TTL)):
            return
        previous = self._acct
        try:
            balances = [
                {"asset": e.get("asset_symbol") or "?",
                 "balance": self._f(e.get("balance")),
                 "available": self._f(e.get("available_balance"))}
                for e in (self.client.get_balance() or [])
            ]
            usd = next((b["balance"] for b in balances
                        if (b["asset"] or "").upper() in ("USD", "USDT")), None)
            raw_pos = self.client.get_positions()
            if usd is None or not math.isfinite(usd) or usd <= 0:
                raise DeltaError("USD account balance unavailable or non-positive")
            positions = [
                {"symbol": p.get("product_symbol") or p.get("product_id"),
                 "size": p.get("size"),
                 "entry": self._f(p.get("entry_price")),
                 "margin": self._f(p.get("margin")),
                 "liquidation": self._f(p.get("liquidation_price")),
                 "unrealized": self._f(p.get("unrealized_pnl")
                                       or p.get("unrealised_pnl"))}
                for p in (raw_pos or [])
                if (self._f(p.get("size")) or 0) != 0
            ]
            for m in self.markets.values():
                if isinstance(m.broker, DeltaBroker):
                    before = self._position_fingerprint(m.broker.position)
                    m.broker.reconcile(raw_pos)
                    after = self._position_fingerprint(m.broker.position)
                    if before != after and m.last_reconcile_fingerprint != after:
                        self._feed("reconcile", f"[{m.symbol}] exchange position changed outside "
                                   f"this instance: {before or 'flat'} -> {after or 'flat'}")
                    m.last_reconcile_fingerprint = after
                    if m.broker.position is None and m.open_trade:
                        self._record_exchange_exit(m)
            self._acct = {"balances": balances, "positions": positions,
                          "usd": usd, "status": "live", "error": None,
                          "code": None, "client_ip": None, "ts": now,
                          "retry_at": now + self._BAL_TTL, "failures": 0}
            if previous.get("status") == "unavailable":
                self._feed("account", "Delta account connection restored automatically")
                log.info("Delta account connection restored")
        except Exception as e:
            raw = str(e)[:1000]
            code = client_ip = None
            try:
                payload = json.loads(raw[raw.index("{"):])
                detail = payload.get("error") or {}
                code = detail.get("code")
                client_ip = (detail.get("context") or {}).get("client_ip")
            except (ValueError, TypeError, AttributeError):
                pass
            if code == "ip_not_whitelisted_for_api_key":
                message = (f"Delta rejected this server IP ({client_ip}). Add it to the "
                           "API-key whitelist; automatic retry is active.")
                delay = self._ACCOUNT_MAX_BACKOFF
            elif code in ("invalid_api_key", "unauthorized"):
                message = "Delta rejected the API credentials. Update them; automatic retry is active."
                delay = self._ACCOUNT_MAX_BACKOFF
            else:
                message = "Delta account connection failed. Automatic retry is active."
                failures = previous.get("failures", 0) + 1
                delay = min(self._ACCOUNT_MAX_BACKOFF, 5 * (2 ** min(failures - 1, 6)))
            failures = previous.get("failures", 0) + 1
            self._acct = {"balances": [], "positions": [], "usd": None,
                          "status": "unavailable", "error": message,
                          "code": code, "client_ip": client_ip, "ts": now,
                          "retry_at": now + delay, "failures": failures}
            # Report a persistent failure once. Later retries stay quiet unless
            # the error changes; the dashboard retains the current state.
            if previous.get("status") != "unavailable" or previous.get("code") != code:
                self._feed("account", message)
                log.warning("account fetch failed: %s", raw)

    def retry_account(self) -> dict:
        with self.lock:
            self._refresh_account(force=True)
            return {key: self._acct.get(key) for key in
                    ("status", "error", "code", "client_ip", "retry_at")}

    def _reseed_equity(self, equity: float):
        """Re-base the risk engine (and the equity curve) on a new starting
        equity — used when switching modes so sizing reflects the right account."""
        self.risk.equity = equity
        self.risk._day_start_equity = equity
        self.risk.realized_today = 0.0
        self.equity_curve = [{"t": self._now(), "equity": equity}]

    def _now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")

    def _feed(self, event, detail):
        self.recent.insert(0, {"t": self._now(), "event": event, "detail": detail})
        self.recent = self.recent[:50]
        if event in ("entry", "exit", "error", "account", "reconcile", "risk"):
            alerts.notify(event, detail)

    @staticmethod
    def _position_fingerprint(position):
        if position is None:
            return None
        return (position.side, int(position.size), round(float(position.entry), 8))

    def _strategy_guard(self, strategy: str) -> tuple[bool, str]:
        losses, last_exit = 0, None
        for trade in reversed(self.trades):
            if trade.get("strategy") != strategy:
                continue
            if trade.get("win"):
                break
            losses += 1
            last_exit = last_exit or trade.get("exit_ts")
        limit = self.s.risk.max_consecutive_losses
        if losses < limit or not last_exit:
            return True, ""
        try:
            ended = datetime.fromisoformat(last_exit.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            return True, ""
        remaining = self.s.risk.strategy_cooldown_minutes * 60 - (self.clock() - ended)
        if remaining <= 0:
            return True, ""
        return False, (f"{strategy} paused after {losses} consecutive losses; "
                       f"retry in {math.ceil(remaining / 60)}m")

    def _market(self, symbol: str | None) -> Market:
        if symbol is None:
            symbol = next(iter(self.markets))
        if symbol not in self.markets:
            raise RuntimeError(f"unknown symbol: {symbol}")
        return self.markets[symbol]

    # ── trade analytics ──
    @staticmethod
    def _classify_exit(reason: str) -> str:
        r = (reason or "").lower()
        if "stop-loss" in r or "stop loss" in r:
            return "SL"
        if "take-profit" in r or "take profit" in r:
            return "TP"
        if "manual" in r:
            return "manual"
        if "backtest" in r:
            return "backtest"
        return "strategy"

    @staticmethod
    def _dur_s(a: str, b: str):
        try:
            return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
        except Exception:
            return None

    def _mk_trade(self, symbol: str, e: dict, exit_ts, exit_price, pnl, reason) -> dict:
        """Build a completed-trade record from an entry dict + exit data."""
        entry, sl, size, side = e["entry"], e.get("sl"), e["size"], e["side"]
        stop_dist = abs(entry - sl) if sl else 0
        move = (exit_price - entry) if side == "long" else (entry - exit_price)
        r = round(move / stop_dist, 2) if stop_dist else None
        cv = e.get("contract_value") or 0.001 * entry
        notional = size * cv
        return {
            "symbol": symbol,
            "entry_ts": e.get("entry_ts"), "exit_ts": exit_ts,
            "strategy": e.get("strategy") or "—",
            "side": side, "entry": _round_price(entry), "exit": _round_price(exit_price),
            "size": size, "sl": _round_price(sl) if sl else None,
            "tp": _round_price(e.get("tp")) if e.get("tp") else None,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / notional * 100, 2) if notional else None,
            "r": r,
            "reason": self._classify_exit(reason),
            "win": pnl >= 0,
            "duration_s": self._dur_s(e.get("entry_ts"), exit_ts),
            "entry_note": (e.get("reason") or "")[:300],
            "exit_note": (reason or "")[:300],
        }

    def _load_trades_from_log(self):
        """Rebuild the completed-trade ledger by pairing ENTRY→EXIT rows (per
        symbol), so history survives restarts — including on hosts with an
        ephemeral filesystem (e.g. Render), where the local CSV resets on
        every redeploy but MongoDB (if configured) doesn't. Prefers Mongo;
        falls back to the local CSV. Best-effort; skips malformed rows and
        rows from a log written before the symbol column existed (they load
        with symbol '—')."""
        # A replay/backtest agent shares symbol names with the live bot; it
        # must never seed itself from the live Mongo trade log.
        replay = bool(getattr(self.client, "offline_replay", False))
        with (db.offline() if replay else nullcontext()):
            rows = db.load_trades()
        if rows is None:
            path = self.s.trade_log
            if not os.path.exists(path):
                return
            try:
                with open(path, newline="") as f:
                    rows = list(csv.DictReader(f))
            except Exception as e:
                log.warning("could not load trade history: %s", e)
                return
        try:
            pending: dict[str, dict] = {}
            for row in rows:
                ev = (row.get("event") or "").upper()
                sym = row.get("symbol") or next(iter(self.markets), "—")
                if ev == "ENTRY":
                    pending[sym] = {
                        "entry_ts": row.get("ts"), "side": row.get("side"),
                        "entry": float(row["price"]), "size": int(float(row["size"])),
                        "sl": float(row["sl"]) if row.get("sl") else None,
                        "tp": float(row["tp"]) if row.get("tp") else None,
                        "strategy": row.get("strategy") or "—",
                        "reason": row.get("reason") or "",
                    }
                elif ev == "EXIT" and sym in pending:
                    self.trades.append(self._mk_trade(
                        sym, pending[sym], row.get("ts"), float(row["price"]),
                        float(row.get("pnl") or 0), row.get("reason")))
                    del pending[sym]
            self.trades = self.trades[-self.trade_history_limit:]
            # Preserve unmatched entries across restarts. A live exchange
            # position will be reconciled next; a position already closed by
            # its bracket will be completed from Delta fills.
            for sym, entry in pending.items():
                m = self.markets.get(sym)
                if m:
                    entry["contract_value"] = m.broker.contract_value(entry["entry"])
                    m.open_trade = entry
        except Exception as e:
            log.warning("could not load trade history: %s", e)

    def _record_exchange_exit(self, m: Market) -> bool:
        """Complete a locally known trade from Delta's authoritative fills."""
        entry = m.open_trade
        if not entry or not isinstance(m.broker, DeltaBroker):
            return False
        try:
            fills = self.client.get_fills(m.broker.product_id)
            entry_at = datetime.fromisoformat(entry["entry_ts"].replace("Z", "+00:00"))
            exit_side = "sell" if entry["side"] == "long" else "buy"
            matches = []
            for fill in fills:
                created = fill.get("created_at")
                if fill.get("side") != exit_side or not created:
                    continue
                stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if stamp >= entry_at:
                    matches.append((stamp, fill))
            matches.sort(key=lambda item: item[0])
            remaining = int(entry["size"])
            value = 0.0
            used = 0
            exit_at = None
            for stamp, fill in matches:
                quantity = min(remaining, int(abs(float(fill.get("size") or 0))))
                if quantity <= 0:
                    continue
                value += quantity * float(fill["price"])
                used += quantity
                remaining -= quantity
                exit_at = stamp
                if remaining == 0:
                    break
            if remaining or not used or exit_at is None:
                return False
            exit_price = value / used
            base_per_contract = m.broker.contract_size
            move = (exit_price - entry["entry"]) if entry["side"] == "long" else (entry["entry"] - exit_price)
            pnl = move * base_per_contract * used
            sl, tp = entry.get("sl"), entry.get("tp")
            if sl is not None and tp is not None:
                reason = ("stop-loss filled on exchange" if abs(exit_price - sl) <= abs(exit_price - tp)
                          else "take-profit filled on exchange")
            else:
                reason = "closed on exchange"
            exit_ts = exit_at.astimezone(timezone.utc).isoformat(timespec="seconds")
            self.risk.register_pnl(pnl)
            self.equity_curve.append({"t": exit_ts, "equity": self.risk.equity})
            self.equity_curve = self.equity_curve[-500:]
            self.trades.append(self._mk_trade(m.symbol, entry, exit_ts, exit_price, pnl, reason))
            self.trades = self.trades[-self.trade_history_limit:]
            m.open_trade = None
            db.clear_position_bracket(m.symbol)
            self._feed("exit", f"[{m.symbol}] {entry['side'].upper()} @ {_round_price(exit_price)}  "
                               f"PnL {pnl:+.2f}  ({reason})")
            self.tlog.write("EXIT", side=entry["side"], price=_price_str(exit_price),
                            pnl=f"{pnl:.2f}", equity=f"{self.risk.equity:.2f}",
                            reason=reason, symbol=m.symbol)
            return True
        except Exception as e:
            log.warning("could not reconcile %s closed trade from fills: %s", m.symbol, e)
            return False

    def _analytics(self) -> dict:
        ts = self.trades
        n = len(ts)
        base = {"trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "total_pnl": 0.0, "avg_win": None, "avg_loss": None,
                "profit_factor": None, "avg_r": None, "best": None,
                "worst": None, "by_reason": {}, "by_symbol": {}, "by_strategy": {}}
        if not n:
            return base
        wins = [t for t in ts if t["win"]]
        losses = [t for t in ts if not t["win"]]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = -sum(t["pnl"] for t in losses)     # positive magnitude
        rs = [t["r"] for t in ts if t["r"] is not None]
        by_reason: dict[str, int] = {}
        by_symbol: dict[str, float] = {}
        for t in ts:
            by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
            by_symbol[t["symbol"]] = round(by_symbol.get(t["symbol"], 0) + t["pnl"], 2)
        by_strategy = self._analytics_by_strategy(ts)
        return {
            "trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / n * 100, 1),
            "total_pnl": round(sum(t["pnl"] for t in ts), 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "best": round(max(t["pnl"] for t in ts), 2),
            "worst": round(min(t["pnl"] for t in ts), 2),
            "by_reason": by_reason,
            "by_symbol": by_symbol,
            "by_strategy": by_strategy,
        }

    @staticmethod
    def _analytics_by_strategy(ts: list[dict]) -> dict:
        """Per-strategy performance breakdown, keyed by strategy name, so the
        dashboard can show which strategy is actually working."""
        groups: dict[str, list[dict]] = {}
        for t in ts:
            groups.setdefault(t.get("strategy") or "—", []).append(t)
        out = {}
        for name, g in groups.items():
            wins = [t for t in g if t["win"]]
            losses = [t for t in g if not t["win"]]
            gross_win = sum(t["pnl"] for t in wins)
            gross_loss = -sum(t["pnl"] for t in losses)
            rs = [t["r"] for t in g if t["r"] is not None]
            out[name] = {
                "trades": len(g),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(g) * 100, 1),
                "total_pnl": round(sum(t["pnl"] for t in g), 2),
                "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
                "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            }
        return out

    def _shadow_snapshot(self) -> dict:
        trades = self.shadow_trades
        wins = [t for t in trades if t.get("win")]
        by_strategy = self._analytics_by_strategy(trades)
        if self.strategy_lanes:
            for lane in self.strategy_lanes.values():
                stats = by_strategy.setdefault(lane.tag, {
                    "trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                    "total_pnl": 0.0, "profit_factor": None, "avg_r": None,
                })
                stats["open_positions"] = stats.get("open_positions", 0) + bool(lane.broker.position)
                stats.setdefault("symbols", {})[lane.symbol] = {
                    "resolution": lane.resolution,
                    "position": lane.broker.position.side if lane.broker.position else None,
                    "action": lane.last_action,
                    "reason": lane.last_reason,
                    "evaluated_at": lane.last_evaluated,
                    "error": lane.error,
                }
        return {
            "enabled": self.s.shadow_paper,
            "independent": bool(self.strategy_lanes),
            "market_data": "Delta candles" if self.strategy_lanes else None,
            "execution": "paper" if self.strategy_lanes else None,
            "lanes": len(self.strategy_lanes),
            # Treat the lanes as parallel experiments sharing one starting
            # balance: starting equity plus their aggregate realized P&L.
            "equity": (round(self.s.risk.account_equity + sum(t["pnl"] for t in trades), 2)
                       if self.strategy_lanes else
                       round(self.shadow_risk.equity, 2) if self.s.shadow_paper else None),
            "open_positions": (sum(1 for lane in self.strategy_lanes.values() if lane.broker.position)
                               if self.strategy_lanes else
                               sum(1 for s in self.shadow_markets.values() if s.broker.position)),
            "trades": len(trades),
            "pnl": round(sum(t["pnl"] for t in trades), 2),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "by_strategy": by_strategy,
        }

    def _narrate(self, context: str):
        """Fire-and-forget: ask Gemini for a one-line explanation of a trade and
        drop it into the live feed. Runs in a thread so the Gemini call never
        blocks the evaluation loop or the WebSocket. No-op if AI is off/unset."""
        if not self.s.ai_narrate or not ai.available():
            return

        def work():
            txt = ai.ask(
                context,
                system=("You are a terse trading assistant. In ONE short sentence "
                        "(max 20 words), explain this futures trading action. No preamble."),
                max_tokens=100, temperature=0.5, thinking_budget=0,
            )
            if txt:
                with self.lock:
                    self._feed("ai", txt.replace("\n", " ").strip()[:200])

        threading.Thread(target=work, daemon=True).start()

    def ai_analysis(self, symbol: str | None = None) -> dict:
        """On-demand AI report combining current conditions and trade results.
        Returns a dict the dashboard renders. Honest states: not-configured vs.
        unavailable vs. ok."""
        if not ai.available():
            return {"available": False, "text": "AI provider not configured (set OPENROUTER_API_KEY)."}
        with self.lock:
            m = self._market(symbol)
            candles = m.last_candles
            if not candles:
                return {"available": True, "text": "No market data yet — start the bot or run a cycle."}
            c = closes(candles)
            ef, es, r = ema(c, 9), ema(c, 21), rsi(c, 14)
            pos = m.broker.position
            recent = self.trades[-30:]
            net = sum(float(t.get("pnl") or 0) for t in recent)
            wins = sum(1 for t in recent if float(t.get("pnl") or 0) > 0)
            by_strategy = {}
            for t in recent:
                name = t.get("strategy") or "unknown"
                bucket = by_strategy.setdefault(name, {"trades": 0, "pnl": 0.0})
                bucket["trades"] += 1
                bucket["pnl"] += float(t.get("pnl") or 0)
            trade_lines = " | ".join(
                f"{t.get('symbol')} {t.get('strategy')} {t.get('side')} pnl={t.get('pnl')} reason={t.get('reason')}"
                for t in recent[-12:]
            ) or "no completed trades"
            prompt = (
                f"{m.symbol} {self.s.resolution}. "
                f"Price {c[-1]:.1f}, EMA9 {ef[-1]:.1f}, EMA21 {es[-1]:.1f}, "
                f"RSI14 {r[-1]:.1f}. "
                f"Position: {'flat' if not pos else pos.side}. "
                f"Recent completed trades: {len(recent)}, wins={wins}, net_pnl={net:.2f}. "
                f"By strategy: {by_strategy}. Trade details: {trade_lines}. "
                "Report what is happening, which strategies placed trades, profit/loss, and the recorded reasons. "
                "Mention when there is insufficient evidence. Use concise headings and bullets."
            )
        txt = ai.ask(prompt, system="You are a concise crypto market analyst. No financial advice disclaimers.",
                     max_tokens=300, thinking_budget=0)
        if not txt:
            return {"available": True, "text": "AI unavailable (check OpenRouter key, model, or network)."}
        return {"available": True, "text": txt, "model": ai.model_name(),
                "trade_count": len(recent), "net_pnl": round(net, 2),
                "by_strategy": by_strategy, "ts": self._now()}

    # ── voice/chat command layer ("Jarvis") ──
    # A fixed whitelist of real actions, each backed by the exact same
    # internal methods the dashboard's own buttons call — the model never
    # executes anything itself, it only ever picks a name from this list.
    JARVIS_ACTIONS = (
        "close_position", "close_all_positions", "set_mode", "set_auto",
        "pause_entries", "set_strategy_risk_pct", "set_coin_strategy",
        "set_coin_params", "set_coin_enabled", "set_coin_risk", "set_risk_limit",
        "set_engine", "add_market", "remove_market", "start_bot", "stop_bot",
    )

    def jarvis_status_summary(self) -> str:
        with self.lock:
            lines = [f"mode={self.s.mode} auto={self.auto} "
                     f"entries_paused={self.entries_paused} equity={self.risk.equity:.2f}",
                     f"engine: poll_seconds={self.s.poll_seconds} lookback={self.s.lookback} "
                     f"base_timeframe={self.s.resolution} ai_narrate={self.s.ai_narrate}"]
            slots = {sym: self.slot_for_symbol(sym) for sym in self.markets}
            for sym, m in self.markets.items():
                pos = m.broker.position
                slot = slots.get(sym)
                if slot:
                    changed = {k: v for k, v in slot["params"].items()}
                    desc = (f"slot={slot['tag']} strategy={slot['name']} "
                            f"{'ENABLED' if slot['enabled'] else 'DISABLED'} risk={slot['risk_pct']}% "
                            f"params={changed or 'defaults'}")
                else:
                    desc = f"strategy={self.s.strategy_name}"
                state_txt = f"{pos.side} {pos.size} @ {pos.entry} SL {pos.stop_loss} TP {pos.take_profit}" if pos else "flat"
                lines.append(f"{sym}: {desc}; position: {state_txt}")
            r = self.s.risk
            lines.append("risk limits: " + ", ".join(f"{k}={getattr(r, k)}" for k in self.RISK_LIMITS))
            a = self._analytics()
            if a["trades"]:
                lines.append(
                    f"full trade history: {a['trades']} closed trades, "
                    f"{a['wins']} wins / {a['losses']} losses (win_rate={a['win_rate']}%), "
                    f"total_pnl={a['total_pnl']}, avg_win={a['avg_win']}, avg_loss={a['avg_loss']}, "
                    f"profit_factor={a['profit_factor']}, best={a['best']}, worst={a['worst']}"
                )
                for name, s in a["by_strategy"].items():
                    lines.append(
                        f"strategy {name}: {s['trades']} trades, {s['wins']}W/{s['losses']}L "
                        f"(win_rate={s['win_rate']}%), total_pnl={s['total_pnl']}"
                    )
                for sym, pnl in a["by_symbol"].items():
                    lines.append(f"symbol {sym} lifetime total_pnl={pnl}")
            else:
                lines.append("full trade history: no closed trades yet")
            recent = self.trades[-10:]
            net = sum(float(t.get("pnl") or 0) for t in recent if t.get("pnl") not in (None, ""))
            recent_wins = sum(1 for t in recent if t.get("win"))
            recent_losses = len(recent) - recent_wins
            lines.append(
                f"last {len(recent)} closed trades: {recent_wins} wins / {recent_losses} losses "
                f"(win_rate={round(recent_wins / len(recent) * 100, 1) if recent else 0}%), net_pnl={net:.2f}"
            )
            return "\n".join(lines)

    def jarvis_command(self, text: str) -> dict:
        """Turn a natural-language instruction/question into either a direct
        spoken answer, or a proposed action from JARVIS_ACTIONS for the
        caller to confirm before executing (see jarvis_execute) — this
        method NEVER mutates state itself."""
        if not ai.available():
            return {"reply": "AI isn't configured (set OPENROUTER_API_KEY).", "action": None}
        symbols = list(self.markets)
        tags = sorted(self.s.risk.strategy_risk_pct)
        catalog = [n for n in available_strategies() if n != "combo"]
        param_help = {}
        for slot in self.slots():
            param_help.setdefault(slot["name"], sorted(slot["effective_params"]))
        system = (
            "You are Jarvis, the voice/chat assistant embedded in TradeOS, a live crypto "
            "trading bot dashboard. Answer questions about current status directly "
            "and concisely (2-3 sentences, spoken-style, no markdown). If the user "
            "asks you to CHANGE something, reply with a short confirmation-style "
            "sentence describing exactly what you are about to do, and set the "
            "action field — never claim to have done it yet, since it has not "
            "happened until the user confirms. One action per reply.\n\n"
            "The status block below includes every coin's strategy slot, its "
            "params, the risk limits, and the FULL closed-trade history (win/loss "
            "counts, win rate, total P&L, profit factor, per strategy and per "
            "symbol). Answer from those numbers — never say you lack access.\n\n"
            f"Valid symbols: {symbols}\n"
            f"Valid strategy slot tags: {tags}\n"
            f"Installed strategies: {catalog}\n"
            f"Param names by strategy in use: {param_help}\n"
            f"Risk limit fields: {sorted(self.RISK_LIMITS)}\n"
            "Valid action names and their args (omit args that don't apply):\n"
            '  close_position {"symbol": one of the valid symbols}\n'
            '  close_all_positions {}\n'
            '  set_mode {"mode": "PAPER" or "DELTA_DEMO"}\n'
            '  set_auto {"enabled": true or false}\n'
            '  pause_entries {"paused": true or false}\n'
            '  set_coin_strategy {"symbol": ..., "strategy": an installed strategy}\n'
            '  set_coin_params {"symbol": ..., "params": {"param_name": value}}\n'
            '  set_coin_enabled {"symbol": ..., "enabled": true or false}\n'
            '  set_coin_risk {"symbol": ..., "risk_pct": number 0.05-5}\n'
            '  set_strategy_risk_pct {"strategy": a slot tag, "risk_pct": number 0.05-5}\n'
            '  set_risk_limit {"field": a risk limit field, "value": number}\n'
            '  set_engine {"poll_seconds"?: int, "lookback"?: int, "resolution"?: timeframe, "ai_narrate"?: bool}\n'
            '  add_market {"symbol": e.g. "LTCUSD", "strategy"?: installed strategy, "risk_pct"?: number}\n'
            '  remove_market {"symbol": ...}\n'
            '  start_bot {}\n'
            '  stop_bot {}\n'
            "Words like 'take profit 8x' or 'TP multiple' on Donchian mean the "
            "fallback_tp_mult param. If the request doesn't match a real action, "
            "or is missing required info (e.g. which symbol), ask a short "
            "clarifying question instead and set action to null.\n"
            'Reply with JSON: {"reply": "...", "action": null or '
            '{"name": "...", "args": {...}}}'
        )
        prompt = f"Current status:\n{self.jarvis_status_summary()}\n\nUser said: {text}"
        result = ai.ask_json(prompt, system=system, max_tokens=700)
        if not result or "reply" not in result:
            return {"reply": "I didn't catch that clearly — can you repeat it?", "action": None}
        action = result.get("action")
        if isinstance(action, dict) and action.get("name") not in self.JARVIS_ACTIONS:
            action = None
        return {"reply": str(result["reply"])[:500], "action": action}

    def _dedicated_slot(self, symbol) -> dict:
        if symbol not in self.markets:
            raise ValueError(f"unknown symbol: {symbol}")
        slot = self.slot_for_symbol(symbol)
        if slot is None:
            raise ValueError(f"{symbol} has no strategy slot")
        if slot["symbols"] != [symbol]:
            raise ValueError(f"{symbol}'s slot {slot['tag']} is shared with "
                             f"{', '.join(s for s in slot['symbols'] if s != symbol)}; edit it on the Strategies page")
        return slot

    def jarvis_execute(self, name: str, args: dict) -> dict:
        """Actually perform a JARVIS_ACTIONS action. Called only after the
        user has explicitly confirmed what jarvis_command proposed."""
        args = args or {}
        if name == "close_position":
            symbol = args.get("symbol")
            if symbol not in self.markets:
                raise ValueError(f"unknown symbol: {symbol}")
            out = self.force_close(symbol)
            return {"event": out.event, "detail": out.detail}
        if name == "close_all_positions":
            return {"closed": self.close_all()}
        if name == "set_mode":
            self.set_mode(str(args.get("mode", "")).upper())
            return {"mode": self.s.mode}
        if name == "set_auto":
            self.set_auto(_as_bool(args.get("enabled")))
            return {"auto": self.auto}
        if name == "pause_entries":
            self.pause_entries(_as_bool(args.get("paused")))
            return {"entries_paused": self.entries_paused}
        if name == "set_strategy_risk_pct":
            strategy = args.get("strategy")
            if strategy not in self.s.risk.strategy_risk_pct:
                raise ValueError(f"unknown strategy: {strategy}")
            risk_pct = self._validate_risk_pct(args.get("risk_pct"))
            self.update_risk_settings({"strategy_risk_pct": {strategy: risk_pct}})
            return {"strategy": strategy, "risk_pct": risk_pct}
        if name == "set_coin_strategy":
            slot = self._dedicated_slot(args.get("symbol"))
            sub = self.update_sub(slot["tag"], name=str(args.get("strategy") or ""),
                                  params=args.get("params") or None)
            return {"symbol": args.get("symbol"), "slot": sub}
        if name == "set_coin_params":
            slot = self._dedicated_slot(args.get("symbol"))
            if not isinstance(args.get("params"), dict) or not args["params"]:
                raise ValueError("no params given")
            return {"symbol": args.get("symbol"), "slot": self.update_sub(slot["tag"], params=args["params"])}
        if name == "set_coin_enabled":
            slot = self._dedicated_slot(args.get("symbol"))
            return {"symbol": args.get("symbol"),
                    "slot": self.update_sub(slot["tag"], enabled=_as_bool(args.get("enabled")))}
        if name == "set_coin_risk":
            slot = self._dedicated_slot(args.get("symbol"))
            self.update_sub(slot["tag"], risk_pct=args.get("risk_pct"))
            return {"symbol": args.get("symbol"), "risk_pct": self.s.risk.risk_for(slot["tag"])}
        if name == "set_risk_limit":
            field = args.get("field")
            if field not in self.RISK_LIMITS:
                raise ValueError(f"unknown risk field: {field}")
            self.update_risk_settings({field: args.get("value")})
            return {field: getattr(self.s.risk, field)}
        if name == "set_engine":
            return self.update_engine({k: args.get(k) for k in ("poll_seconds", "lookback", "resolution", "ai_narrate")})
        if name == "add_market":
            return self.add_market(args.get("symbol"), strategy=args.get("strategy") or "paused",
                                   risk_pct=args.get("risk_pct"))
        if name == "remove_market":
            return self.remove_market(args.get("symbol"))
        if name in ("start_bot", "stop_bot"):
            # the runner lives in server.py, not here — the route handles it
            return {"ok": True}
        raise ValueError(f"unknown action: {name}")

    # ── data ──
    def fetch_candles(self, symbol: str, resolution: str | None = None) -> list[dict]:
        """resolution defaults to the agent's own shared timeframe; a
        multi-timeframe strategy (e.g. a combo sub on a different interval)
        can pass its own to fetch independently of the agent's main feed."""
        resolution = resolution or self.s.resolution
        sec = _RES_SECONDS.get(resolution, 300)
        end = int(self.clock())
        start = end - sec * (self.s.lookback + 5)
        return self.client.get_candles(symbol, resolution, start, end)

    # ── one evaluation cycle, across every symbol ──
    def evaluate(self) -> dict[str, Outcome]:
        with self.lock:
            started = time.monotonic()
            self._refresh_account()  # cached; one shared account, not per symbol
            outcomes = {}
            for sym, m in self.markets.items():
                try:
                    out = self._evaluate_market(m)
                except Exception as e:
                    m.last_error = str(e)[:300]
                    m.pending = None
                    out = Outcome("error", m.last_error)
                    self._feed("error", f"[{sym}] {m.last_error}")
                    log.exception("evaluation failed for %s", sym)
                m.last_outcome = outcomes[sym] = out
                if sym in self.shadow_markets:
                    try:
                        self._evaluate_shadow(sym, m.last_candles)
                    except Exception:
                        # A staging simulator must never stop live position
                        # management or mark the live market unhealthy.
                        log.exception("shadow evaluation failed for %s", sym)
            if self.strategy_lanes and self.execution_enabled:
                self._evaluate_strategy_lanes()
            self.last_cycle_at = self._now()
            self.last_cycle_ms = round((time.monotonic() - started) * 1000)
            return outcomes

    def _shadow_portfolio(self):
        positions = [s.broker.position for s in self.shadow_markets.values() if s.broker.position]
        return (sum(p.notional for p in positions),
                sum(abs(p.entry - p.stop_loss) * (p.notional / p.entry) for p in positions),
                len(positions))

    def _evaluate_shadow(self, symbol: str, candles: list[dict]):
        """Independent staging lane: same strategy/data, simulated fills only."""
        if not candles:
            return
        shadow = self.shadow_markets[symbol]
        last, price = candles[-1], float(candles[-1]["close"])
        bracket = shadow.broker.check_bracket(last)
        if bracket:
            _, exit_price, why = bracket
            entry = shadow.open_trade
            pnl = shadow.broker.close(exit_price)
            self.shadow_risk.register_pnl(pnl)
            if entry:
                self.shadow_trades.append(self._mk_trade(symbol, entry, self._now(), exit_price, pnl, why))
                self.shadow_trades = self.shadow_trades[-500:]
            shadow.open_trade = None
        side = shadow.broker.position.side if shadow.broker.position else None
        signal_candles = candles
        if getattr(shadow.strategy, "closed_candles_only", False):
            seconds = _RES_SECONDS[self.s.resolution]
            signal_candles = [c for c in candles if float(c["time"]) + seconds <= self.clock()]
            if not signal_candles:
                return
        sig = shadow.strategy.generate(signal_candles, side)
        if sig.action == Action.EXIT and side:
            entry = shadow.open_trade
            pnl = shadow.broker.close(price)
            self.shadow_risk.register_pnl(pnl)
            if entry:
                self.shadow_trades.append(self._mk_trade(symbol, entry, self._now(), price, pnl, sig.reason))
            shadow.open_trade = None
            return
        if sig.action not in (Action.ENTER_LONG, Action.ENTER_SHORT) or side:
            return
        stamp = last.get("time")
        if stamp == shadow.last_entry_candle:
            return
        trade_side = "long" if sig.action == Action.ENTER_LONG else "short"
        notional, stop_risk, count = self._shadow_portfolio()
        same = sum(1 for s in self.shadow_markets.values()
                   if s.broker.position and s.broker.position.side == trade_side)
        strategy = sig.strategy or self.s.strategy_name
        plan = self.shadow_risk.size_position(
            trade_side, price, shadow.broker.contract_value(price), sig.stop_loss, sig.take_profit,
            notional, stop_risk, count, same, self.s.risk.risk_for(strategy),
        )
        if not plan.ok:
            return
        shadow.broker.open(plan)
        shadow.last_entry_candle = stamp
        shadow.open_trade = {"entry_ts": self._now(), "side": trade_side, "entry": price,
                             "size": plan.size_contracts, "sl": plan.stop_loss,
                             "tp": plan.take_profit, "strategy": strategy,
                             "contract_value": shadow.broker.contract_value(price)}

    def _lane_candles(self, lane: StrategyLane) -> list[dict]:
        """Use the live cycle's base feed or a short-lived per-timeframe cache."""
        if lane.resolution == self.s.resolution:
            return self.markets[lane.symbol].last_candles
        key = (lane.symbol, lane.resolution)
        now = self.clock()
        cached = self._lane_candle_cache.get(key)
        if cached and now - cached[0] < max(5, min(30, self.s.poll_seconds * 2)):
            return cached[1]
        candles = self.fetch_candles(lane.symbol, lane.resolution)
        self._lane_candle_cache[key] = (now, candles)
        return candles

    def _evaluate_strategy_lanes(self):
        """Run every combo sub independently with simulated execution.

        Each lane can hold a position at the same time as every other lane,
        including another strategy on the same symbol.  Market data is live;
        fills and P&L are explicitly paper-only.
        """
        for key, lane in self.strategy_lanes.items():
            try:
                candles = self._lane_candles(lane)
                if not candles:
                    lane.last_action, lane.last_reason = "WARMING_UP", "waiting for market data"
                    continue
                for candle in candles:
                    values = [float(candle[k]) for k in ("open", "high", "low", "close")]
                    if not all(math.isfinite(v) and v > 0 for v in values):
                        raise ValueError("invalid OHLC market data")
                price = float(candles[-1]["close"])
                latest_stamp = candles[-1].get("time")
                stamp = self._f(latest_stamp)
                max_age = _RES_SECONDS.get(lane.resolution, 300) + max(60, self.s.poll_seconds * 2)
                if stamp is None or not math.isfinite(stamp) or self.clock() - stamp > max_age:
                    lane.last_action, lane.last_reason = "BLOCKED", "stale market data"
                    lane.last_evaluated = self._now()
                    continue

                if lane.broker.position:
                    bracket_candle = candles[-1]
                    if lane.opened_market_candle == latest_stamp:
                        bracket_candle = dict(bracket_candle, open=price, high=price, low=price)
                    bracket = lane.broker.check_bracket(bracket_candle)
                    if bracket:
                        _, exit_price, reason = bracket
                        entry = lane.open_trade
                        pnl = lane.broker.close(exit_price)
                        lane.risk.register_pnl(pnl)
                        if entry:
                            self.shadow_trades.append(
                                self._mk_trade(lane.symbol, entry, self._now(), exit_price, pnl, reason)
                            )
                            self.shadow_trades = self.shadow_trades[-500:]
                        lane.open_trade = None
                        lane.opened_market_candle = None

                signal_candles = candles
                if getattr(lane.strategy, "closed_candles_only", False):
                    seconds = _RES_SECONDS.get(lane.resolution, 300)
                    signal_candles = [c for c in candles
                                      if float(c["time"]) + seconds <= self.clock()]
                    if any(float(b["time"]) - float(a["time"]) != seconds
                           for a, b in zip(signal_candles, signal_candles[1:])):
                        lane.last_action, lane.last_reason = "BLOCKED", "gap in closed candles"
                        continue
                required = getattr(lane.strategy, "required_candles", 1)
                if len(signal_candles) < required:
                    lane.last_action = "WARMING_UP"
                    lane.last_reason = f"need {required} closed candles; have {len(signal_candles)}"
                    continue

                side = lane.broker.position.side if lane.broker.position else None
                sig = lane.strategy.generate(signal_candles, side)
                lane.last_action = sig.action.value
                lane.last_reason = sig.reason
                lane.last_evaluated = self._now()
                lane.error = None

                if sig.action == Action.EXIT and side:
                    entry = lane.open_trade
                    pnl = lane.broker.close(price)
                    lane.risk.register_pnl(pnl)
                    if entry:
                        self.shadow_trades.append(
                            self._mk_trade(lane.symbol, entry, self._now(), price, pnl, sig.reason)
                        )
                        self.shadow_trades = self.shadow_trades[-500:]
                    lane.open_trade = None
                    lane.opened_market_candle = None
                    continue
                if sig.action not in (Action.ENTER_LONG, Action.ENTER_SHORT) or side:
                    continue
                if self.entries_paused:
                    lane.last_action, lane.last_reason = "BLOCKED", "new entries are paused"
                    continue
                signal_stamp = signal_candles[-1].get("time")
                if signal_stamp == lane.last_entry_candle:
                    lane.last_action, lane.last_reason = "HOLD", "already entered this signal candle"
                    continue
                if (sig.reference_price is not None and sig.max_entry_deviation is not None
                        and abs(price - sig.reference_price) > sig.max_entry_deviation):
                    lane.last_action, lane.last_reason = "BLOCKED", "price moved beyond entry limit"
                    continue
                trade_side = "long" if sig.action == Action.ENTER_LONG else "short"
                plan = lane.risk.size_position(
                    trade_side, price, lane.broker.contract_value(price),
                    sig.stop_loss, sig.take_profit,
                    open_notional=0, open_risk=0, open_positions=0,
                    same_direction_positions=0,
                    risk_pct=self.s.risk.risk_for(lane.tag),
                )
                if not plan.ok:
                    lane.last_action, lane.last_reason = "BLOCKED", plan.reject_reason
                    continue
                lane.broker.open(plan)
                lane.last_entry_candle = signal_stamp
                lane.opened_market_candle = latest_stamp
                lane.open_trade = {
                    "entry_ts": self._now(), "side": trade_side, "entry": price,
                    "size": plan.size_contracts, "sl": plan.stop_loss,
                    "tp": plan.take_profit, "strategy": lane.tag,
                    "contract_value": lane.broker.contract_value(price),
                }
            except Exception as e:
                lane.error = str(e)[:300]
                lane.last_action, lane.last_reason = "ERROR", lane.error
                lane.last_evaluated = self._now()
                log.warning("paper strategy lane %s failed: %s", key, e)

    def _data_issue(self, m: Market) -> str:
        if m.last_error:
            return m.last_error
        if not m.last_candles:
            return "waiting for market data"
        stamp = self._f(m.last_candles[-1].get("time"))
        if stamp is None or not math.isfinite(stamp):
            return "missing candle timestamp"
        age = self.clock() - stamp
        if age < -5:
            return "candle timestamp is in the future"
        if age > _RES_SECONDS.get(self.s.resolution, 300) + max(60, self.s.poll_seconds * 2):
            return "stale market data; entries blocked"
        if m.last_fetch and self.clock() - m.last_fetch > max(60, self.s.poll_seconds * 2):
            return "market polling stale; entries blocked"
        return ""

    def _evaluate_market(self, m: Market) -> Outcome:
        try:
            candles = self.fetch_candles(m.symbol)
        except DeltaError as e:
            m.last_error = str(e)[:300]
            m.pending = None
            return Outcome("hold", f"candle fetch failed: {e}")
        if len(candles) < 5:
            m.last_error = "too few candles"
            m.pending = None
            return Outcome("hold", "too few candles")
        for candle in candles:
            o, h, l, c = (float(candle[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError("invalid OHLC market data")
        m.last_candles = candles
        m.last_fetch = self.clock()
        m.last_error = None
        last = candles[-1]
        price = float(last["close"])

        # 1. manage open position (SL/TP) — always automatic, even in manual mode
        if m.broker.position is not None:
            # The entry candle's high/low may predate the entry. On repeated
            # polls of that candle, only the current close is usable.
            bracket_candle = last
            if m.last_entry_candle is not None and m.last_entry_candle == last.get("time"):
                bracket_candle = dict(last, open=price, high=price, low=price)
            bracket = m.broker.check_bracket(bracket_candle)
            if bracket:
                _, exit_price, why = bracket
                return self._do_exit(m, exit_price, why)

        pos_side = m.broker.position.side if m.broker.position else None
        signal_candles = candles
        if getattr(m.strategy, "closed_candles_only", False):
            if self.s.resolution not in m.strategy.supported_resolutions:
                m.pending = None
                return Outcome("hold", "strategy requires 1h, 4h or 1d candles")
            seconds = _RES_SECONDS[self.s.resolution]
            signal_candles = [c for c in candles if float(c["time"]) + seconds <= self.clock()]
            if not signal_candles:
                m.pending = None
                return Outcome("hold", "waiting for a closed candle")
            if any(float(b["time"]) - float(a["time"]) != seconds
                   for a, b in zip(signal_candles, signal_candles[1:])):
                m.pending = None
                return Outcome("hold", "gap in closed candles; strategy signals blocked")
        sig = m.strategy.generate(signal_candles, pos_side)

        if sig.action == Action.HOLD:
            m.pending = None
            return Outcome("hold", sig.reason)

        # strategy exit — but once a real exchange bracket is protecting the
        # trade, let IT decide the outcome (SL or TP actually hit), not an
        # early strategy cross. Otherwise the bot cuts winners/losers short
        # before the stop/target it already set on Delta is reached.
        if sig.action == Action.EXIT and m.broker.position is not None:
            if getattr(m.broker.position, "bracket_confirmed", False) and not sig.exit_with_bracket:
                return Outcome("hold", f"ignoring strategy exit — exchange bracket owns the exit ({sig.reason})")
            if self.auto:
                return self._do_exit(m, price, sig.reason)
            m.pending, m.pending_price = sig, price
            m.pending_at = self.clock()
            self._feed("pending", f"[{m.symbol}] EXIT — {sig.reason}")
            return Outcome("pending", sig.reason, sig.action.value)

        # entries
        if sig.action in (Action.ENTER_LONG, Action.ENTER_SHORT) and pos_side is None:
            issue = self._data_issue(m)
            if issue or self.entries_paused:
                m.pending = None
                return Outcome("reject", issue or "new entries are paused")
            if self.auto:
                return self._open_from_signal(m, sig, price)
            m.pending, m.pending_price = sig, price
            m.pending_at = self.clock()
            self._feed("pending", f"[{m.symbol}] {sig.action.value} — {sig.reason}")
            return Outcome("pending", sig.reason, sig.action.value)

        return Outcome("hold", sig.reason)

    def execute_pending(self, symbol: str | None = None) -> Outcome:
        """Manual-mode confirm: act on symbol's stored pending signal at current price."""
        with self.lock:
            m = self._market(symbol)
            if not m.pending:
                return Outcome("none", "nothing pending")
            sig = m.pending
            price = float(m.last_candles[-1]["close"]) if m.last_candles else m.pending_price
            m.pending = None
            if self.clock() - m.pending_at > max(60, self.s.poll_seconds * 2):
                return Outcome("reject", "signal expired; run a new evaluation")
            if sig.action == Action.EXIT and m.broker.position is not None:
                return self._do_exit(m, price, "manual confirm: " + sig.reason)
            if sig.action in (Action.ENTER_LONG, Action.ENTER_SHORT):
                issue = self._data_issue(m)
                if issue:
                    return Outcome("reject", issue)
                self._refresh_account(force=True)
                return self._open_from_signal(m, sig, price)
            return Outcome("none", "pending no longer valid")

    def dismiss_pending(self, symbol: str | None = None) -> Outcome:
        with self.lock:
            self._market(symbol).pending = None
            return Outcome("none", "pending dismissed")

    def force_close(self, symbol: str | None = None) -> Outcome:
        with self.lock:
            if not self.execution_enabled:
                return Outcome("reject", self.execution_reason or "read-only standby instance")
            m = self._market(symbol)
            if m.broker.position is None:
                return Outcome("none", "no open position")
            price = float(m.last_candles[-1]["close"]) if m.last_candles else m.broker.position.entry
            return self._do_exit(m, price, "manual close")

    # ── internal execution ──
    def _open_from_signal(self, m: Market, sig: Signal, price: float, *,
                          risk_pct: float | None = None, manual: bool = False) -> Outcome:
        if not self.execution_enabled:
            return Outcome("reject", self.execution_reason or "read-only standby instance")
        if self.entries_paused:
            return Outcome("reject", "new entries are paused")
        if sig.reference_price is not None and sig.max_entry_deviation is not None:
            if abs(price - sig.reference_price) > sig.max_entry_deviation:
                return Outcome("reject", "price moved more than 0.5 ATR from signal; do not chase entry")
        if any(getattr(market.broker, "execution_issue", None) for market in self.markets.values()):
            return Outcome("reject", "unresolved exchange execution; entries blocked")
        if m.broker.position is not None:
            return Outcome("reject", "position already open")
        stamp = m.last_candles[-1].get("time") if m.last_candles else None
        rejection_key = (stamp, sig.action.value)
        if not manual and m.last_rejected_entry and m.last_rejected_entry[:2] == rejection_key:
            return Outcome("hold", f"entry rejected earlier this candle: {m.last_rejected_entry[2]}")
        if stamp is not None and stamp == m.last_entry_candle:
            return Outcome("reject", "already entered on this candle")
        if self.s.mode == "DELTA_DEMO":
            if self._acct["status"] != "live" or self.clock() - self._acct["ts"] > self._BAL_TTL:
                return Outcome("reject", "fresh account data required")
            known = {sym for sym, market in self.markets.items() if market.broker.position}
            if any(p["symbol"] not in known for p in self._acct["positions"]):
                return Outcome("reject", "unreconciled exchange positions; entries blocked")
            self.risk.equity = min(self.risk.equity, self._acct["usd"])
        portfolio = self._portfolio()
        side = "long" if sig.action == Action.ENTER_LONG else "short"
        strat = sig.strategy or self.s.strategy_name
        allowed, guard_reason = (True, "") if manual else self._strategy_guard(strat)
        if not allowed:
            m.last_rejected_entry = rejection_key + (guard_reason,)
            self._feed("risk", f"[{m.symbol}] {guard_reason}")
            return Outcome("reject", guard_reason)
        cv = m.broker.contract_value(price)
        same_direction = sum(
            1 for market in self.markets.values()
            if market.broker.position and market.broker.position.side == side
        )
        plan = self.risk.size_position(side, price, contract_value=cv,
                                       stop_loss=sig.stop_loss, take_profit=sig.take_profit,
                                       open_notional=portfolio["notional"], open_risk=portfolio["stop_risk"],
                                       open_positions=portfolio["positions"],
                                       same_direction_positions=same_direction,
                                       risk_pct=risk_pct if risk_pct is not None else self.s.risk.risk_for(strat))
        if not plan.ok:
            if not manual:
                m.last_rejected_entry = rejection_key + (plan.reject_reason,)
            self._feed("reject", f"[{m.symbol}] {plan.reject_reason}")
            self.tlog.write("REJECT", side=side, price=_price_str(price),
                            reason=plan.reject_reason, equity=f"{self.risk.equity:.2f}",
                            symbol=m.symbol)
            return Outcome("reject", plan.reject_reason)
        m.broker.open(plan)
        m.last_rejected_entry = None
        m.last_entry_candle = stamp
        m.open_trade = {
            "entry_ts": self._now(), "side": side, "entry": price,
            "size": plan.size_contracts, "sl": plan.stop_loss, "tp": plan.take_profit,
            "strategy": strat, "contract_value": cv, "reason": sig.reason or "",
        }
        # Persist what WE intended as the bracket, independent of whether
        # Delta actually confirmed it — reconcile() falls back to this if
        # Delta's resting bracket is missing/incomplete (e.g. a leg gets
        # silently cancelled for violating margin/liquidation bounds), so
        # the position keeps local protection even across a restart.
        db.save_position_bracket(m.symbol, {
            "side": side, "entry": price,
            "stop_loss": plan.stop_loss, "take_profit": plan.take_profit,
            "strategy": strat,
        })
        if isinstance(m.broker, DeltaBroker) and not m.broker.position.bracket_confirmed:
            self._feed("bracket",
                       f"[{m.symbol}] Delta did not confirm the exchange bracket for this "
                       f"entry (a leg may have been rejected, e.g. for violating margin/"
                       f"liquidation bounds) — falling back to local stop/target protection, "
                       f"which only works while this process keeps running.")
        self._feed("entry", f"[{m.symbol}/{strat}] {side.upper()} {plan.size_contracts}c @ {_round_price(price)} "
                            f"SL {_round_price(plan.stop_loss)} TP {_round_price(plan.take_profit)}")
        self._narrate(f"Opened {m.symbol} {side.upper()} {plan.size_contracts} contracts @ {_round_price(price)}, "
                      f"SL {_round_price(plan.stop_loss)}, TP {_round_price(plan.take_profit)}. Signal: {sig.reason}")
        self.tlog.write("ENTRY", side=side, price=_price_str(price),
                        size=plan.size_contracts, sl=_price_str(plan.stop_loss),
                        tp=_price_str(plan.take_profit), equity=f"{self.risk.equity:.2f}",
                        reason=sig.reason, strategy=strat, symbol=m.symbol)
        return Outcome("entry", sig.reason)

    def _do_exit(self, m: Market, exit_price: float, why: str) -> Outcome:
        if not self.execution_enabled:
            return Outcome("hold", self.execution_reason or "read-only standby instance")
        side = m.broker.position.side
        pnl = m.broker.close(exit_price)
        self.risk.register_pnl(pnl)
        self.equity_curve.append({"t": self._now(), "equity": self.risk.equity})
        self.equity_curve = self.equity_curve[-500:]
        if m.open_trade:
            self.trades.append(self._mk_trade(
                m.symbol, m.open_trade, self._now(), exit_price, pnl, why))
            self.trades = self.trades[-self.trade_history_limit:]
            m.open_trade = None
        db.clear_position_bracket(m.symbol)
        self._feed("exit", f"[{m.symbol}] {side.upper()} @ {_round_price(exit_price)}  PnL {pnl:+.2f}  ({why})")
        self.tlog.write("EXIT", side=side, price=_price_str(exit_price),
                        pnl=f"{pnl:.2f}", equity=f"{self.risk.equity:.2f}", reason=why,
                        symbol=m.symbol)
        self._narrate(f"Closed {m.symbol} {side.upper()} @ {_round_price(exit_price)} for PnL {pnl:+.2f}. Reason: {why}")
        return Outcome("exit", f"{why} | PnL {pnl:+.2f}")

    def _portfolio(self) -> dict:
        notional = stop_risk = unrealized = 0.0
        positions = unprotected = 0
        for m in self.markets.values():
            p = m.broker.position
            if not p:
                continue
            positions += 1
            price = float(m.last_candles[-1]["close"]) if m.last_candles else p.entry
            units = p.notional / p.entry
            notional += abs(units * price)
            unrealized += units * (price - p.entry) * (1 if p.side == "long" else -1)
            if p.stop_loss is None or p.take_profit is None:
                unprotected += 1
            else:
                stop_risk += abs(p.entry - p.stop_loss) * units
                if self.s.mode == "DELTA_DEMO" and not p.bracket_confirmed:
                    unprotected += 1
        equity = self.risk.equity
        return {"positions": positions, "unprotected": unprotected,
                "notional": notional, "stop_risk": stop_risk, "unrealized": unrealized,
                "leverage": notional / equity if equity > 0 else None,
                "risk_pct": stop_risk / equity * 100 if equity > 0 else None,
                "daily_loss_remaining": max(0, self.risk._day_start_equity * self.s.risk.max_daily_loss_pct / 100 + self.risk.realized_today)}

    def _protection_status(self, pos) -> str:
        """Truthful SL/TP protection state.
        "exchange"  — a real bracket is resting on Delta itself (confirmed by
                      Delta's own order response); survives even if this
                      process goes down.
        "local"     — enforced only by the bot loop watching candles each
                      cycle (paper positions, or a DELTA_DEMO entry where
                      Delta didn't confirm the bracket) — active ONLY while
                      the bot keeps running.
        "none"      — no stop/target set at all.
        """
        if pos.stop_loss is None or pos.take_profit is None:
            return "none"
        if getattr(pos, "bracket_confirmed", False):
            return "exchange"
        return "local"

    def _market_snapshot(self, m: Market, lite: bool = False) -> dict:
        candles = m.last_candles
        c = closes(candles) if candles else []
        # lite = the new dashboard's stream: it fetches full charts on demand
        # (/api/chart), so skip the per-second indicator math entirely.
        ef = ema(c, 9) if c and not lite else []
        es = ema(c, 20) if c and not lite else []
        et = ema(c, 200) if c and not lite else []
        r = rsi(c, 14) if c and not lite else []
        n = 0 if lite else 120
        chart = []
        start = max(0, len(candles) - n)
        for i in range(start if n else len(candles), len(candles)):
            chart.append({
                "t": candles[i].get("time"),
                "o": float(candles[i]["open"]), "h": float(candles[i]["high"]),
                "l": float(candles[i]["low"]), "c": float(candles[i]["close"]),
                "ema_fast": ef[i] if i < len(ef) else None,
                "ema_slow": es[i] if i < len(es) else None,
                "ema_trend": et[i] if i < len(et) else None,
                "volume": float(candles[i].get("volume") or 0),
                "rsi": r[i] if i < len(r) else None,
            })
        pos = m.broker.position
        price = float(candles[-1]["close"]) if candles else None
        unrealized = None
        if pos and price:
            move = (price - pos.entry) if pos.side == "long" else (pos.entry - price)
            unrealized = move * (pos.notional / pos.entry / pos.size) * pos.size
        is_demo = self.s.mode == "DELTA_DEMO"
        # cross-reference the account-wide exchange_positions list (real Delta
        # data) for this symbol's liquidation price, if live.
        liq = None
        if is_demo and pos:
            match = next((p for p in self._acct["positions"] if p.get("symbol") == m.symbol), None)
            liq = match.get("liquidation") if match else None
        step = _RES_SECONDS.get(self.s.resolution)
        day_bars = int(86400 // step) if step else 0
        change_24h = None
        if day_bars and len(c) > day_bars and c[-1 - day_bars]:
            change_24h = round((c[-1] / c[-1 - day_bars] - 1) * 100, 3)
        last_bar = candles[-1] if candles else None
        return {
            "symbol": m.symbol,
            "spark": [round(x, 8) for x in c[-72:]],
            "change_24h": change_24h,
            "candle": None if not last_bar else {
                "t": last_bar.get("time"), "o": float(last_bar["open"]), "h": float(last_bar["high"]),
                "l": float(last_bar["low"]), "c": float(last_bar["close"]),
                "v": float(last_bar.get("volume") or 0)},
            "data_status": self._data_issue(m) or "fresh",
            "last_fetch": m.last_fetch or None,
            "decision": m.last_outcome.__dict__,
            "execution_issue": getattr(m.broker, "execution_issue", None),
            "price": price,
            "contract_value": m.broker.contract_value(price) if price else None,
            "chart": chart,
            "position": None if not pos else {
                "side": pos.side, "entry": pos.entry, "size": pos.size,
                "sl": pos.stop_loss, "tp": pos.take_profit,
                "liquidation": liq,
                "notional": round(pos.notional, 2),
                "unrealized": round(unrealized, 2) if unrealized is not None else None,
                "protection": self._protection_status(pos),
                "reconciliation": "polled from exchange" if is_demo else "n/a (simulated)",
                "entry_ts": m.open_trade.get("entry_ts") if m.open_trade else None,
            },
            "pending": None if not m.pending else {
                "action": m.pending.action.value, "reason": m.pending.reason,
                "price": m.pending_price,
            },
        }

    # ── state snapshot for UI ──
    def snapshot(self, lite: bool = False) -> dict:
        with self.lock:
            can, why = self.risk.can_trade()
            is_demo = self.s.mode == "DELTA_DEMO"
            if is_demo:
                # Authoritative equity = the live Delta USD wallet balance. If the
                # account call is failing, show null (→ "—" in the UI), never 0.
                usd = self._acct["usd"]
                equity = round(usd, 2) if usd is not None else None
                equity_status = self._acct["status"]      # live | unavailable
            else:
                equity = round(self.risk.equity, 2)
                equity_status = "simulated"
            markets = {sym: self._market_snapshot(m, lite) for sym, m in self.markets.items()}
            portfolio = self._portfolio()
            portfolio["known"] = not is_demo or self._acct["status"] == "live"
            entry_blocks = []
            if not portfolio["known"]:
                entry_blocks.append("portfolio exposure unavailable")
            else:
                if portfolio["positions"] >= self.s.risk.max_open_positions:
                    entry_blocks.append("position slots full")
                if portfolio["risk_pct"] >= self.s.risk.max_portfolio_risk_pct:
                    entry_blocks.append("portfolio stop-risk budget exhausted")
                if portfolio["leverage"] >= self.s.risk.max_leverage:
                    entry_blocks.append("leverage budget exhausted")
            if not can:
                entry_blocks.append(why)
            first = next(iter(markets.values())) if markets else {}
            active_strategies = []
            for market in self.markets.values():
                for name, _ in getattr(market.strategy, "subs", []):
                    if name not in active_strategies:
                        active_strategies.append(name)
            if not active_strategies:
                active_strategies = [self.s.strategy_name]
            return {
                "ts": self._now(),
                "mode": self.s.mode,
                "auto": self.auto,
                "entries_paused": self.entries_paused,
                "execution_enabled": self.execution_enabled,
                "execution_reason": self.execution_reason,
                "alerts_configured": alerts.configured(),
                "portfolio": portfolio,
                "health": {"last_cycle": self.last_cycle_at, "cycle_ms": self.last_cycle_ms},
                "symbols": list(self.markets.keys()),
                "resolution": self.s.resolution,
                "strategy": self.s.strategy_name,
                "active_strategies": active_strategies,
                "strategy_params": self.s.strategy_params,
                "strategies": available_strategies(),
                "venue": "Delta testnet (demo)" if is_demo else "Paper (simulated fills)",
                "price_source": "Delta candles",
                "ai_available": ai.available(),
                "markets": markets,
                # back-compat top-level fields mirror the first symbol, so an
                # older frontend build still renders something sane.
                "symbol": first.get("symbol"),
                "price": first.get("price"),
                "chart": first.get("chart", []),
                "position": first.get("position"),
                "pending": first.get("pending"),
                "equity": equity,
                "equity_status": equity_status,
                "equity_asset": "USD",
                "account_status": self._acct["status"] if is_demo else "n/a",
                "account_error": self._acct.get("error") if is_demo else None,
                "account_error_code": self._acct.get("code") if is_demo else None,
                "account_client_ip": self._acct.get("client_ip") if is_demo else None,
                "account_retry_at": self._acct.get("retry_at") if is_demo else None,
                "account_failures": self._acct.get("failures", 0) if is_demo else 0,
                "account_balances": self._acct["balances"] if is_demo else [],
                "exchange_positions": self._acct["positions"] if is_demo else [],
                "start_equity": self.s.risk.account_equity,
                "realized_today": round(self.risk.realized_today, 2),
                "daily_cap_ok": can,
                "daily_cap_reason": why,
                "entry_risk_ok": not entry_blocks,
                "entry_risk_reason": "; ".join(entry_blocks),
                "equity_curve": self.equity_curve[-200:],
                "feed": self.recent[:30],
                "trades": self.trades[-(30 if lite else 100):],
                "trade_count": len(self.trades),
                "slots": self.slots(),
                "engine": self.engine_settings(),
                "analytics": self._analytics(),
                "risk": {
                    "risk_per_trade_pct": self.s.risk.risk_per_trade_pct,
                    "stop_loss_pct": self.s.risk.stop_loss_pct,
                    "take_profit_pct": self.s.risk.take_profit_pct,
                    "max_leverage": self.s.risk.max_leverage,
                    "max_daily_loss_pct": self.s.risk.max_daily_loss_pct,
                    "max_portfolio_risk_pct": self.s.risk.max_portfolio_risk_pct,
                    "max_open_positions": self.s.risk.max_open_positions,
                    "min_reward_risk": self.s.risk.min_reward_risk,
                    "max_stop_distance_pct": self.s.risk.max_stop_distance_pct,
                    "max_same_direction_positions": self.s.risk.max_same_direction_positions,
                    "strategy_risk_pct": self.s.risk.strategy_risk_pct,
                    "max_consecutive_losses": self.s.risk.max_consecutive_losses,
                    "strategy_cooldown_minutes": self.s.risk.strategy_cooldown_minutes,
                },
                "strategy_guards": {
                    name: {"enabled": self._strategy_guard(name)[0],
                           "reason": self._strategy_guard(name)[1]}
                    for name in active_strategies
                },
                "shadow_paper": self._shadow_snapshot(),
            }

    # ── CLI loop (unchanged behaviour) ──
    def run(self):
        log.info("loop every %ds. Ctrl-C to stop.", self.s.poll_seconds)
        while True:
            try:
                outcomes = self.evaluate()
                for sym, out in outcomes.items():
                    if out.event not in ("hold", "none"):
                        log.info("%s %s | %s", sym, out.event.upper(), out.detail)
            except Exception as e:
                log.error("step error: %s", e)
            time.sleep(self.s.poll_seconds)
