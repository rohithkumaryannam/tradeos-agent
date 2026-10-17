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
import json
import logging
import os
import threading
import time
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from core.settings import RiskConfig, Settings
from core.delta_client import DeltaClient, DeltaError
from core.broker import PaperBroker, DeltaBroker
from core.indicators import ema, rsi, closes
from core.risk import RiskEngine
from core.trade_log import TradeLog
from core import ai, db
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
    pending signal. A strategy instance is NOT shared across symbols because
    strategies like ny_range_fakeout/combo hold per-symbol state (an armed
    setup, which sub-strategy owns the open trade, "already traded today")."""
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

        # structured completed-trade ledger (persists via the CSV log)
        self.trades: list[dict] = []
        self.trade_history_limit = 500
        self._load_trades_from_log()

        self.auto = True                 # auto-execute vs manual confirm
        self.entries_paused = False
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
        m.strategy = load_strategy(self.s.strategy_name, self.s.strategy_params)

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
            candidates = {sym: load_strategy(name, {}) for sym in self.markets}
            self.s.strategy_name = name
            self.s.strategy_params = {}
            for sym, m in self.markets.items():
                m.strategy = candidates[sym]
                m.pending = None
                m.last_candles = []
                m.last_entry_candle = None
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
            "max_portfolio_risk_pct": (0.1, 10.0, float),
            "max_open_positions": (1, 10, int),
            "max_same_direction_positions": (1, 10, int),
            "max_leverage": (1, 25, int),
            "max_daily_loss_pct": (0.1, 20.0, float),
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
            self._feed("risk", "risk controls updated from dashboard")
        return new_config

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
                    m.broker.reconcile(raw_pos)
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
            "side": side, "entry": round(entry, 1), "exit": round(exit_price, 1),
            "size": size, "sl": round(sl, 1) if sl else None,
            "tp": round(e["tp"], 1) if e.get("tp") else None,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / notional * 100, 2) if notional else None,
            "r": r,
            "reason": self._classify_exit(reason),
            "win": pnl >= 0,
            "duration_s": self._dur_s(e.get("entry_ts"), exit_ts),
        }

    def _load_trades_from_log(self):
        """Rebuild the completed-trade ledger by pairing ENTRY→EXIT rows (per
        symbol), so history survives restarts — including on hosts with an
        ephemeral filesystem (e.g. Render), where the local CSV resets on
        every redeploy but MongoDB (if configured) doesn't. Prefers Mongo;
        falls back to the local CSV. Best-effort; skips malformed rows and
        rows from a log written before the symbol column existed (they load
        with symbol '—')."""
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
            self._feed("exit", f"[{m.symbol}] {entry['side'].upper()} @ {exit_price:.1f}  "
                               f"PnL {pnl:+.2f}  ({reason})")
            self.tlog.write("EXIT", side=entry["side"], price=f"{exit_price:.1f}",
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
        """On-demand Gemini read of current chart conditions for one symbol.
        Returns a dict the dashboard renders. Honest states: not-configured vs.
        unavailable vs. ok."""
        if not ai.available():
            return {"available": False, "text": "Gemini key not configured (set GEMINI_API_KEY in .env)."}
        with self.lock:
            m = self._market(symbol)
            candles = m.last_candles
            if not candles:
                return {"available": True, "text": "No market data yet — start the bot or run a cycle."}
            c = closes(candles)
            ef, es, r = ema(c, 9), ema(c, 21), rsi(c, 14)
            pos = m.broker.position
            prompt = (
                f"{m.symbol} {self.s.resolution}. "
                f"Price {c[-1]:.1f}, EMA9 {ef[-1]:.1f}, EMA21 {es[-1]:.1f}, "
                f"RSI14 {r[-1]:.1f}. "
                f"Position: {'flat' if not pos else pos.side}. "
                "Give a brief read of trend, momentum and what to watch. 3 short sentences max."
            )
        txt = ai.ask(prompt, system="You are a concise crypto market analyst. No financial advice disclaimers.",
                     max_tokens=300, thinking_budget=0)
        if not txt:
            return {"available": True, "text": "AI unavailable (check GEMINI_API_KEY / model / network)."}
        return {"available": True, "text": txt, "model": ai.model_name(), "ts": self._now()}

    # ── data ──
    def fetch_candles(self, symbol: str) -> list[dict]:
        sec = _RES_SECONDS.get(self.s.resolution, 300)
        end = int(self.clock())
        start = end - sec * (self.s.lookback + 5)
        return self.client.get_candles(symbol, self.s.resolution, start, end)

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
            self.last_cycle_at = self._now()
            self.last_cycle_ms = round((time.monotonic() - started) * 1000)
            return outcomes

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
            m = self._market(symbol)
            if m.broker.position is None:
                return Outcome("none", "no open position")
            price = float(m.last_candles[-1]["close"]) if m.last_candles else m.broker.position.entry
            return self._do_exit(m, price, "manual close")

    # ── internal execution ──
    def _open_from_signal(self, m: Market, sig: Signal, price: float) -> Outcome:
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
        if portfolio["unprotected"]:
            return Outcome("reject", "unprotected position; entries blocked")
        side = "long" if sig.action == Action.ENTER_LONG else "short"
        strat = sig.strategy or self.s.strategy_name
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
                                       risk_pct=self.s.risk.risk_for(strat))
        if not plan.ok:
            self._feed("reject", f"[{m.symbol}] {plan.reject_reason}")
            self.tlog.write("REJECT", side=side, price=f"{price:.1f}",
                            reason=plan.reject_reason, equity=f"{self.risk.equity:.2f}",
                            symbol=m.symbol)
            return Outcome("reject", plan.reject_reason)
        m.broker.open(plan)
        m.last_entry_candle = stamp
        m.open_trade = {
            "entry_ts": self._now(), "side": side, "entry": price,
            "size": plan.size_contracts, "sl": plan.stop_loss, "tp": plan.take_profit,
            "strategy": strat, "contract_value": cv,
        }
        self._feed("entry", f"[{m.symbol}/{strat}] {side.upper()} {plan.size_contracts}c @ {price:.1f} "
                            f"SL {plan.stop_loss:.0f} TP {plan.take_profit:.0f}")
        self._narrate(f"Opened {m.symbol} {side.upper()} {plan.size_contracts} contracts @ {price:.1f}, "
                      f"SL {plan.stop_loss:.0f}, TP {plan.take_profit:.0f}. Signal: {sig.reason}")
        self.tlog.write("ENTRY", side=side, price=f"{price:.1f}",
                        size=plan.size_contracts, sl=f"{plan.stop_loss:.1f}",
                        tp=f"{plan.take_profit:.1f}", equity=f"{self.risk.equity:.2f}",
                        reason=sig.reason, strategy=strat, symbol=m.symbol)
        return Outcome("entry", sig.reason)

    def _do_exit(self, m: Market, exit_price: float, why: str) -> Outcome:
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
        self._feed("exit", f"[{m.symbol}] {side.upper()} @ {exit_price:.1f}  PnL {pnl:+.2f}  ({why})")
        self.tlog.write("EXIT", side=side, price=f"{exit_price:.1f}",
                        pnl=f"{pnl:.2f}", equity=f"{self.risk.equity:.2f}", reason=why,
                        symbol=m.symbol)
        self._narrate(f"Closed {m.symbol} {side.upper()} @ {exit_price:.1f} for PnL {pnl:+.2f}. Reason: {why}")
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

    def _market_snapshot(self, m: Market) -> dict:
        candles = m.last_candles
        c = closes(candles) if candles else []
        ef = ema(c, 9) if c else []
        es = ema(c, 20) if c else []
        et = ema(c, 200) if c else []
        r = rsi(c, 14) if c else []
        n = 120
        chart = []
        start = max(0, len(candles) - n)
        for i in range(start, len(candles)):
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
        return {
            "symbol": m.symbol,
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
    def snapshot(self) -> dict:
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
            markets = {sym: self._market_snapshot(m) for sym, m in self.markets.items()}
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
            first_strategy = next(iter(self.markets.values())).strategy if self.markets else None
            active_strategies = [
                name for name, _ in getattr(first_strategy, "subs", [])
            ] or [self.s.strategy_name]
            return {
                "ts": self._now(),
                "mode": self.s.mode,
                "auto": self.auto,
                "entries_paused": self.entries_paused,
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
                "trades": self.trades[-100:],
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
                },
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
