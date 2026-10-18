"""
Brokers: one interface, two implementations.

  PaperBroker  — simulates fills at the given price. No API. Tracks position,
                 checks SL/TP against each new candle, computes realized PnL.
  DeltaBroker  — places real orders on Delta testnet via DeltaClient, with a
                 bracket (SL/TP) attached. Still "paper money" (demo account).

Both expose the same methods so the agent loop doesn't care which is active.
"""
from __future__ import annotations
import logging
import uuid
import math
from dataclasses import dataclass

from core import db
from core.delta_client import DeltaClient, DeltaError
from core.risk import TradePlan

log = logging.getLogger("tradeos")


@dataclass
class Position:
    side: str            # "long" | "short"
    entry: float
    size: int
    stop_loss: float
    take_profit: float
    notional: float
    # True only once Delta has confirmed a real exchange-side bracket is
    # attached to this position (DELTA_DEMO). Always False for PaperBroker —
    # there's no exchange to attach a bracket to.
    bracket_confirmed: bool = False


class PaperBroker:
    """Simulated broker. contract_value_fn maps a price -> USD per contract."""

    def __init__(self, contract_value_fn, fee_bps: float = 0):
        self.position: Position | None = None
        self._cv = contract_value_fn
        self.fee_bps = fee_bps

    def contract_value(self, price: float) -> float:
        return self._cv(price)

    def open(self, plan: TradePlan) -> Position:
        self.position = Position(
            side=plan.side, entry=plan.entry, size=plan.size_contracts,
            stop_loss=plan.stop_loss, take_profit=plan.take_profit,
            notional=plan.notional,
        )
        return self.position

    def _pnl(self, exit_price: float) -> float:
        p = self.position
        btc_per_contract = p.notional / p.entry / p.size if p.size else 0
        move = (exit_price - p.entry) if p.side == "long" else (p.entry - exit_price)
        return move * btc_per_contract * p.size

    def close(self, exit_price: float) -> float:
        p = self.position
        fees = (p.notional + p.notional / p.entry * exit_price) * self.fee_bps / 10_000
        pnl = self._pnl(exit_price) - fees
        self.position = None
        return pnl

    def check_bracket(self, candle: dict) -> tuple[bool, float, str] | None:
        """Did SL or TP trigger inside this candle? Returns (closed, price, why)."""
        if not self.position:
            return None
        p = self.position
        hi, lo = float(candle["high"]), float(candle["low"])
        opening = float(candle.get("open", candle.get("close", p.entry)))
        if p.side == "long":
            if lo <= p.stop_loss:
                return True, min(opening, p.stop_loss), "stop-loss hit"
            if hi >= p.take_profit:
                return True, p.take_profit, "take-profit hit"
        else:
            if hi >= p.stop_loss:
                return True, max(opening, p.stop_loss), "stop-loss hit"
            if lo <= p.take_profit:
                return True, p.take_profit, "take-profit hit"
        return None


class DeltaBroker:
    """Live-on-testnet broker. Sends bracket orders to Delta demo."""

    def __init__(self, client: DeltaClient, symbol: str, reconcile: bool = True):
        self.client = client
        self.symbol = symbol
        self.position: Position | None = None
        # set by check_bracket() when it detects Delta already closed the
        # position server-side (bracket fired) — close() then skips sending a
        # redundant reduce-only order but still computes PnL normally.
        self._already_closed_on_exchange = False
        self.execution_issue = None
        prod = client.get_product(symbol)
        self.product_id = prod.get("id")
        # contract value in BTC terms; Delta BTCUSD perp = 0.001 BTC/contract
        self.contract_size = float(prod.get("contract_value", 0))
        if not math.isfinite(self.contract_size) or self.contract_size <= 0:
            raise DeltaError(f"invalid contract specification for {symbol}")
        if not self.product_id:
            raise DeltaError(f"could not resolve product_id for {symbol}")
        if reconcile:
            self.reconcile()

    def reconcile(self, positions=None):
        """Adopt a position that already exists on Delta but that this process
        doesn't know about — e.g. after a restart. Without this, a restart
        with a real open position would make the bot think it's flat and risk
        opening a second, conflicting position. Recovers SL/TP from the
        resting bracket legs if one is attached; if not, the position is
        still adopted (so we never double-enter) but stop_loss/take_profit
        stay None — honestly reported as unprotected rather than guessed."""
        try:
            positions = self.client.get_positions() if positions is None else positions
        except DeltaError as e:
            self.execution_issue = "startup reconciliation failed; entries blocked"
            log.warning("could not reconcile %s on startup: %s", self.symbol, e)
            return
        before = None if self.position is None else (
            self.position.side, self.position.size, self.position.entry,
            self.position.stop_loss, self.position.take_profit,
        )
        live = next((p for p in positions
                    if p.get("product_id") == self.product_id
                    and (float(p.get("size") or 0)) != 0), None)
        if not live:
            self.position = None
            self.execution_issue = None
            return
        size = float(live["size"])
        side = "long" if size > 0 else "short"
        entry = float(live.get("entry_price") or 0)
        try:
            sl, tp = self.client.get_bracket_prices(self.product_id)
            self.execution_issue = None
        except DeltaError as e:
            sl = tp = None
            self.execution_issue = "bracket reconciliation failed; entries blocked"
            log.warning("could not reconcile %s bracket: %s", self.symbol, e)
        bracket_confirmed = sl is not None and tp is not None
        if not bracket_confirmed:
            # Delta has no (or only a partial) resting bracket for this
            # position — fall back to whatever stop/target WE originally
            # intended at entry (persisted in Mongo), so the position still
            # gets local (software) protection instead of running naked.
            # Never claim this is an exchange bracket.
            fallback = db.load_position_bracket(self.symbol)
            if fallback and abs(float(fallback.get("entry", 0)) - entry) < max(entry * 0.001, 1e-6):
                sl = sl if sl is not None else fallback.get("stop_loss")
                tp = tp if tp is not None else fallback.get("take_profit")
                if sl is not None or tp is not None:
                    log.warning("%s exchange bracket incomplete; using locally "
                                "remembered stop/target instead", self.symbol)
        notional = abs(size) * self.contract_size * entry
        self.position = Position(
            side=side, entry=entry, size=int(abs(size)),
            stop_loss=sl, take_profit=tp, notional=notional,
            bracket_confirmed=bracket_confirmed,
        )
        after = (self.position.side, self.position.size, self.position.entry,
                 self.position.stop_loss, self.position.take_profit)
        if before != after:
            log.info("reconciled existing %s position: %s %s @ %s (bracket=%s)",
                     self.symbol, side, int(abs(size)), entry, self.position.bracket_confirmed)

    def contract_value(self, price: float) -> float:
        return self.contract_size * price

    def open(self, plan: TradePlan) -> Position:
        if self.position or self.execution_issue:
            raise DeltaError(self.execution_issue or "position already open")
        side = "buy" if plan.side == "long" else "sell"
        # Market entry WITH a real exchange-side bracket (resting SL/TP) — per
        # docs.delta.exchange's CreateOrderRequest, bracket_stop_loss_price +
        # bracket_take_profit_price on the entry order attach a server-side
        # bracket, so protection survives even if this process goes down.
        # Fail-closed: if Delta doesn't confirm the bracket, the position is
        # still opened (we're already filled) but bracket_confirmed stays
        # False, and the bot loop's own check_bracket keeps watching as a
        # fallback — never silently claim exchange protection we didn't get.
        client_order_id = uuid.uuid4().hex
        try:
            order = self.client.place_order(
                product_id=self.product_id, size=plan.size_contracts,
                side=side, order_type="market_order",
                bracket_stop_loss_price=plan.stop_loss,
                bracket_take_profit_price=plan.take_profit,
                client_order_id=client_order_id,
            )
        except Exception:
            # A timeout can happen AFTER the exchange accepted the order.
            # Never submit a second market entry to recover an unknown result.
            self.execution_issue = f"entry outcome unresolved ({client_order_id}); verify on Delta before restart"
            raise DeltaError(self.execution_issue)
        self.position = Position(
            side=plan.side, entry=plan.entry, size=plan.size_contracts,
            stop_loss=plan.stop_loss, take_profit=plan.take_profit,
            notional=plan.notional, bracket_confirmed=False,
        )
        # Record the position before the extra network call: a bracket lookup
        # failure must never erase an accepted entry from local state.
        try:
            self.position.bracket_confirmed = self.client.has_bracket(self.product_id)
        except Exception as e:
            log.warning("bracket verification unavailable for %s: %s", self.symbol, e)
        return self.position

    def close(self, exit_price: float) -> float:
        p = self.position
        if not p:
            return 0.0
        if self._already_closed_on_exchange:
            # Delta's bracket already closed it server-side (see check_bracket)
            # — sending another reduce-only order here would be redundant.
            self._already_closed_on_exchange = False
        else:
            side = "sell" if p.side == "long" else "buy"
            # A plain market order can get rejected outright with
            # "out_of_bankruptcy" on a thin/low-margin position (Delta adds a
            # worst-case-slippage safety margin to market orders that a small
            # isolated-margin position can't absorb, even though the position
            # itself is nowhere near its actual bankruptcy price). A
            # marketable limit order — priced to cross the book immediately —
            # gets the same effective fill without tripping that check.
            buffer = max(abs(exit_price) * 0.01, 0.01)
            limit_price = round(exit_price - buffer if side == "sell" else exit_price + buffer, 6)
            try:
                self.client.place_order(
                    product_id=self.product_id, size=p.size, side=side,
                    order_type="limit_order", limit_price=limit_price, reduce_only=True,
                )
            except DeltaError as e:
                if "out_of_bankruptcy" not in str(e):
                    raise
                # Still rejected even as a marketable limit — widen the
                # buffer once and retry before giving up (never silently
                # leave the position open after a stop/target was reached).
                buffer *= 5
                limit_price = round(exit_price - buffer if side == "sell" else exit_price + buffer, 6)
                self.client.place_order(
                    product_id=self.product_id, size=p.size, side=side,
                    order_type="limit_order", limit_price=limit_price, reduce_only=True,
                )
        btc_per_contract = self.contract_size
        move = (exit_price - p.entry) if p.side == "long" else (p.entry - exit_price)
        pnl = move * btc_per_contract * p.size
        self.position = None
        return pnl

    def check_bracket(self, candle: dict):
        """Apply local protection only when no exchange bracket is confirmed.

        Confirmed brackets are reconciled by Agent's single account refresh,
        which covers every market and records the authoritative exit fill.
        Avoiding a second signed positions request here also lets the account
        circuit breaker suppress repeated authentication/IP errors.
        """
        if not self.position:
            return None
        p = self.position
        if p.bracket_confirmed:
            return None

        if p.stop_loss is None or p.take_profit is None:
            return None
        hi = lo = float(candle["close"])
        if p.side == "long":
            if lo <= p.stop_loss:
                return True, p.stop_loss, "stop-loss hit"
            if hi >= p.take_profit:
                return True, p.take_profit, "take-profit hit"
        else:
            if hi >= p.stop_loss:
                return True, p.stop_loss, "stop-loss hit"
            if lo <= p.take_profit:
                return True, p.take_profit, "take-profit hit"
        return None
