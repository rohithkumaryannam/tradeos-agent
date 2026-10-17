"""Pure EMA 9/20 crossover — no trend filter.

  - EMA9 crosses UP through EMA20   -> LONG
  - EMA9 crosses DOWN through EMA20 -> SHORT
  - Exit on the opposite cross.

Stop-loss / take-profit come from the risk config (this strategy returns
direction only), so sizing and the 1:2 bracket are enforced by the risk engine.
Select it in config.yaml:
    strategy: { name: ema_crossover, params: { fast: 9, mid: 20 } }
"""
from __future__ import annotations

from core.indicators import ema, closes
from strategies.base import BaseStrategy, Signal, Action

name = "ema_crossover"


class Strategy(BaseStrategy):
    def __init__(self, fast=9, mid=20, **kw):
        super().__init__(fast=fast, mid=mid, **kw)
        self.fast, self.mid = fast, mid

    def generate(self, candles, position_side):
        if len(candles) < self.mid + 2:
            return Signal(Action.HOLD, "warming up")
        c = closes(candles)
        ef, em = ema(c, self.fast), ema(c, self.mid)
        if None in (ef[-1], ef[-2], em[-1], em[-2]):
            return Signal(Action.HOLD, "warming up")

        cross_up = ef[-2] <= em[-2] and ef[-1] > em[-1]
        cross_down = ef[-2] >= em[-2] and ef[-1] < em[-1]

        # exits first — opposite cross
        if position_side == "long" and cross_down:
            return Signal(Action.EXIT, "EMA9 crossed below EMA20")
        if position_side == "short" and cross_up:
            return Signal(Action.EXIT, "EMA9 crossed above EMA20")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        # entries — the cross alone decides direction
        if cross_up:
            return Signal(Action.ENTER_LONG, "EMA9 crossed above EMA20")
        if cross_down:
            return Signal(Action.ENTER_SHORT, "EMA9 crossed below EMA20")
        return Signal(Action.HOLD, "no cross")
