"""4H Range Fakeout Reversal — faithful port of a given Pine Script strategy.

Rules (from the supplied Pine source, not a judgment-call reconstruction):
  1. Range = high/low of the first 4-hour NY window (00:00-04:00
     America/New_York by default), extended for the rest of that NY day.
  2. On 5-minute candles: wait for a close fully outside the range (the
     "breakout"), then track the extreme reached while outside, then wait
     for a LATER close back inside the range (the "fakeout confirmation").
  3. Fade the fakeout: short when price closes back below the range high
     (after having broken above); long when it closes back above the range
     low (after having broken below). Stop beyond the breakout excursion
     extreme (the high/low reached while outside); target is reward_risk x
     that stop distance (2:1 by default, matching the Pine default).

State (broke-above/broke-below, excursion extent) is derived fresh each call
from the candle history for the current NY day's post-range bars, rather
than stored on the instance, so a fresh Agent/backtest replay reconstructs
identical state regardless of restarts — same convention as
ny_4h_orb.py.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

from strategies.base import BaseStrategy, Signal, Action

name = "range_fakeout_reversal"

NY = ZoneInfo("America/New_York")


def _ny_day_and_hour(epoch_seconds):
    dt = datetime.fromtimestamp(epoch_seconds, tz=NY)
    return dt.date(), dt.hour


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "5m"
    supported_resolutions = ("5m",)

    def __init__(self, reward_risk=2.0, range_start_hour=0, range_end_hour=4, **kw):
        super().__init__(reward_risk=reward_risk, range_start_hour=range_start_hour,
                          range_end_hour=range_end_hour, **kw)
        self.reward_risk = float(reward_risk)
        self.range_start_hour, self.range_end_hour = range_start_hour, range_end_hour
        self.required_candles = 400

    def _range_for_today(self, candles):
        last_day, last_hour = _ny_day_and_hour(float(candles[-1]["time"]))
        window_highs, window_lows = [], []
        post_range = []
        for c in candles:
            day, hour = _ny_day_and_hour(float(c["time"]))
            if day != last_day:
                continue
            if self.range_start_hour <= hour < self.range_end_hour:
                window_highs.append(float(c["high"]))
                window_lows.append(float(c["low"]))
            elif hour >= self.range_end_hour:
                post_range.append(c)
        if not window_highs:
            return None, None, []
        return max(window_highs), min(window_lows), post_range

    def generate(self, candles, position_side):
        if len(candles) < 10 or position_side is not None:
            return Signal(Action.HOLD, "warming up" if len(candles) < 10 else "in position")

        range_high, range_low, post_range = self._range_for_today(candles)
        if range_high is None:
            return Signal(Action.HOLD, "warming up")
        if not post_range:
            return Signal(Action.HOLD, "NY opening range still forming")

        broke_above = broke_below = False
        excursion_high = excursion_low = None
        signal = None  # (direction, entry, stop) from the LAST transition bar
        for c in post_range:
            close, high, low = float(c["close"]), float(c["high"]), float(c["low"])
            if not broke_above and not broke_below:
                if close > range_high:
                    broke_above, excursion_high = True, high
                elif close < range_low:
                    broke_below, excursion_low = True, low
            elif broke_above:
                excursion_high = max(excursion_high, high)
                if close < range_high:
                    signal = ("short", close, excursion_high, c["time"])
                    broke_above = False
            elif broke_below:
                excursion_low = min(excursion_low, low)
                if close > range_low:
                    signal = ("long", close, excursion_low, c["time"])
                    broke_below = False

        if signal is None or signal[3] != float(candles[-1]["time"]):
            return Signal(Action.HOLD, "no fresh fakeout confirmation")

        direction, entry, extreme, _ = signal
        if direction == "short":
            risk = extreme - entry
            if risk <= 0:
                return Signal(Action.HOLD, "invalid range")
            tp = entry - risk * self.reward_risk
            return Signal(Action.ENTER_SHORT,
                          f"fakeout above range high ({range_high:.4f}) faded back inside",
                          stop_loss=extreme, take_profit=tp)
        risk = entry - extreme
        if risk <= 0:
            return Signal(Action.HOLD, "invalid range")
        tp = entry + risk * self.reward_risk
        return Signal(Action.ENTER_LONG,
                      f"fakeout below range low ({range_low:.4f}) faded back inside",
                      stop_loss=extreme, take_profit=tp)
