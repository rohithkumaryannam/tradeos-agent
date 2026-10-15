"""EMA 9/20/200 crossover with a 200-EMA trend filter.

Trend filter : EMA 200 — only long when price is above it, short when below.
Entry trigger: EMA 9 crossing EMA 20 in the trend direction.
  - LONG  when price > EMA200 and EMA9 crosses UP through EMA20
  - SHORT when price < EMA200 and EMA9 crosses DOWN through EMA20
Exit: the opposite 9/20 cross. Stop-loss / take-profit come from the risk config
(this strategy returns direction only), so sizing and the 1:2 bracket are enforced
by the risk engine.

Needs enough candles for EMA 200 — bump market.lookback to ~300 in config for a
reliable 200-EMA at 5m. Select it in config.yaml:
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

    def generate(self, candles, position_side):
        if len(candles) < self.trend + 2:
            return Signal(Action.HOLD, "warming up")
        c = closes(candles)
        ef, em, et = ema(c, self.fast), ema(c, self.mid), ema(c, self.trend)
        if None in (ef[-1], ef[-2], em[-1], em[-2], et[-1]):
            return Signal(Action.HOLD, "warming up")

        price = c[-1]
        uptrend = price > et[-1]
        downtrend = price < et[-1]
        cross_up = ef[-2] <= em[-2] and ef[-1] > em[-1]
        cross_down = ef[-2] >= em[-2] and ef[-1] < em[-1]

        # exits first — opposite cross
        if position_side == "long" and cross_down:
            return Signal(Action.EXIT, "EMA9 crossed below EMA20")
        if position_side == "short" and cross_up:
            return Signal(Action.EXIT, "EMA9 crossed above EMA20")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        # entries — cross in the direction of the 200-EMA trend
        if uptrend and cross_up:
            return Signal(Action.ENTER_LONG,
                          f"uptrend (>EMA200) + EMA9 crossed above EMA20")
        if downtrend and cross_down:
            return Signal(Action.ENTER_SHORT,
                          f"downtrend (<EMA200) + EMA9 crossed below EMA20")
        return Signal(Action.HOLD, "no cross / trend align")
