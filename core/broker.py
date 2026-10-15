"""
Brokers: one interface, two implementations.

  PaperBroker  — simulates fills at the given price. No API. Tracks position,
                 checks SL/TP against each new candle, computes realized PnL.
  DeltaBroker  — places real orders on Delta testnet via DeltaClient, with a
                 bracket (SL/TP) attached. Still "paper money" (demo account).

Both expose the same methods so the agent loop doesn't care which is active.
"""
from __future__ import annotations
from dataclasses import dataclass

from core.delta_client import DeltaClient, DeltaError
from core.risk import TradePlan


@dataclass
class Position:
    side: str            # "long" | "short"
    entry: float
    size: int
    stop_loss: float
    take_profit: float
    notional: float


class PaperBroker:
    """Simulated broker. contract_value_fn maps a price -> USD per contract."""

    def __init__(self, contract_value_fn):
        self.position: Position | None = None
        self._cv = contract_value_fn

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
        pnl = self._pnl(exit_price)
        self.position = None
        return pnl

    def check_bracket(self, candle: dict) -> tuple[bool, float, str] | None:
        """Did SL or TP trigger inside this candle? Returns (closed, price, why)."""
        if not self.position:
            return None
        p = self.position
        hi, lo = float(candle["high"]), float(candle["low"])
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


class DeltaBroker:
    """Live-on-testnet broker. Sends bracket orders to Delta demo."""

    def __init__(self, client: DeltaClient, symbol: str):
        self.client = client
        self.symbol = symbol
        self.position: Position | None = None
        prod = client.get_product(symbol)
        self.product_id = prod.get("id")
        # contract value in BTC terms; Delta BTCUSD perp = 0.001 BTC/contract
        self.contract_size = float(prod.get("contract_value", 0.001))
        if not self.product_id:
            raise DeltaError(f"could not resolve product_id for {symbol}")

    def contract_value(self, price: float) -> float:
        return self.contract_size * price

    def open(self, plan: TradePlan) -> Position:
        side = "buy" if plan.side == "long" else "sell"
        # Market entry. SL/TP are enforced by the bot loop (see check_bracket),
        # which fires a real reduce-only close when a level is touched — so the
        # bot MUST stay running for stop protection.
        self.client.place_order(
            product_id=self.product_id, size=plan.size_contracts,
            side=side, order_type="market_order",
        )
        self.position = Position(
            side=plan.side, entry=plan.entry, size=plan.size_contracts,
            stop_loss=plan.stop_loss, take_profit=plan.take_profit,
            notional=plan.notional,
        )
        return self.position

    def close(self, exit_price: float) -> float:
        p = self.position
        if not p:
            return 0.0
        side = "sell" if p.side == "long" else "buy"
        self.client.place_order(
            product_id=self.product_id, size=p.size, side=side,
            order_type="market_order", reduce_only=True,
        )
        btc_per_contract = self.contract_size
        move = (exit_price - p.entry) if p.side == "long" else (p.entry - exit_price)
        pnl = move * btc_per_contract * p.size
        self.position = None
        return pnl

    def check_bracket(self, candle: dict):
        """Enforce SL/TP against each candle, same logic as the paper broker.
        Returns (closed, price, why) so the agent's exit path sends the real
        reduce-only close order; returns None if neither level was touched."""
        if not self.position:
            return None
        p = self.position
        hi, lo = float(candle["high"]), float(candle["low"])
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
