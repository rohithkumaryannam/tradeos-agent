"""EMA 9/20/200 crossover, filtered by trend + ADX, with an ATR-based bracket.

  - EMA200 = trend filter: only long above it, only short below it.
  - EMA9/EMA20 cross = entry trigger, required to hold for `confirm_candles`
    closed candles (reduces single-candle fakeouts).
  - ADX(14) > adx_threshold = chop filter; skips low-conviction ranges.
  - Stop-loss  = entry +/- max(atr_mult * ATR14, distance to the recent
    swing low/high), set once at entry.
  - Take-profit = reward_risk * that stop distance.
  - Exit also fires immediately on EMA9/EMA20 crossing back against the
    position (no confirmation needed for exits).

Ported from a Freqtrade strategy; ADX/ATR/swing math reproduced in
core/indicators.py. Ran on 15m candles originally — set resolution: "15m"
in config.yaml (the strategy also self-heals it on startup).

Select it in config.yaml:
    strategy: { name: ema_crossover, params: { fast: 9, mid: 20, trend: 200 } }
"""
from __future__ import annotations

from core.indicators import ema, adx, atr, closes
from strategies.base import BaseStrategy, Signal, Action

name = "ema_crossover"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "15m"
    supported_resolutions = ("15m",)

    def __init__(self, fast=9, mid=20, trend=200, adx_period=14, adx_threshold=25,
                 confirm_candles=2, atr_period=14, atr_mult=1.0, reward_risk=1.6,
                 swing_lookback=10, **kw):
        super().__init__(fast=fast, mid=mid, trend=trend, adx_period=adx_period,
                          adx_threshold=adx_threshold, confirm_candles=confirm_candles,
                          atr_period=atr_period, atr_mult=atr_mult, reward_risk=reward_risk,
                          swing_lookback=swing_lookback, **kw)
        self.fast, self.mid, self.trend = fast, mid, trend
        self.adx_period, self.adx_threshold = adx_period, adx_threshold
        self.confirm_candles = max(1, confirm_candles)
        self.atr_period, self.atr_mult = atr_period, atr_mult
        self.reward_risk, self.swing_lookback = reward_risk, swing_lookback
        self.required_candles = max(trend, adx_period * 2, atr_period, swing_lookback) \
            + self.confirm_candles + 10

    @staticmethod
    def _cross_held(ef, em, n, direction):
        """True if ema `fast` has been continuously on `direction`'s side of
        `mid` for the last n candles, AND a cross into that side happened
        somewhere within that same window (not just a trend that's been
        running since before the window started)."""
        length = len(ef)
        if length < n + 1:
            return False
        for k in range(length - n, length):
            if ef[k] is None or em[k] is None:
                return False
            on_side = ef[k] > em[k] if direction == "up" else ef[k] < em[k]
            if not on_side:
                return False
        for k in range(length - n, length):
            pf, pm = ef[k - 1], em[k - 1]
            if pf is None or pm is None:
                continue
            crossed = pf <= pm if direction == "up" else pf >= pm
            if crossed:
                return True
        return False

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")
        c = closes(candles)
        ef, em, et = ema(c, self.fast), ema(c, self.mid), ema(c, self.trend)
        adx_vals = adx(candles, self.adx_period)
        if None in (ef[-1], ef[-2], em[-1], em[-2], et[-1], adx_vals[-1]):
            return Signal(Action.HOLD, "warming up")

        cross_up = ef[-2] <= em[-2] and ef[-1] > em[-1]
        cross_down = ef[-2] >= em[-2] and ef[-1] < em[-1]

        # exits first — immediate opposite cross, no confirmation required
        if position_side == "long" and cross_down:
            return Signal(Action.EXIT, "EMA9 crossed below EMA20")
        if position_side == "short" and cross_up:
            return Signal(Action.EXIT, "EMA9 crossed above EMA20")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        price = c[-1]
        uptrend, downtrend = price > et[-1], price < et[-1]
        trending = adx_vals[-1] > self.adx_threshold
        confirmed_bull = self._cross_held(ef, em, self.confirm_candles, "up")
        confirmed_bear = self._cross_held(ef, em, self.confirm_candles, "down")

        atr_vals = atr(candles, self.atr_period)
        atr_val = atr_vals[-1]
        if atr_val is None:
            return Signal(Action.HOLD, "warming up")
        window = candles[-self.swing_lookback:]
        swing_low = min(float(c_["low"]) for c_ in window)
        swing_high = max(float(c_["high"]) for c_ in window)

        if uptrend and confirmed_bull and trending:
            stop_dist = max(self.atr_mult * atr_val, price - swing_low)
            sl, tp = price - stop_dist, price + stop_dist * self.reward_risk
            return Signal(Action.ENTER_LONG,
                          f"EMA9/20 bull cross held {self.confirm_candles}c, "
                          f"price>EMA200, ADX {adx_vals[-1]:.1f}",
                          stop_loss=sl, take_profit=tp)
        if downtrend and confirmed_bear and trending:
            stop_dist = max(self.atr_mult * atr_val, swing_high - price)
            sl, tp = price + stop_dist, price - stop_dist * self.reward_risk
            return Signal(Action.ENTER_SHORT,
                          f"EMA9/20 bear cross held {self.confirm_candles}c, "
                          f"price<EMA200, ADX {adx_vals[-1]:.1f}",
                          stop_loss=sl, take_profit=tp)
        return Signal(Action.HOLD, "no confirmed trend-aligned cross")
