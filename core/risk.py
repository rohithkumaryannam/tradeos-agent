"""
Risk engine. Enforces the four controls you asked for:
  1. Position sizing by risk %        -> size_position()
  2. Stop-loss + take-profit          -> compute_sl_tp()
  3. Max daily loss cap               -> can_trade() / register_pnl()
  4. Leverage cap                     -> clamped inside size_position()

All numbers in USD. This is intentionally conservative: if a check can't be
satisfied, the trade is rejected rather than forced through.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date

from core.settings import RiskConfig


@dataclass
class TradePlan:
    side: str            # "long" | "short"
    entry: float
    stop_loss: float
    take_profit: float
    size_contracts: int  # number of contracts/lots
    notional: float      # USD exposure
    leverage: float
    ok: bool = True
    reject_reason: str = ""


@dataclass
class RiskEngine:
    cfg: RiskConfig
    equity: float = 0.0
    _day: date = field(default_factory=date.today)
    _day_start_equity: float = 0.0
    realized_today: float = 0.0

    def __post_init__(self):
        if self.equity == 0.0:
            self.equity = self.cfg.account_equity
        self._day_start_equity = self.equity

    # ── daily loss cap ──
    def _roll_day(self):
        today = date.today()
        if today != self._day:
            self._day = today
            self._day_start_equity = self.equity
            self.realized_today = 0.0

    def can_trade(self) -> tuple[bool, str]:
        self._roll_day()
        max_loss = self._day_start_equity * (self.cfg.max_daily_loss_pct / 100)
        if self.realized_today <= -max_loss:
            return False, (
                f"daily loss cap hit: {self.realized_today:.2f} <= -{max_loss:.2f}"
            )
        return True, ""

    def register_pnl(self, pnl: float):
        self._roll_day()
        self.realized_today += pnl
        self.equity += pnl

    # ── SL / TP ──
    def compute_sl_tp(self, side: str, entry: float) -> tuple[float, float]:
        sl_pct = self.cfg.stop_loss_pct / 100
        tp_pct = self.cfg.take_profit_pct / 100
        if side == "long":
            return entry * (1 - sl_pct), entry * (1 + tp_pct)
        else:  # short
            return entry * (1 + sl_pct), entry * (1 - tp_pct)

    # ── position sizing by risk % + leverage cap ──
    def size_position(
        self, side: str, entry: float, contract_value: float = 1.0,
        stop_loss: float | None = None, take_profit: float | None = None,
    ) -> TradePlan:
        """
        contract_value = USD value represented by 1 contract at price `entry`.
        For Delta BTCUSD perp, 1 contract = 0.001 BTC, so contract_value =
        0.001 * entry. We risk `risk_per_trade_pct` of equity to the stop.

        If stop_loss/take_profit are given (strategy-set prices), they override
        the config % bracket, and sizing is computed off that stop distance.
        """
        ok, why = self.can_trade()
        if stop_loss is not None and take_profit is not None:
            sl, tp = stop_loss, take_profit
        else:
            sl, tp = self.compute_sl_tp(side, entry)

        if not ok:
            return TradePlan(side, entry, sl, tp, 0, 0, 0, ok=False, reject_reason=why)

        risk_usd = self.equity * (self.cfg.risk_per_trade_pct / 100)
        stop_distance = abs(entry - sl)
        if stop_distance <= 0:
            return TradePlan(side, entry, sl, tp, 0, 0, 0, ok=False,
                             reject_reason="zero stop distance")

        # risk per contract = stop_distance (price) * BTC per contract
        btc_per_contract = contract_value / entry
        risk_per_contract = stop_distance * btc_per_contract
        size = int(risk_usd / risk_per_contract) if risk_per_contract > 0 else 0

        if size < 1:
            return TradePlan(side, entry, sl, tp, 0, 0, 0, ok=False,
                             reject_reason="computed size < 1 contract; risk too small")

        notional = size * contract_value
        leverage = notional / self.equity

        # leverage cap: shrink size until within cap
        if leverage > self.cfg.max_leverage:
            max_notional = self.equity * self.cfg.max_leverage
            size = int(max_notional / contract_value)
            if size < 1:
                return TradePlan(side, entry, sl, tp, 0, 0, 0, ok=False,
                                 reject_reason="leverage cap leaves < 1 contract")
            notional = size * contract_value
            leverage = notional / self.equity

        return TradePlan(side, entry, sl, tp, size, notional, leverage)
