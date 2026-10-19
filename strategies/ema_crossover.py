"""Pure EMA 9/20 crossover, stop-and-reverse, on a configurable timeframe
(5m by default).

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
    strategy: { name: ema_crossover, params: { fast: 9, mid: 20, resolution: "5m" } }
    market: { resolution: "5m" }

The resolution param is what makes it possible to run two instances of this
same strategy on different timeframes at once, as combo sub-strategies —
each fetches its own candles at its own resolution independently (see
strategies/combo.py), rather than all sharing the agent's one candle feed:
    strategy:
      name: combo
      params:
        subs:
          - { name: ema_crossover, params: { resolution: "5m" }, tag: ema_5m }
          - { name: ema_crossover, params: { resolution: "15m" }, tag: ema_15m }
"""
from __future__ import annotations

from core.indicators import ema, closes
from strategies.base import BaseStrategy, Signal, Action

name = "ema_crossover"


class Strategy(BaseStrategy):
    def __init__(self, fast=9, mid=20, resolution="5m", **kw):
        super().__init__(fast=fast, mid=mid, resolution=resolution, **kw)
        self.fast, self.mid = fast, mid
        self.required_candles = mid + 2
        self.closed_candles_only = True
        self.preferred_resolution = resolution
        self.supported_resolutions = (resolution,)

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
