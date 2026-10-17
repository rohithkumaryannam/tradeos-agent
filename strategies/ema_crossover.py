"""EMA 9/20 crossover, filtered by a longer EMA200 trend.

  - EMA9 crosses UP through EMA20, price above EMA200   -> LONG
  - EMA9 crosses DOWN through EMA20, price below EMA200 -> SHORT
  - A cross against the EMA200 trend is skipped (filtered) — this is what
    keeps the strategy out of most whipsaws in a choppy/ranging market.
  - Exit on the opposite cross (regardless of trend, so an open position
    always closes cleanly).

Set trend=0 to disable the filter and trade the raw crossover.

Stop-loss / take-profit come from the risk config (this strategy returns
direction only), so sizing and the 1:2 bracket are enforced by the risk engine.
Select it in config.yaml:
    strategy: { name: ema_crossover, params: { fast: 9, mid: 20, trend: 200 } }
"""
from __future__ import annotations

from core.indicators import ema, closes
from strategies.base import BaseStrategy, Signal, Action

name = "ema_crossover"


class Strategy(BaseStrategy):
    def __init__(self, fast=9, mid=20, trend=200, **kw):
        super().__init__(fast=fast, mid=mid, trend=trend, **kw)
        self.fast, self.mid, self.trend = fast, mid, trend
        self.required_candles = max(mid, trend) + 2

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")
        c = closes(candles)
        ef, em = ema(c, self.fast), ema(c, self.mid)
        if None in (ef[-1], ef[-2], em[-1], em[-2]):
            return Signal(Action.HOLD, "warming up")

        cross_up = ef[-2] <= em[-2] and ef[-1] > em[-1]
        cross_down = ef[-2] >= em[-2] and ef[-1] < em[-1]

        # exits first — opposite cross, regardless of trend
        if position_side == "long" and cross_down:
            return Signal(Action.EXIT, "EMA9 crossed below EMA20")
        if position_side == "short" and cross_up:
            return Signal(Action.EXIT, "EMA9 crossed above EMA20")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        if not (cross_up or cross_down):
            return Signal(Action.HOLD, "no cross")

        et = ema(c, self.trend)[-1] if self.trend else None
        uptrend = et is None or c[-1] > et
        downtrend = et is None or c[-1] < et

        if cross_up and uptrend:
            return Signal(Action.ENTER_LONG, "EMA9 crossed above EMA20, price above EMA200")
        if cross_down and downtrend:
            return Signal(Action.ENTER_SHORT, "EMA9 crossed below EMA20, price below EMA200")
        return Signal(Action.HOLD, "cross against EMA200 trend; filtered")
