"""
The agent. Separates DECISION from EXECUTION so the dashboard can run it in
auto mode (execute immediately) or manual mode (surface a pending signal and
wait for the user to confirm).

Key methods:
  evaluate()         -> runs one cycle; manages SL/TP; returns an Outcome
  execute_pending()  -> in manual mode, act on the last pending entry/exit
  snapshot()         -> full state dict for the UI / WebSocket
"""
from __future__ import annotations
import csv
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from core.settings import Settings
from core.delta_client import DeltaClient
from core.broker import PaperBroker, DeltaBroker
from core.indicators import ema, rsi, closes
from core.risk import RiskEngine
from core.trade_log import TradeLog
from core import ai
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


class Agent:
    def __init__(self, settings: Settings, client: DeltaClient | None = None):
        self.s = settings
        self.lock = threading.RLock()
        self.client = client or DeltaClient(settings.api_key, settings.api_secret)
        self.reload_strategy()
        self.risk = RiskEngine(settings.risk)
        self.tlog = TradeLog(settings.trade_log)
        self._build_broker()

        # structured completed-trade ledger (persists via the CSV log)
        self.trades: list[dict] = []
        self._open_trade: dict | None = None
        self._load_trades_from_log()

        self.auto = True                 # auto-execute vs manual confirm
        self.pending: Signal | None = None
        self.pending_price: float | None = None
        self.last_candles: list[dict] = []
        self.equity_curve: list[dict] = [
            {"t": self._now(), "equity": self.risk.equity}
        ]
        self.recent: list[dict] = []     # recent events for the UI feed
        # live Delta account cache (DELTA_DEMO only); fail-closed.
        self._acct = {"balances": [], "positions": [], "usd": None,
                      "status": "n/a", "ts": 0.0}
        if self.s.mode == "DELTA_DEMO":
            self._refresh_account(force=True)
            if self._acct["status"] == "live" and self._acct["usd"] is not None:
                self._reseed_equity(self._acct["usd"])
        log.info("Agent ready | mode=%s strategy=%s %s %s",
                 settings.mode, settings.strategy_name,
                 settings.symbol, settings.resolution)

    # ── setup helpers ──
    def _build_broker(self):
        if self.s.mode == "DELTA_DEMO":
            self.broker = DeltaBroker(self.client, self.s.symbol)
        else:
            self.broker = PaperBroker(lambda price: 0.001 * price)

    def reload_strategy(self):
        self.strategy = load_strategy(self.s.strategy_name, self.s.strategy_params)

    def set_strategy(self, name: str):
        """Switch the active strategy from the UI. Uses the strategy's own
        defaults (config params only apply to the config-selected strategy)."""
        with self.lock:
            if name not in available_strategies():
                raise RuntimeError(f"unknown strategy: {name}")
            if name == self.s.strategy_name:
                return
            self.s.strategy_name = name
            self.s.strategy_params = {}
            self.reload_strategy()
            self._feed("mode", f"strategy -> {name}")

    def set_mode(self, mode: str):
        with self.lock:
            if mode == self.s.mode:
                return
            if self.broker.position is not None:
                raise RuntimeError("close the open position before switching mode")
            self.s.mode = mode
            self._build_broker()
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
            if not auto:
                pass  # keep any pending signal
            self._feed("mode", "auto-execute ON" if auto else "manual confirm ON")

    _BAL_TTL = 20  # seconds between live account polls

    @staticmethod
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _refresh_account(self, force: bool = False):
        """DELTA_DEMO only: pull the full testnet account — every wallet asset
        balance plus live exchange positions — cached ~20s so the signed endpoints
        aren't hammered. Fail-closed: on any error, mark the account unavailable
        rather than showing stale or fabricated numbers."""
        if self.s.mode != "DELTA_DEMO":
            self._acct = {"balances": [], "positions": [], "usd": None,
                          "status": "n/a", "ts": time.time()}
            return
        if not force and (time.time() - self._acct["ts"] < self._BAL_TTL):
            return
        try:
            balances = [
                {"asset": e.get("asset_symbol") or "?",
                 "balance": self._f(e.get("balance")),
                 "available": self._f(e.get("available_balance"))}
                for e in (self.client.get_balance() or [])
            ]
            usd = next((b["balance"] for b in balances
                        if (b["asset"] or "").upper() in ("USD", "USDT")), None)
            try:
                raw_pos = self.client.get_positions()
            except Exception:
                raw_pos = []
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
            self._acct = {"balances": balances, "positions": positions,
                          "usd": usd, "status": "live", "ts": time.time()}
        except Exception as e:
            self._acct = {"balances": [], "positions": [], "usd": None,
                          "status": "unavailable", "ts": time.time()}
            log.warning("account fetch failed: %s", e)

    def _reseed_equity(self, equity: float):
        """Re-base the risk engine (and the equity curve) on a new starting
        equity — used when switching modes so sizing reflects the right account."""
        self.risk.equity = equity
        self.risk._day_start_equity = equity
        self.risk.realized_today = 0.0
        self.equity_curve = [{"t": self._now(), "equity": equity}]

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _feed(self, event, detail):
        self.recent.insert(0, {"t": self._now(), "event": event, "detail": detail})
        self.recent = self.recent[:50]

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

    def _mk_trade(self, e: dict, exit_ts, exit_price, pnl, reason) -> dict:
        """Build a completed-trade record from an entry dict + exit data."""
        entry, sl, size, side = e["entry"], e.get("sl"), e["size"], e["side"]
        stop_dist = abs(entry - sl) if sl else 0
        move = (exit_price - entry) if side == "long" else (entry - exit_price)
        r = round(move / stop_dist, 2) if stop_dist else None
        notional = size * 0.001 * entry
        return {
            "entry_ts": e.get("entry_ts"), "exit_ts": exit_ts,
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
        """Rebuild the completed-trade ledger by pairing ENTRY→EXIT rows in the
        CSV, so history survives restarts. Best-effort; skips malformed rows."""
        path = self.s.trade_log
        if not os.path.exists(path):
            return
        try:
            with open(path, newline="") as f:
                pending = None
                for row in csv.DictReader(f):
                    ev = (row.get("event") or "").upper()
                    if ev == "ENTRY":
                        pending = {
                            "entry_ts": row.get("ts"), "side": row.get("side"),
                            "entry": float(row["price"]), "size": int(float(row["size"])),
                            "sl": float(row["sl"]) if row.get("sl") else None,
                            "tp": float(row["tp"]) if row.get("tp") else None,
                        }
                    elif ev == "EXIT" and pending:
                        self.trades.append(self._mk_trade(
                            pending, row.get("ts"), float(row["price"]),
                            float(row.get("pnl") or 0), row.get("reason")))
                        pending = None
            self.trades = self.trades[-500:]
        except Exception as e:
            log.warning("could not load trade history: %s", e)

    def _analytics(self) -> dict:
        ts = self.trades
        n = len(ts)
        base = {"trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "total_pnl": 0.0, "avg_win": None, "avg_loss": None,
                "profit_factor": None, "avg_r": None, "best": None,
                "worst": None, "by_reason": {}}
        if not n:
            return base
        wins = [t for t in ts if t["win"]]
        losses = [t for t in ts if not t["win"]]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = -sum(t["pnl"] for t in losses)     # positive magnitude
        rs = [t["r"] for t in ts if t["r"] is not None]
        by_reason: dict[str, int] = {}
        for t in ts:
            by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
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
                        "(max 20 words), explain this BTC futures action. No preamble."),
                max_tokens=100, temperature=0.5, thinking_budget=0,
            )
            if txt:
                with self.lock:
                    self._feed("ai", txt.replace("\n", " ").strip()[:200])

        threading.Thread(target=work, daemon=True).start()

    def ai_analysis(self) -> dict:
        """On-demand Gemini read of current chart conditions. Returns a dict the
        dashboard renders. Honest states: not-configured vs. unavailable vs. ok."""
        if not ai.available():
            return {"available": False, "text": "Gemini key not configured (set GEMINI_API_KEY in .env)."}
        with self.lock:
            candles = self.last_candles
            if not candles:
                return {"available": True, "text": "No market data yet — start the bot or run a cycle."}
            c = closes(candles)
            ef, es, r = ema(c, 9), ema(c, 21), rsi(c, 14)
            pos = self.broker.position
            prompt = (
                f"BTC {self.s.symbol} {self.s.resolution}. "
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
    def fetch_candles(self) -> list[dict]:
        sec = _RES_SECONDS.get(self.s.resolution, 300)
        end = int(time.time())
        start = end - sec * (self.s.lookback + 5)
        return self.client.get_candles(self.s.symbol, self.s.resolution, start, end)

    # ── one evaluation cycle ──
    def evaluate(self) -> Outcome:
        with self.lock:
            self._refresh_account()  # cached; no-op in paper / within TTL
            candles = self.fetch_candles()
            if len(candles) < 5:
                return Outcome("hold", "too few candles")
            self.last_candles = candles
            last = candles[-1]
            price = float(last["close"])

            # 1. manage open position (SL/TP) — always automatic, even in manual mode
            if self.broker.position is not None:
                bracket = self.broker.check_bracket(last)
                if bracket:
                    _, exit_price, why = bracket
                    return self._do_exit(exit_price, why)

            pos_side = self.broker.position.side if self.broker.position else None
            sig = self.strategy.generate(candles, pos_side)

            if sig.action == Action.HOLD:
                self.pending = None
                return Outcome("hold", sig.reason)

            # strategy exit
            if sig.action == Action.EXIT and self.broker.position is not None:
                if self.auto:
                    return self._do_exit(price, sig.reason)
                self.pending, self.pending_price = sig, price
                self._feed("pending", f"EXIT — {sig.reason}")
                return Outcome("pending", sig.reason, sig.action.value)

            # entries
            if sig.action in (Action.ENTER_LONG, Action.ENTER_SHORT) and pos_side is None:
                if self.auto:
                    return self._open_from_signal(sig, price)
                self.pending, self.pending_price = sig, price
                self._feed("pending", f"{sig.action.value} — {sig.reason}")
                return Outcome("pending", sig.reason, sig.action.value)

            return Outcome("hold", sig.reason)

    def execute_pending(self) -> Outcome:
        """Manual-mode confirm: act on the stored pending signal at current price."""
        with self.lock:
            if not self.pending:
                return Outcome("none", "nothing pending")
            sig = self.pending
            price = float(self.last_candles[-1]["close"]) if self.last_candles else self.pending_price
            self.pending = None
            if sig.action == Action.EXIT and self.broker.position is not None:
                return self._do_exit(price, "manual confirm: " + sig.reason)
            if sig.action in (Action.ENTER_LONG, Action.ENTER_SHORT):
                return self._open_from_signal(sig, price)
            return Outcome("none", "pending no longer valid")

    def dismiss_pending(self) -> Outcome:
        with self.lock:
            self.pending = None
            return Outcome("none", "pending dismissed")

    def force_close(self) -> Outcome:
        with self.lock:
            if self.broker.position is None:
                return Outcome("none", "no open position")
            price = float(self.last_candles[-1]["close"]) if self.last_candles else self.broker.position.entry
            return self._do_exit(price, "manual close")

    # ── internal execution ──
    def _open_from_signal(self, sig: Signal, price: float) -> Outcome:
        side = "long" if sig.action == Action.ENTER_LONG else "short"
        cv = self.broker.contract_value(price)
        plan = self.risk.size_position(side, price, contract_value=cv,
                                       stop_loss=sig.stop_loss, take_profit=sig.take_profit)
        if not plan.ok:
            self._feed("reject", plan.reject_reason)
            self.tlog.write("REJECT", side=side, price=f"{price:.1f}",
                            reason=plan.reject_reason, equity=f"{self.risk.equity:.2f}")
            return Outcome("reject", plan.reject_reason)
        self.broker.open(plan)
        self._open_trade = {
            "entry_ts": self._now(), "side": side, "entry": price,
            "size": plan.size_contracts, "sl": plan.stop_loss, "tp": plan.take_profit,
        }
        self._feed("entry", f"{side.upper()} {plan.size_contracts}c @ {price:.1f} "
                            f"SL {plan.stop_loss:.0f} TP {plan.take_profit:.0f}")
        self._narrate(f"Opened {side.upper()} {plan.size_contracts} contracts @ {price:.1f}, "
                      f"SL {plan.stop_loss:.0f}, TP {plan.take_profit:.0f}. Signal: {sig.reason}")
        self.tlog.write("ENTRY", side=side, price=f"{price:.1f}",
                        size=plan.size_contracts, sl=f"{plan.stop_loss:.1f}",
                        tp=f"{plan.take_profit:.1f}", equity=f"{self.risk.equity:.2f}",
                        reason=sig.reason)
        return Outcome("entry", sig.reason)

    def _do_exit(self, exit_price: float, why: str) -> Outcome:
        side = self.broker.position.side
        pnl = self.broker.close(exit_price)
        self.risk.register_pnl(pnl)
        self.equity_curve.append({"t": self._now(), "equity": self.risk.equity})
        self.equity_curve = self.equity_curve[-500:]
        if self._open_trade:
            self.trades.append(self._mk_trade(
                self._open_trade, self._now(), exit_price, pnl, why))
            self.trades = self.trades[-500:]
            self._open_trade = None
        self._feed("exit", f"{side.upper()} @ {exit_price:.1f}  PnL {pnl:+.2f}  ({why})")
        self.tlog.write("EXIT", side=side, price=f"{exit_price:.1f}",
                        pnl=f"{pnl:.2f}", equity=f"{self.risk.equity:.2f}", reason=why)
        self._narrate(f"Closed {side.upper()} @ {exit_price:.1f} for PnL {pnl:+.2f}. Reason: {why}")
        return Outcome("exit", f"{why} | PnL {pnl:+.2f}")

    def _protection_status(self, pos) -> str:
        """Truthful SL/TP protection state. SL/TP are enforced by the bot loop
        each cycle (DeltaBroker.check_bracket fires a real reduce-only close on
        Delta when a level is touched) — so protection is active ONLY while the
        bot is running, in both modes."""
        if pos.stop_loss is None or pos.take_profit is None:
            return "none"
        return "local"

    # ── state snapshot for UI ──
    def snapshot(self) -> dict:
        with self.lock:
            candles = self.last_candles
            c = closes(candles) if candles else []
            ef = ema(c, 9) if c else []
            es = ema(c, 21) if c else []
            r = rsi(c, 14) if c else []
            # trim to last 120 for the chart
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
                    "rsi": r[i] if i < len(r) else None,
                })
            pos = self.broker.position
            price = float(candles[-1]["close"]) if candles else None
            unrealized = None
            if pos and price:
                move = (price - pos.entry) if pos.side == "long" else (pos.entry - price)
                unrealized = move * (pos.notional / pos.entry / pos.size) * pos.size
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
            return {
                "ts": self._now(),
                "mode": self.s.mode,
                "auto": self.auto,
                "symbol": self.s.symbol,
                "resolution": self.s.resolution,
                "strategy": self.s.strategy_name,
                "strategies": available_strategies(),
                "venue": "Delta testnet (demo)" if is_demo else "Paper (simulated fills)",
                "price_source": "Delta BTCUSD candles",
                "ai_available": ai.available(),
                "price": price,
                "equity": equity,
                "equity_status": equity_status,
                "equity_asset": "USD",
                "account_status": self._acct["status"] if is_demo else "n/a",
                "account_balances": self._acct["balances"] if is_demo else [],
                "exchange_positions": self._acct["positions"] if is_demo else [],
                "start_equity": self.s.risk.account_equity,
                "realized_today": round(self.risk.realized_today, 2),
                "daily_cap_ok": can,
                "daily_cap_reason": why,
                "position": None if not pos else {
                    "side": pos.side, "entry": pos.entry, "size": pos.size,
                    "sl": pos.stop_loss, "tp": pos.take_profit,
                    "notional": round(pos.notional, 2),
                    "unrealized": round(unrealized, 2) if unrealized is not None else None,
                    "protection": self._protection_status(pos),
                    "reconciliation": "polled from exchange" if is_demo else "n/a (simulated)",
                },
                "pending": None if not self.pending else {
                    "action": self.pending.action.value, "reason": self.pending.reason,
                    "price": self.pending_price,
                },
                "chart": chart,
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
                },
            }

    # ── CLI loop (unchanged behaviour) ──
    def run(self):
        log.info("loop every %ds. Ctrl-C to stop.", self.s.poll_seconds)
        while True:
            try:
                out = self.evaluate()
                if out.event not in ("hold", "none"):
                    log.info("%s | %s", out.event.upper(), out.detail)
            except Exception as e:
                log.error("step error: %s", e)
            time.sleep(self.s.poll_seconds)
