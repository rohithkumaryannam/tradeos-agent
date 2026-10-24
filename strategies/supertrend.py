"""Supertrend — ATR-based trend-following overlay, stop-and-reverse.

One of the most widely used systematic trend indicators (works on any
instrument/timeframe, no curve-fitted parameters beyond ATR period and
multiplier). Computation (standard formula):

  mid = (high + low) / 2
  basic_upper = mid + multiplier * ATR
  basic_lower = mid - multiplier * ATR
  final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i-1] or
                   close[i-1] > final_upper[i-1]) else final_upper[i-1]
  final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i-1] or
                   close[i-1] < final_lower[i-1]) else final_lower[i-1]
  trend flips to up when close crosses above final_upper (while in a
  downtrend); flips to down when close crosses below final_lower (while in
  an uptrend). The supertrend line is final_lower while uptrend, final_upper
  while downtrend.

Entry only on the bar the trend flips (not every bar within a trend) to
avoid chasing. Exit is the opposite flip — this strategy's SL/TP are a
backstop only, matching donchian_breakout.py's convention: the real exit is
the trend reversal, not the bracket.

Select it in config.yaml:
    strategy: { name: supertrend, params: { resolution: "15m" } }
"""
from __future__ import annotations

from core.indicators import atr
from strategies.base import BaseStrategy, Signal, Action

name = "supertrend"


def _supertrend(candles, period, multiplier):
    atr_vals = atr(candles, period)
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    n = len(candles)
    final_upper = [None] * n
    final_lower = [None] * n
    uptrend = [None] * n
    for i in range(n):
        if atr_vals[i] is None:
            continue
        mid = (highs[i] + lows[i]) / 2
        basic_upper = mid + multiplier * atr_vals[i]
        basic_lower = mid - multiplier * atr_vals[i]
        if final_upper[i - 1] is None if i > 0 else True:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            uptrend[i] = closes[i] >= mid
            continue
        final_upper[i] = (basic_upper if (basic_upper < final_upper[i - 1]
                           or closes[i - 1] > final_upper[i - 1]) else final_upper[i - 1])
        final_lower[i] = (basic_lower if (basic_lower > final_lower[i - 1]
                           or closes[i - 1] < final_lower[i - 1]) else final_lower[i - 1])
        prev_up = uptrend[i - 1]
        if prev_up and closes[i] < final_lower[i]:
            uptrend[i] = False
        elif not prev_up and closes[i] > final_upper[i]:
            uptrend[i] = True
        else:
            uptrend[i] = prev_up
    return final_upper, final_lower, uptrend


class Strategy(BaseStrategy):
    closed_candles_only = True

    def __init__(self, resolution="15m", atr_period=10, multiplier=3.0,
                 stop_atr_mult=1.5, fallback_tp_mult=6.0, **kw):
        super().__init__(resolution=resolution, atr_period=atr_period,
                          multiplier=multiplier, stop_atr_mult=stop_atr_mult,
                          fallback_tp_mult=fallback_tp_mult, **kw)
        self.preferred_resolution = resolution
        self.supported_resolutions = (resolution,)
        self.atr_period, self.multiplier = atr_period, multiplier
        self.stop_atr_mult, self.fallback_tp_mult = stop_atr_mult, fallback_tp_mult
        self.required_candles = atr_period * 3 + 10

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        final_upper, final_lower, uptrend = _supertrend(candles, self.atr_period, self.multiplier)
        if uptrend[-1] is None or uptrend[-2] is None:
            return Signal(Action.HOLD, "warming up")

        price = float(candles[-1]["close"])
        flipped_up = uptrend[-1] and not uptrend[-2]
        flipped_down = (not uptrend[-1]) and uptrend[-2]

        if position_side == "long" and flipped_down:
            return Signal(Action.EXIT, "supertrend flipped bearish")
        if position_side == "short" and flipped_up:
            return Signal(Action.EXIT, "supertrend flipped bullish")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        atr_vals = atr(candles, self.atr_period)
        atr_val = atr_vals[-1]
        if atr_val is None:
            return Signal(Action.HOLD, "warming up")
        stop_dist = self.stop_atr_mult * atr_val

        if flipped_up:
            sl = price - stop_dist
            tp = price + stop_dist * self.fallback_tp_mult
            return Signal(Action.ENTER_LONG, "supertrend flipped bullish",
                          stop_loss=sl, take_profit=tp)
        if flipped_down:
            sl = price + stop_dist
            tp = price - stop_dist * self.fallback_tp_mult
            return Signal(Action.ENTER_SHORT, "supertrend flipped bearish",
                          stop_loss=sl, take_profit=tp)
        return Signal(Action.HOLD, "no fresh flip")
