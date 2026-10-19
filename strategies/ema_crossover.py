"""Pure EMA 9/20 crossover on 5m candles, stop-and-reverse.

  - EMA9 crosses UP through EMA20   -> LONG
  - EMA9 crosses DOWN through EMA20 -> SHORT
  - Exit on the opposite cross — then the very next cycle (position is now
    flat) sees that same cross and opens the reverse position, so a down-cross
    while long closes the long and flips into a short, and vice versa.

No trend/chop filter — every cross is traded. Stop-loss and take-profit come
from the risk config's stop_loss_pct/take_profit_pct (1%/2% by default — a
1:2 risk:reward), not set per-signal here, so sizing and the bracket are
enforced by the risk engine exactly as for any other strategy.

Select it in config.yaml:
    strategy: { name: ema_crossover, params: { fast: 9, mid: 20 } }
    market: { resolution: "5m" }
"""
from __future__ import annotations

from core.indicators import ema, closes
from strategies.base import BaseStrategy, Signal, Action

name = "ema_crossover"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "5m"
    supported_resolutions = ("5m",)

    def __init__(self, fast=9, mid=20, **kw):
        super().__init__(fast=fast, mid=mid, **kw)
        self.fast, self.mid = fast, mid
        self.required_candles = mid + 2

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")
        c = closes(candles)
        ef, em = ema(c, self.fast), ema(c, self.mid)
        if None in (ef[-1], ef[-2], em[-1], em[-2]):
            return Signal(Action.HOLD, "warming up")

        cross_up = ef[-2] <= em[-2] and ef[-1] > em[-1]
        cross_down = ef[-2] >= em[-2] and ef[-1] < em[-1]

        # exits first — opposite cross, always, even once an exchange
        # bracket is resting (stop-and-reverse means the cross itself is the
        # exit signal; this isn't a strategy whose edge is waiting for its
        # own SL/TP). The reverse entry follows on the next cycle, once this
        # returns to flat.
        if position_side == "long" and cross_down:
            return Signal(Action.EXIT, "EMA9 crossed below EMA20", exit_with_bracket=True)
        if position_side == "short" and cross_up:
            return Signal(Action.EXIT, "EMA9 crossed above EMA20", exit_with_bracket=True)
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        if cross_up:
            return Signal(Action.ENTER_LONG, "EMA9 crossed above EMA20")
        if cross_down:
            return Signal(Action.ENTER_SHORT, "EMA9 crossed below EMA20")
        return Signal(Action.HOLD, "no cross")
